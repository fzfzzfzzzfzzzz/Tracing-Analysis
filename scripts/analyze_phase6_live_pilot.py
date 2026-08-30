"""只读检查第六阶段真实模型试跑的文件、费用和最终结论。"""

from __future__ import annotations

from tracegraph.plain_cli import PlainArgumentParser, run_cli

import json
from pathlib import Path

from tracegraph.phase6_live import file_sha256, load_jsonl


def main() -> int:
    parser = PlainArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("outputs/phase6/e3_qwen38_27b_pilot_v2"),
        help="要检查的真实模型试跑结果目录",
    )
    args = parser.parse_args()
    root = args.input.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    hashes = json.loads((root / "hashes.json").read_text(encoding="utf-8"))
    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    gates = json.loads((root / "gate_report.json").read_text(encoding="utf-8"))
    failures = [
        name
        for name, expected in hashes.items()
        if not (root / name).is_file() or file_sha256(root / name) != expected
    ]
    if file_sha256(root / "hashes.json") != manifest["hashes_sha256"]:
        failures.append("hashes.json")
    ledger = load_jsonl(root / "ledger.jsonl")
    report = {
        "run_id": manifest["run_id"],
        "selected_model": manifest["selected_model"],
        "fallback_used": manifest["fallback_used"],
        "trial_count": manifest["trial_count"],
        "provider_requests": manifest["provider_requests"],
        "provider_total_cost_cny": manifest["provider_total_cost_cny"],
        "ledger_rows": len(ledger),
        "artifact_hashes_valid": not failures,
        "hash_failures": failures,
        "gate_decision": gates["decision"],
        "gate_criteria": gates["criteria"],
        "method_summaries": metrics["methods"],
        "controlled_synthetic": manifest["controlled_synthetic"],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
