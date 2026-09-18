"""Zero-call, post-hoc sensitivity of error-signature matching policies."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from tracegraph.benchmark.compression_audit.artifacts import load_dataset
from tracegraph.benchmark.compression_audit.development_experiment import write_json, write_rows
from tracegraph.benchmark.compression_audit.io import file_sha256


def _normalize(value: str) -> str:
    return " ".join(value.split()).rstrip(".。")


def _without_absolute_python_path_suffix(value: str) -> str:
    return re.sub(r"\s+\(/[^()]+\.py\)$", "", _normalize(value))


def _hard_if_signature(score: dict, signature_pass: bool, status: str) -> bool:
    other_strict = all(
        item.get("pass")
        for name, item in score["strict_values"].items()
        if name != "error_signature"
    )
    return bool(
        status == "complete"
        and score.get("structured_output_valid")
        and other_strict
        and signature_pass
        and score.get("answer_citation_pass")
        and score.get("fact_scope_correct")
        and score.get("safety_pass")
    )


def analyze(dataset: Path, run: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("use a new signature-sensitivity output directory")
    _, _, gold_rows = load_dataset(dataset)
    gold = {row.prefix_id: row for row in gold_rows}
    episodes = [json.loads(line) for line in (run / "episodes.jsonl").read_text(
        encoding="utf-8").splitlines()]
    target = [row for row in episodes if row.get("phase") == "diagnostic"
              and row.get("method_id") == "oracle_evidence_only"]
    rows = []
    for row in target:
        expected = _normalize(str(gold[row["prefix_id"]].error_signature))
        value = _normalize(str(row["score"]["strict_values"]["error_signature"]["value"]))
        policies = {
            "current_one_way_gold_substring": bool(expected) and expected in value,
            "drop_absolute_python_path_suffix_then_one_way": (
                bool(_without_absolute_python_path_suffix(expected))
                and _without_absolute_python_path_suffix(expected) in value
            ),
            "bidirectional_normalized_containment": (
                bool(expected) and (expected in value or value in expected)
            ),
        }
        rows.append({
            "prefix_id": row["prefix_id"],
            "expected_signature": expected,
            "answer_signature": value,
            "source_hard_pass": row["score"]["hard_pass"],
            "signature_policy_pass": policies,
            "counterfactual_hard_pass": {
                name: _hard_if_signature(row["score"], passed, row["status"])
                for name, passed in policies.items()
            },
        })
    totals = {
        policy: sum(item["counterfactual_hard_pass"][policy] for item in rows)
        for policy in rows[0]["counterfactual_hard_pass"]
    }
    report = {
        "schema_version": "failure_episode_signature_sensitivity_v1",
        "interpretation": "post_hoc_zero_call_metric_sensitivity",
        "formal_comparison_eligible": False,
        "development_only": True,
        "independent_validation": False,
        "human_validated": False,
        "new_provider_requests": 0,
        "episode_count": len(rows),
        "hard_pass_by_policy": totals,
        "gate_threshold": 3,
        "gate_pass_by_policy": {name: value >= 3 for name, value in totals.items()},
        "warning": (
            "These policies were evaluated after observing answers. They are sensitivity "
            "analyses, not replacements for the frozen result. A blinded review must choose "
            "the construct before any gold or scorer migration."
        ),
        "source_hashes": {
            "dataset_manifest_sha256": file_sha256(dataset / "manifest.json"),
            "report_sha256": file_sha256(run / "report.json"),
            "episodes_sha256": file_sha256(run / "episodes.jsonl"),
        },
    }
    output.mkdir(parents=True)
    write_json(output / "report.json", report)
    write_rows(output / "episodes.jsonl", rows)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.dataset, args.run, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
