# -*- coding: utf-8 -*-
"""Pass B / batch_10 writer (annotator B). Writes 5 rows then self-checks."""
from pathlib import Path

import anno_lib as A

OUT = Path(__file__).resolve().parent / "batch_10.jsonl"
ANNO = "ai-draft:zcode:glm-5.2:pass-b"

# ---------------- case 50: pandas-dev__pandas-50773 (annotated) ----------------
CID50 = "296308d848cd59d7d1e29a0d624bcb12dcb75ab531d5e6c1546afc17a51ea537"
A0 = "m0008:call:call_eE9sWQxBylVaPnDjHXllUEXm"
R0 = "m0009:result:call_eE9sWQxBylVaPnDjHXllUEXm"
D1 = "m0010:message"
A1 = "m0010:call:call_DfNmzrdc93tpDlHUtP3kdH1w"
R1 = "m0011:result:call_DfNmzrdc93tpDlHUtP3kdH1w"
D2 = "m0014:message"
A2 = "m0014:call:call_ubDs5AKZ5hPWXuIJ0BK7NX9X"
R2 = "m0015:result:call_ubDs5AKZ5hPWXuIJ0BK7NX9X"
D3 = "m0016:message"
A3 = "m0016:call:call_ZOlhoEvryM7Qri9TF3cWSuUc"
R3 = "m0017:result:call_ZOlhoEvryM7Qri9TF3cWSuUc"
D4 = "m0018:message"
A4 = "m0018:call:call_4WpqkPPpFSBevVWTW8kfDwEC"
R4 = "m0019:result:call_4WpqkPPpFSBevVWTW8kfDwEC"
D5 = "m0020:message"
A5 = "m0020:call:call_cjxF2johgAG1dtQHjrOZp8dc"
R5 = "m0021:result:call_cjxF2johgAG1dtQHjrOZp8dc"
D6 = "m0022:message"
A6 = "m0022:call:call_zsC1OX5c1LWteqnzImJkNr8M"
R6 = "m0023:result:call_zsC1OX5c1LWteqnzImJkNr8M"

