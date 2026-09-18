"""Zero-call sensitivity analysis for the task-level failure-episode contract.

This script never mutates the source run and never contacts a model provider.  It
rebuilds rubrics from the bound development dataset, applies the current local
scoring contract to saved answers/judgments, and emits a post-hoc report whose
results are explicitly ineligible for formal comparison.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from tracegraph.benchmark.compression_audit.artifacts import load_dataset
from tracegraph.benchmark.compression_audit.development_scoring import (
    RUBRIC_VERSION,
    SCORING_REVISION,
    make_rubric,
    score_submission,
)
from tracegraph.benchmark.compression_audit.io import file_sha256, load_jsonl


def _counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "n": len(rows),
        "source_hard_pass": sum(bool(row["source_score"].get("hard_pass")) for row in rows),
        "sensitivity_hard_pass": sum(
            bool(row["sensitivity_score"].get("hard_pass")) for row in rows
        ),
        "source_audit_pass": sum(bool(row["source_score"].get("audit_pass")) for row in rows),
        "sensitivity_audit_pass": sum(
            bool(row["sensitivity_score"].get("audit_pass")) for row in rows
        ),
        "source_context_support": sum(
            bool(row["source_score"].get("necessary_evidence_present")) for row in rows
        ),
        "sensitivity_context_support": sum(
            bool(row["sensitivity_score"].get("necessary_evidence_present")) for row in rows
        ),
        "source_citation_pass": sum(
            bool(row["source_score"].get("answer_citation_pass")) for row in rows
        ),
        "sensitivity_citation_pass": sum(
            bool(row["sensitivity_score"].get("answer_citation_pass")) for row in rows
        ),
        "source_semantic_pass": sum(
            row["source_score"].get("judge_auxiliary_pass") is True for row in rows
        ),
        "sensitivity_semantic_pass": sum(
            row["sensitivity_score"].get("judge_auxiliary_pass") is True for row in rows
        ),
    }


def _group(rows: list[dict[str, Any]], key: str) -> dict[str, dict[str, int]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(row[key])].append(row)
    return {name: _counts(groups[name]) for name in sorted(groups)}


def _strict_passes(rows: list[dict[str, Any]], score_key: str) -> dict[str, dict[str, int]]:
    totals: dict[str, int] = defaultdict(int)
    passes: dict[str, int] = defaultdict(int)
    for row in rows:
        for name, value in row[score_key].get("strict_values", {}).items():
            totals[name] += 1
            passes[name] += int(bool(value.get("pass")))
    return {
        name: {"pass": passes[name], "n": totals[name]}
        for name in sorted(totals)
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if args.output.exists():
        raise ValueError("output directory must be new")
    source_episodes = args.run / "episodes.jsonl"
    source_report_path = args.run / "report.json"
    if not source_episodes.is_file() or not source_report_path.is_file():
        raise FileNotFoundError("source run lacks episodes.jsonl or report.json")

    _, query_rows, gold_rows = load_dataset(args.dataset, legacy=False)
    queries = {row.query_id: row for row in query_rows}
    gold = {row.prefix_id: row for row in gold_rows}
    source_report = json.loads(source_report_path.read_text(encoding="utf-8"))
    calibrated = all(
        bool(cell.get("calibration", {}).get("pass"))
        for cell in source_report.get("cells", [])
    )
    if not calibrated:
        raise ValueError("source run did not pass its recorded calibration gate")

    rescored: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for row in load_jsonl(source_episodes):
        query = queries.get(str(row["query_id"]))
        if query is None:
            raise ValueError(f"dataset lacks source query {row['query_id']}")
        rubric = make_rubric(query, gold[query.prefix_id])
        new_score = score_submission(
            row.get("answer"),
            rubric,
            list(map(str, row.get("final_visible_ids", ()))),
            judge=row.get("judge"),
            judge_calibrated=calibrated,
            status=str(row.get("status", "complete")),
            executed_side_effects=sum(
                bool(call.get("executed_side_effect"))
                for call in row.get("tool_calls", ())
            ),
            unsafe_attempts=sum(
                bool(call.get("unsafe_side_effect_attempt"))
                for call in row.get("tool_calls", ())
            ),
        )
        item = {
            "episode_id": row["episode_id"],
            "phase": row["phase"],
            "method_id": row["method_id"],
            "prefix_id": row["prefix_id"],
            "query_id": row["query_id"],
            "query_type": row["query_type"],
            "source_score": row["score"],
            "sensitivity_score": new_score,
        }
        rescored.append(item)
        if any(
            row["score"].get(key) != new_score.get(key)
            for key in (
                "hard_pass", "audit_pass", "strict_values", "failure_labels",
                "failure_stages", "attribution",
            )
        ):
            changes.append(item)

    main_rows = [row for row in rescored if row["phase"] == "main"]
    oracle_chain = [
        row for row in rescored
        if row["phase"] == "diagnostic"
        and row["method_id"] == "oracle"
        and row["query_type"] == "audit_chain"
    ]
    task_chain_rows = [
        row for row in rescored
        if row["query_type"] == "audit_chain"
        and row["phase"] in {"main", "diagnostic"}
    ]
    report = {
        "schema_version": "failure_episode_answer_contract_sensitivity_v1",
        "interpretation": "post_hoc_zero_call_contract_sensitivity",
        "formal_comparison_eligible": False,
        "development_only": True,
        "independent_validation": False,
        "human_validated": False,
        "new_provider_requests": 0,
        "source_provider_requests_reused": len(load_jsonl(args.run / "provider_ledger.jsonl")),
        "rubric_version": RUBRIC_VERSION,
        "scoring_revision": SCORING_REVISION,
        "source_answer_contract_revisions": source_report.get(
            "answer_contract_revisions", []
        ),
        "counterfactual_mismatch": (
            "Saved answers were elicited under the source answer schema. The new schema "
            "was not shown to the answering model; this report isolates scoring-contract "
            "sensitivity and is not a replacement live result."
        ),
        "contract_change": {
            "task_episode_queries": [
                "audit_recovery", "audit_chain", "interactive_reacquisition"
            ],
            "removed_legacy_singular_strict_fields": [
                "replacement_action", "replacement_arguments"
            ],
            "multi_step_semantic_field": "repair_sequence",
            "error_signature_rule": (
                "answer must contain the gold stable signature after whitespace and "
                "terminal-period normalization; reverse containment is rejected"
            ),
        },
        "all_phases": _group(rescored, "phase"),
        "main": {
            "overall": _counts(main_rows),
            "by_method": _group(main_rows, "method_id"),
            "by_query_type": _group(main_rows, "query_type"),
        },
        "oracle_audit_chain": _counts(oracle_chain),
        "audit_chain_strict_field_passes": {
            "source": _strict_passes(task_chain_rows, "source_score"),
            "sensitivity": _strict_passes(task_chain_rows, "sensitivity_score"),
        },
        "changed_episode_count": len(changes),
        "source_hashes": {
            "episodes_sha256": file_sha256(source_episodes),
            "report_sha256": file_sha256(source_report_path),
            "dataset_manifest_sha256": file_sha256(args.dataset / "manifest.json"),
            "dataset_file_manifest_sha256": file_sha256(
                args.dataset / "file_manifest.jsonl"
            ),
        },
    }

    args.output.mkdir(parents=True)
    (args.output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    with (args.output / "changed_episodes.jsonl").open(
        "w", encoding="utf-8", newline="\n"
    ) as stream:
        for row in changes:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
