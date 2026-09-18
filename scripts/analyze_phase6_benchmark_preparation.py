"""只读检查公开测试任务准备结果和文件指纹，不下载数据或调用模型。"""

from __future__ import annotations

from tracegraph.plain_cli import PlainArgumentParser, run_cli

import json
from pathlib import Path

from tracegraph.phase6_benchmarks import sha256_file


def main() -> int:
    parser = PlainArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("outputs/phase6/public_tasks_preparation_v2"),
        help="要只读检查的公开测试任务准备结果目录",
    )
    args = parser.parse_args()
    root = args.input.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    hashes = json.loads((root / "hashes.json").read_text(encoding="utf-8"))
    report = json.loads((root / "preparation_report.json").read_text(encoding="utf-8"))
    failures = sorted(
        name
        for name, expected in hashes.items()
        if not (root / name).is_file() or sha256_file(root / name) != expected
    )
    result = {
        "run_id": manifest["run_id"],
        "status": report["status"],
        "provider_requests_made": report["provider_requests_made"],
        "source_statuses": {
            source["benchmark_id"]: source["status"] for source in report["sources"]
        },
        "budget": report["budget"],
        "artifact_hashes_valid": not failures,
        "hash_failures": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