EPISODE50 = {
    "scope": "task_level_failure_episode",
    "failure_family": "inferred_frequency_mismatch",
    "recoverability": "R0",
    "error_signature": "ValueError: Inferred frequency None from passed values does not conform to passed frequency D",
    "diagnostic_evidence": (
        "m0008:call 运行刚创建的 reproduce_error.py，m0009:result 给出完整 traceback："
        "DatetimeArray._from_sequence_not_strict 调用 _validate_frequency 时在 datetimelike.py 第 1914 行抛出 "
        "ValueError: Inferred frequency None from passed values does not conform to passed frequency D（exit code 1），"
        "任务要修的 bug（非 nano 单位配合 freq='D' 报错）首次被直接复现，构成任务级初始失败。"
        "m0010:message 将根因指向 _validate_frequency，m0011:result 查看 1900-1940 行确认 "
        "np.array_equal 比较失败即 raise 的路径：'s' 单位的 asi8 与按 'D' 重建的数组不一致。"
    ),
    "recovery_sequence": (
        "1) m0010:call 查看 datetimelike.py 1900-1940 行定位 raise 位置（m0011:result）；"
        "2) m0014:call 首次 str_replace 因 old_str 'raise ValueError(' 在文件中有 11 处匹配被拒（m0015:result）；"
        "3) m0016:call 扩展上下文重试，old_str 与文件实际文本不逐字一致仍被拒（m0017:result）；"
        "4) m0018:call 重读 1900-1920 行获取逐字文本（m0019:result）；"
        "5) m0020:call 以正确上下文写入补丁——当推断间隔恰为 86400 秒且 freq='D' 时直接 return（m0021:result 确认已编辑）；"
        "6) m0022:call 复跑 reproduce_error.py，m0023:result 显示 exit code 0 且无任何 traceback。"
    ),
    "resolution_evidence": (
        "m0023:result 显示同一复现脚本 python3 reproduce_error.py 以 [Command finished with exit code 0] 完成、"
        "输出中无任何异常，任务级失败（ValueError）被直接观察到已消除。"
    ),
    "anchor_source_event_id": A0,
    "initial_action_source_event_id": A0,
    "initial_result_source_event_ids": [R0],
    "repair_steps": [
        {
            "step_id": "locate-raise-site",
            "decision_source_event_ids": [D1],
            "action_source_event_id": A1,
            "result_source_event_ids": [R1],
            "outcome": "intermediate_failure",
            "semantic_change": "查看 datetimelike.py 第 1900-1940 行，确认 _validate_frequency 中推断频率与传入 freq 不一致时 raise ValueError 的具体位置；漏洞本身尚未被处理。",
        },
        {
            "step_id": "first-edit-attempt",
            "decision_source_event_ids": [D2],
            "action_source_event_id": A2,
            "result_source_event_ids": [R2],
            "outcome": "intermediate_failure",
            "semantic_change": "首次 str_replace 尝试在 raise ValueError( 前插入 86400 秒/'D' 兼容检查，因 old_str 在文件中出现 11 处不唯一而被拒绝，补丁未写入。",
        },
        {
            "step_id": "second-edit-attempt",
            "decision_source_event_ids": [D3],
            "action_source_event_id": A3,
            "result_source_event_ids": [R3],
            "outcome": "intermediate_failure",
            "semantic_change": "第二次 str_replace 携带 f-string 错误消息上下文重试，old_str 仍与文件实际文本不逐字一致（缩进/换行差异）而被拒绝。",
        },
        {
            "step_id": "read-exact-text",
            "decision_source_event_ids": [D4],
            "action_source_event_id": A4,
            "result_source_event_ids": [R4],
            "outcome": "intermediate_failure",
            "semantic_change": "重新精确查看 1900-1920 行拿到 raise 语句的逐字文本，为最终替换做准备；失败仍未修复。",
        },
        {
            "step_id": "apply-compat-patch",
            "decision_source_event_ids": [D5],
            "action_source_event_id": A5,
            "result_source_event_ids": [R5],
            "outcome": "intermediate_failure",
            "semantic_change": "以正确缩进上下文执行 str_replace 成功：_validate_frequency 中加入推断间隔 86400 秒且 freq.freqstr=='D' 时直接 return 的兼容分支，补丁写入 /workspace 副本，但尚未复跑验证。",
        },
        {
            "step_id": "reverify-repro",
            "decision_source_event_ids": [D6],
            "action_source_event_id": A6,
            "result_source_event_ids": [R6],
            "outcome": "resolved",
            "semantic_change": "复跑 reproduce_error.py，无任何 traceback 且 exit code 0，非 nano（'s'）单位配合 freq='D' 不再抛 ValueError，任务级修复在运行时被直接确认。",
        },
    ],
    "resolution_source_event_ids": [R6],
    "required_core_source_event_ids": [A0, R0, A5, R5, A6, R6],
    "optional_support_source_event_ids": [R1, R2, R3, R4],
    "chain_policy": {
        "relevant_evidence_ids": [A0, R0, D1, A1, R1, D2, A2, R2, D3, A3, R3, D4, A4, R4, D5, A5, R5, D6, A6, R6],
        "alternative_evidence_sets": [
            [A0, R0, A5, R5, A6, R6],
            [A0, R0, A5, R6],
        ],
        "causal_paths": [
            {
                "evidence_ids": [A0, R0, A5, R5, A6, R6],
                "constraints": [[A0, R0], [R0, A5], [A5, R5], [R5, A6], [A6, R6]],
            },
            {
                "evidence_ids": [A0, R0, A5, R6],
                "constraints": [[A0, R0], [R0, A5], [A5, R6]],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "relevant_evidence_ids": [A2, R2, A3, R3, D4, A4, A5, R5, A6, R6],
            "alternative_evidence_sets": [
                [A3, R3, A5, R5, R6],
                [A2, R2, A5, A6, R6],
            ],
            "causal_paths": [
                {
                    "evidence_ids": [A3, R3, A5, R5, R6],
                    "constraints": [[A3, R3], [R3, A5], [A5, R5], [R5, R6]],
                },
                {
                    "evidence_ids": [A2, R2, A5, A6, R6],
                    "constraints": [[A2, R2], [R2, A5], [A5, A6], [A6, R6]],
                },
            ],
        },
        "interactive_reacquisition": {
            "relevant_evidence_ids": [A0, R0, R5, A6, R6],
            "alternative_evidence_sets": [
                [R5, A6, R6],
                [A0, A6, R6],
            ],
            "causal_paths": [
                {
                    "evidence_ids": [R5, A6, R6],
                    "constraints": [[R5, A6], [A6, R6]],
                },
                {
                    "evidence_ids": [A0, A6, R6],
                    "constraints": [[A0, A6], [A6, R6]],
                },
            ],
        },
    },
}

# ---------------- case 90: dask__dask-7973 (annotated) ----------------
CID90 = "46f238e937bbfd29a4bca67825041c22e99715febd5259b139cfad526623b62d"
B0 = "m0020:call:call_B6xRniRe9qhwm17O26TcQxrL"
S0 = "m0021:result:call_B6xRniRe9qhwm17O26TcQxrL"
d1 = "m0022:message"
a1 = "m0022:call:call_jFo6vdxPtHIGZZjc5mQklbFD"
r1 = "m0023:result:call_jFo6vdxPtHIGZZjc5mQklbFD"
a2 = "m0024:call:call_jrDVavqEvgucfqwoULquVHKD"
r2 = "m0025:result:call_jrDVavqEvgucfqwoULquVHKD"
d3 = "m0026:message"
a3 = "m0026:call:call_GgjBmvjQ8ouWdQ7pEXXzrjU5"
r3 = "m0027:result:call_GgjBmvjQ8ouWdQ7pEXXzrjU5"
a4 = "m0028:call:call_7yFaX9csi5WazsznQNMckfbn"
r4 = "m0029:result:call_7yFaX9csi5WazsznQNMckfbn"
b0 = "m0014:call:call_kwvPcNWGeKzjedv5seSn0rw3"
b1 = "m0015:result:call_kwvPcNWGeKzjedv5seSn0rw3"
e0 = "m0018:call:call_do7CoyjgyOTwgUuBR0MIjnJF"
e1 = "m0019:result:call_do7CoyjgyOTwgUuBR0MIjnJF"

EPISODE90 = {
    "scope": "task_level_failure_episode",
    "failure_family": "wrong_api_usage",
    "recoverability": "R0",
    "error_signature": "AttributeError: 'tuple' object has no attribute 'annotations'",
    "diagnostic_evidence": (
        "agent 在 m0018:call 给 dask/dot.py 的 to_graphviz 加入 tooltip 逻辑（对 dsk.items() 的值 v 直接取 v.annotations，见 m0019:result）。"
        "m0020:call 复跑 test_tooltips.py，m0021:result 显示 y.visualize 在 to_graphviz 第 165 行抛出 "
        "AttributeError: 'tuple' object has no attribute 'annotations'（exit code 1），可视化整体被新改动弄坏，构成任务级初始失败；"
        "基线 b0/b1（m0014:call→m0015:result，exit 0）证明改动前脚本可正常运行。"
        "m0022:message 诊断：v 有时是 tuple 而非 Layer 对象，直接访问 .annotations 属于错误的 API 假设。"
    ),
    "recovery_sequence": (
        "1) 依 m0022:message 诊断，m0022:call 用 str_replace 给 annotations 访问加 hasattr(v,'annotations') 守卫并写入 dot.py（m0023:result）；"
        "2) m0024:call 复跑脚本，m0025:result 触发 IndentationError: expected an indented block（exit 1），守卫块缩进层级错误；"
        "3) m0026:message 判断为缩进问题，m0026:call 重写该块为正确缩进（m0027:result）；"
        "4) m0028:call 最终复跑 test_tooltips.py，m0029:result 无 traceback、exit code 0。"
    ),
    "resolution_evidence": (
        "m0029:result 显示 test_tooltips.py（含 y.visualize(filename='dask_graph', format='svg')）以 "
        "[Command finished with exit code 0] 完成且无任何异常输出，直接观察确认 tooltip 改动后 graphviz 可视化恢复可用。"
    ),
    "anchor_source_event_id": B0,
    "initial_action_source_event_id": B0,
    "initial_result_source_event_ids": [S0],
    "repair_steps": [
        {
            "step_id": "guard-attr-access",
            "decision_source_event_ids": [d1],
            "action_source_event_id": a1,
            "result_source_event_ids": [r1],
            "outcome": "intermediate_failure",
            "semantic_change": "在 to_graphviz 的 tooltip 逻辑外加 hasattr(v,'annotations') or hasattr(v,'collection_annotations') 守卫（str_replace 写入成功），但替换文本缩进层级不对，语法错误已埋入。",
        },
        {
            "step_id": "reverify-guard",
            "decision_source_event_ids": [],
            "action_source_event_id": a2,
            "result_source_event_ids": [r2],
            "outcome": "intermediate_failure",
            "semantic_change": "复跑 test_tooltips.py 触发 IndentationError: expected an indented block（exit code 1），AttributeError 未解决且新增语法错误。",
        },
        {
            "step_id": "fix-indentation",
            "decision_source_event_ids": [d3],
            "action_source_event_id": a3,
            "result_source_event_ids": [r3],
            "outcome": "intermediate_failure",
            "semantic_change": "按 m0026:message 的诊断将 if 块内 layer_info/tooltip_text 语句改为与外层循环一致的缩进并写入 dot.py（m0027:result），等待最终验证。",
        },
        {
            "step_id": "final-reverify",
            "decision_source_event_ids": [],
            "action_source_event_id": a4,
            "result_source_event_ids": [r4],
            "outcome": "resolved",
            "semantic_change": "再次复跑 test_tooltips.py，无任何 traceback 且 exit code 0，tooltip 增强后的 visualize 正常工作，任务级失败被直接观察到已消除。",
        },
    ],
    "resolution_source_event_ids": [r4],
    "required_core_source_event_ids": [B0, S0, a1, r2, a3, r4],
    "optional_support_source_event_ids": [b0, b1, e0, e1, r1, r3, a4],
    "chain_policy": {
        "relevant_evidence_ids": [B0, S0, b0, b1, e0, e1, d1, a1, r1, a2, r2, d3, a3, r3, a4, r4],
        "alternative_evidence_sets": [
            [B0, S0, a1, r2, a3, r4],
            [B0, S0, a3, r4],
        ],
        "causal_paths": [
            {
                "evidence_ids": [B0, S0, a1, r2, a3, r4],
                "constraints": [[B0, S0], [S0, a1], [a1, r2], [r2, a3], [a3, r4]],
            },
            {
                "evidence_ids": [B0, S0, a3, r4],
                "constraints": [[B0, S0], [S0, a3], [a3, r4]],
            },
        ],
    },
    "query_policies": {
        "audit_recovery": {
            "relevant_evidence_ids": [a1, r1, a2, r2, d3, a3, r3, a4, r4],
            "alternative_evidence_sets": [
                [a1, r2, a3, r4],
                [a1, a3, r3, r4],
            ],
            "causal_paths": [
                {
                    "evidence_ids": [a1, r2, a3, r4],
                    "constraints": [[a1, r2], [r2, a3], [a3, r4]],
                },
                {
                    "evidence_ids": [a1, a3, r3, r4],
                    "constraints": [[a1, a3], [a3, r3], [r3, r4]],
                },
            ],
        },
        "interactive_reacquisition": {
            "relevant_evidence_ids": [B0, S0, e1, r3, a4, r4],
            "alternative_evidence_sets": [
                [r3, a4, r4],
                [B0, a4, r4],
            ],
            "causal_paths": [
                {
                    "evidence_ids": [r3, a4, r4],
                    "constraints": [[r3, a4], [a4, r4]],
                },
                {
                    "evidence_ids": [B0, a4, r4],
                    "constraints": [[B0, a4], [a4, r4]],
                },
            ],
        },
    },
}

REJECTIONS = [
    (
        "140c1e0629907a5c515bd4c121171dbe49f0cabc6d460bd0be232e2b19ef1f1d",
        "轨迹仅 9 个事件且停留在仓库浏览阶段即截断（最后一个 tool_call m0008 无对应结果）："
        "唯一的错误结果 m0005 是 view_range 误用于目录的探索期局部错误，不构成任务级失败锚点；"
        "没有任何修复阶段，也未观察到最终成功，无法给出完整任务级 episode。",
    ),
    (
        "afb665a11a23067ad3bd6394b3948b28a6f84504ace3c33690ca0a8085f99cb2",
        "Crafter 开放式游戏轨迹（372 事件）：代理反复陷入持续性无效行为（t0044-t0059 连续 Move South 被 table 阻挡、"
        "t0070-t0081 及 t0086 起无镐反复 Do 挖 diamond 无效），均为连续性行为失败，无法定位单一任务级失败动作作为锚点；"
        "结尾进入 Sleep/Noop 循环且僵尸逼近（t0178-t0185）处截断，未观察到任何任务级最终成功，给不出完整 episode。",
    ),
    (
        "82076a0808c28f5bf34508e3ad69f0b93f43490334f8614aabd2a2748ea5d677",
        "VBox 报错在整段前缀中从未被任何 tool_call 直接复现：m0038:message 提到的 traceback 只存在于 PR 描述文本，"
        "不是事件观察；前缀中的错误结果均为探索期局部错误（m0003/m0007/m0011/m0019/m0021 的 view_range/路径错误）"
        "与修复中途的 str_replace 局部失败（m0039/m0041），皆非任务级初始失败。"
        "虽然 m0047:result 复跑脚本观察到 exit code 0，但缺少初始失败的直接观察，error_signature 与初始结果约束无法满足，"
        "不能构成完整 episode。",
    ),
]


def main() -> None:
    A.write_row(OUT, CID50, annotator=ANNO, status="annotated", episode=EPISODE50)
    A.write_row(OUT, CID90, annotator=ANNO, status="annotated", episode=EPISODE90)
    for cid, reason in REJECTIONS:
        A.write_row(OUT, cid, annotator=ANNO, status="rejected", rejection_reason=reason)
    errs = A.check_file(OUT)
    for e in errs:
        print("ERROR", e)
    print("checked", OUT, "-", len(errs), "errors")


if __name__ == "__main__":
    main()
