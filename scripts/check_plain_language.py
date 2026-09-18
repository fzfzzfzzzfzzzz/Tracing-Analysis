"""检查文档是否仍有未解释的内部叫法或失效链接。"""

from __future__ import annotations

from tracegraph.plain_cli import PlainArgumentParser, run_cli

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote


FORBIDDEN_TERMS: tuple[tuple[str, str], ...] = (
    (r"\bTraceGraph\b", "工具使用记录关系图"),
    (r"\bEventGraph\b", "工具使用记录之间的关系"),
    (r"\bGDSC\b", "第四阶段的记录整理办法"),
    (r"\blifecycle\b", "判断记录现在是否还需要"),
    (r"\breactivation\b", "找回旧记录"),
    (r"\bprojection\b", "整理后交给模型的内容"),
    (r"\bprefix(?:es)?\b", "截至当时已有的记录"),
    (r"\bforks?\b", "不同后续"),
    (r"\bgold\b", "事先规定的标准答案"),
    (r"\bmanagers?\b", "记录整理办法"),
    (r"\barchives?\b", "单独保存的旧记录"),
    (r"\bhandles?\b", "存放编号"),
    (r"\bcausal closure\b", "把相关的前因后果一起找全"),
    (r"\bprotocol closure\b", "把工具调用和返回结果成对补齐"),
    (r"\bpinned\b", "必须保留"),
    (r"\bdormant\b", "暂时不用，单独保存"),
    (r"\bsuperseded\b", "已被新信息替代"),
    (r"\bephemeral\b", "用完即可丢弃"),
    (r"\buncertain\b", "拿不准，先保留"),
    (r"\bablations?\b", "去掉一个功能再比较"),
    (r"\bgates?\b", "继续或停止条件"),
    (r"\bNo-Go\b", "停止"),
    (r"\bproviders?\b", "模型服务商"),
    (r"\bserialized\b", "真正拼成完整请求后的"),
    (r"\bsubgraphs?\b", "一小段互相关联的记录"),
    (r"\bbaselines?\b", "用来比较的办法"),
    (r"\bprovenance\b", "来源记录"),
    (r"\bmanifests?\b", "文件清单"),
    (r"\bbootstrap\b", "反复抽样得到的估计范围"),
    (r"\bHolm\b", "多项比较校正"),
    (r"\bhash(?:es)?\b", "文件指纹"),
    (r"\bPhase\s*[1-6](?:\.\d+)?\b", "第几阶段"),
    (r"生命周期", "记录现在是否还需要"),
    (r"主动休眠", "暂时收起旧记录"),
    (r"重新激活", "找回旧记录"),
    (r"因果闭包", "把相关的前因后果一起找全"),
    (r"协议闭包", "把工具调用和返回结果成对补齐"),
    (r"活性分析", "判断记录现在是否还有用"),
    (r"投影", "整理后交给模型的内容"),
    (r"子图", "一小段互相关联的记录"),
    (r"前缀", "截至当时已有的记录"),
    (r"归档", "单独保存的旧记录"),
    (r"门禁", "继续或停止条件"),
    (r"消融", "去掉一个功能再比较"),
    (r"基线", "用来比较的办法"),
    (r"序列化", "真正拼成完整请求"),
    (r"上下文", "交给模型的内容"),
    (r"预注册", "实验开始前定下的规则"),
    (r"伪标注", "由模型暂时给出的分类"),
    (r"状态机", "按固定规则逐步更新状态的程序"),
    (r"哈希", "文件指纹"),
)

