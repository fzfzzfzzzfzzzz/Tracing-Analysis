#!/usr/bin/env python3
"""Create an immutable audit report for a paused Phase 5.2 collection."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tracegraph.lifecycle_annotation import load_phase52_config  # noqa: E402
from tracegraph.trajectory_artifacts import sha256_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=REPO_ROOT / "configs/phase52_lifecycle_modeling.json",
    )
    parser.add_argument(
        "--reason",
        default="zai_http_429_code_1305_model_currently_overloaded",
    )
    parser.add_argument(
        "--status",
        choices=(
            "paused_external_rate_limit",
            "stopped_collection_validation_failure",
            "stopped_global_budget",
        ),
        default="paused_external_rate_limit",
    )
    parser.add_argument("--failure-is-quality-no-go", action="store_true")
    args = parser.parse_args()
    config = load_phase52_config(args.config)
    output_root = REPO_ROOT / config["output_root"]
    ledger = [
        json.loads(line)
        for line in (output_root / "usage_ledger.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]
    valid_request_ids = {str(item["request_id"]) for item in ledger if item["valid"]}
    statuses = Counter(int(item["http_status"]) for item in ledger)
    invalid_attempts = [item for item in ledger if not item["valid"]]
    retry_limit = int(config["annotation"]["retry_per_request_max"]) + 1
    invalid_counts = Counter(str(item["request_id"]) for item in invalid_attempts)
    retry_exhausted = sorted(
        request_id
        for request_id, count in invalid_counts.items()
        if count >= retry_limit
    )
    resume_supported = args.status == "paused_external_rate_limit"
    report: dict[str, Any] = {
        "schema_version": "phase52_collection_pause_v1",
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "status": args.status,
        "reason": args.reason,
        "model": config["model"]["report_identity"],
        "counts": {
            "request_attempts": len(ledger),
            "valid_requests": len(valid_request_ids),
            "required_requests": 370,
            "remaining_requests": 370 - len(valid_request_ids),
            "http_statuses": {str(key): value for key, value in sorted(statuses.items())},
            "invalid_response_attempts": len(invalid_attempts),
            "retry_exhausted_request_ids": retry_exhausted,
        },
        "usage": {
            "prompt_tokens": sum(int(item["usage"]["prompt_tokens"]) for item in ledger),
            "completion_tokens": sum(
                int(item["usage"]["completion_tokens"]) for item in ledger
            ),
            "total_tokens": sum(int(item["usage"]["total_tokens"]) for item in ledger),
        },
        "limits": {
            "request_count_hard_max": config["annotation"]["request_count_hard_max"],
            "estimated_input_tokens_hard_max": config["annotation"][
                "estimated_input_tokens_hard_max"
            ],
            "actual_output_tokens_hard_max": config["annotation"][
                "actual_output_tokens_hard_max"
            ],
        },
        "resume_contract": {
            "resume_supported": resume_supported,
            "same_frozen_requests_only": True,
            "same_model_only": config["model"]["api_model"],
            "paid_or_model_fallback_forbidden": config["model"].get(
                "paid_use_forbidden"
            )
            is True,
            "paid_use_authorized_by_user": config["model"].get(
                "paid_use_authorized_by_user"
            )
            is True,
            "recheck_pricing_before_resume": resume_supported,
            "skip_valid_existing_labels": True,
        },
        "gates": {
            "pseudolabel_gate_computable": False,
            "state_machine_held_out_gate_authorized": False,
            "failure_is_quality_no_go": args.failure_is_quality_no_go,
        },
        "external_behavior_model_sessions": 0,
        "scheme_b_started": False,
        "old_no_go_preserved": True,
    }
    report["report_sha256"] = sha256_json(report)
    reports = output_root / "pause_reports"
    reports.mkdir(parents=True, exist_ok=True)
    index = len(list(reports.glob("pause_*.json"))) + 1
    path = reports / f"pause_{index:04d}.json"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({"path": path.as_posix(), **report}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
