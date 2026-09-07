"""只读复核新版自动判分对旧试跑结果的影响，不改写正式结果。"""

from __future__ import annotations

from tracegraph.plain_cli import PlainArgumentParser, run_cli

import json
from pathlib import Path
from typing import Any, Mapping

from tracegraph.phase6.live import (
    SCORING_PROTOCOL_V2,
    file_sha256,
    load_jsonl,
    score_live_answer,
    summarize_live_results,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _failed_score() -> dict[str, Any]:
    return {
        "scoring_protocol": SCORING_PROTOCOL_V2,
        "answer_success": False,
        "answer_fact_match": False,
        "fact_scope_match": False,
        "side_effect_safe": False,
        "unknown_evidence_record_ids": [],
        "evidence_event_ids": [],
        "required_evidence_recall": 0.0,
        "required_anchor_cited": False,
        "evidence_complete_for_answer": False,
        "old_fact_used_as_current": False,
    }


def _verify_frozen_result(root: Path) -> list[str]:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    hashes = json.loads((root / "hashes.json").read_text(encoding="utf-8"))
    failures = [
        name
        for name, expected in hashes.items()
        if not (root / name).is_file() or file_sha256(root / name) != expected
    ]
    if file_sha256(root / "hashes.json") != manifest["hashes_sha256"]:
        failures.append("hashes.json")
    return sorted(set(failures))


def _rescore_rows(
    results: list[Mapping[str, Any]],
    trials: Mapping[str, Mapping[str, Any]],
    forks: Mapping[str, Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    rescored: list[dict[str, Any]] = []
    changed: list[str] = []
    for old in results:
        row = dict(old)
        if old.get("valid_response") and isinstance(old.get("answer"), dict):
            score = score_live_answer(
                old["answer"],
                trials[str(old["trial_id"])],
                forks[str(old["fork_id"])],
                SCORING_PROTOCOL_V2,
            )
        else:
            score = _failed_score()
        if bool(old.get("answer_success")) != bool(score["answer_success"]):
            changed.append(str(old["trial_id"]))
        row.update(score)
        rescored.append(row)
    return rescored, sorted(changed)


def main() -> int:
    parser = PlainArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("outputs/phase6/e3_qwen38_27b_pilot_v2"),
        help="要只读复核的旧试跑结果目录",
    )
    args = parser.parse_args()
    root = args.input.resolve()
    hash_failures = _verify_frozen_result(root)
    if hash_failures:
        raise ValueError(f"旧试跑结果文件指纹不匹配：{hash_failures}")
    config = json.loads((root / "config.snapshot.json").read_text(encoding="utf-8"))
    input_root = (REPO_ROOT / str(config["input_root"])).resolve()
    results = load_jsonl(root / "results.jsonl")
    trials = {
        str(row["trial_id"]): row
        for row in load_jsonl(root / "request_templates.jsonl")
    }
    forks = {
        str(row["fork_id"]): row for row in load_jsonl(input_root / "forks.jsonl")
    }
    rescored, changed = _rescore_rows(results, trials, forks)
    metrics, gates = summarize_live_results(rescored, config)
    report = {
        "schema_version": "phase6_live_scoring_review_v2",
        "source_run_id": config["run_id"],
        "source_artifact_hashes_valid": True,
        "formal_results_modified": False,
        "posthoc_only": True,
        "scoring_protocol": SCORING_PROTOCOL_V2,
        "changed_trial_count": len(changed),
        "changed_trial_ids": changed,
        "method_summaries": metrics["methods"],
        "would_be_gate_decision": gates["decision"],
        "would_be_gate_criteria": gates["criteria"],
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
