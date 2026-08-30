"""只读检查第六阶段本地结果，并输出简短结论。"""

from __future__ import annotations

from tracegraph.plain_cli import PlainArgumentParser, run_cli

import hashlib
import json
from pathlib import Path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = PlainArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("outputs/phase6/e1_controlled_v1"),
        help="要检查的第六阶段结果目录",
    )
    args = parser.parse_args()
    root = args.input
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    hashes = json.loads((root / "hashes.json").read_text(encoding="utf-8"))
    metrics = json.loads((root / "metrics.json").read_text(encoding="utf-8"))
    gates = json.loads((root / "gate_report.json").read_text(encoding="utf-8"))
    failures = [
        name
        for name, expected in manifest["artifact_hashes"].items()
        if not (root / name).is_file() or _sha256(root / name) != expected
    ]
    report = {
        "run_id": manifest["run_id"],
        "artifact_hashes_valid": not failures,
        "hash_failures": failures,
        "protected_unchanged": hashes["protected_unchanged"],
        "gates": manifest["gates"],
        "provider_requests": manifest["provider_requests"],
        "m5": metrics["manager_summaries"].get(
            "M5_lifecycle_causal_reactivation"
        ),
        "failed_gate_criteria": [
            {
                "gate": gate_name,
                "metric": item["metric"],
                "observed": item["observed"],
            }
            for gate_name, gate in gates.items()
            for item in gate.get("criteria", ())
            if not item.get("passed")
        ],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not failures and hashes["protected_unchanged"] else 1


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