OLD_DOCUMENT_STEMS: tuple[str, ...] = (
    "第二阶段实验结论",
    "第二阶段修改计划",
    "第三阶段修改计划",
    "第四阶段修改计划",
    "第五阶段修改计划",
    "第五阶段增量修改计划",
    "工具调用建图_生命周期压缩调研报告",
    "工具调用建图_主动休眠与重新激活_研究与Codex执行规格_v2",
    "导师版实验进展与卡点报告",
    "ARCHITECTURE",
    "CLAIM_EVIDENCE_MATRIX",
    "DATA_FORMAT",
    "EXPERIMENTS",
    "FORMAL_MATRIX",
    "GDSC_PREREGISTRATION",
    "GLM_PILOT",
    "GLM47_FLASH_RESULTS",
    "LIFECYCLE_ANNOTATION",
    "LIFECYCLE_DIAGNOSTICS",
    "METRICS",
    "PHASE3_RESULTS",
    "PHASE4_GDSC_RESULTS",
    "PHASE4_RESULTS",
    "PHASE5_CHECKPOINT",
    "PHASE5_RESULTS",
    "PHASE51_LIFECYCLE_EVIDENCE_RESULTS",
    "PHASE52_IMPLEMENTATION",
    "PHASE52_QWEN37PLUS_PILOT_RESULTS",
    "PHASE52_RELATION_FIRST_DEBUG_RESULTS",
    "PHASE6_CLAIM_EVIDENCE_MATRIX",
    "PHASE6_CONTROLLED_RESULTS",
    "PHASE6_IMPLEMENTATION",
    "PHASE6_PREREGISTRATION",
    "REQUIREMENTS_TRACEABILITY",
    "STAGE1_RESULTS",
    "STRONG_BASELINES",
    "TAU3_INTEGRATION",
    "TOKEN_ACCOUNTING",
    "VALIDATION",
)
OLD_FILENAMES = tuple(f"{stem}.md" for stem in OLD_DOCUMENT_STEMS)

INLINE_CODE = re.compile(r"`[^`]*`")
LINK_DESTINATION = re.compile(r"\]\(([^)]+)\)")
HTML_COMMENT = re.compile(r"<!--.*?-->")


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    message: str


def _visible_lines(path: Path) -> list[tuple[int, str]]:
    visible: list[tuple[int, str]] = []
    inside_fence = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if line.lstrip().startswith("```"):
            inside_fence = not inside_fence
            continue
        if inside_fence:
            continue
        line = INLINE_CODE.sub("", line)
        line = LINK_DESTINATION.sub("]()", line)
        line = HTML_COMMENT.sub("", line)
        visible.append((number, line))
    return visible


def check_words(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    for number, line in _visible_lines(path):
        for pattern, replacement in FORBIDDEN_TERMS:
            if re.search(pattern, line, flags=re.IGNORECASE):
                findings.append(
                    Finding(path, number, f"请改用“{replacement}”：{pattern}")
                )
        for old_name in OLD_FILENAMES:
            if old_name in line:
                findings.append(Finding(path, number, f"仍引用旧文件名：{old_name}"))
    return findings


def check_links(path: Path) -> list[Finding]:
    findings: list[Finding] = []
    text = path.read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        for match in LINK_DESTINATION.finditer(line):
            raw = match.group(1).strip().strip("<>")
            if not raw or raw.startswith(("http://", "https://", "mailto:", "#")):
                continue
            target_text = unquote(raw.split("#", 1)[0])
            target = (path.parent / target_text).resolve()
            if not target.exists():
                findings.append(Finding(path, number, f"链接目标不存在：{raw}"))
    return findings


def markdown_files(root: Path) -> list[Path]:
    blocked = {
        ".git",
        ".venv",
        ".agents",
        ".pytest_cache",
        ".ruff_cache",
        "vendor",
        "outputs",
        "artifacts",
    }
    return sorted(
        path
        for path in root.rglob("*.md")
        if not any(part in blocked for part in path.relative_to(root).parts)
    )


def main() -> int:
    parser = PlainArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."), help="项目根目录")
    args = parser.parse_args()
    root = args.root.resolve()
    findings: list[Finding] = []
    files = markdown_files(root)
    for path in files:
        findings.extend(check_words(path))
        findings.extend(check_links(path))
    if findings:
        for item in findings:
            print(f"{item.path.relative_to(root)}:{item.line}: {item.message}")
        print(f"发现 {len(findings)} 个需要修改的地方。")
        return 1
    print(f"检查通过：{len(files)} 份文档没有未解释的内部叫法或失效链接。")
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
