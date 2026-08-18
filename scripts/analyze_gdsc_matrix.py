"""Analyze the preregistered four-condition GDSC development matrix."""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from tracegraph.trajectory_artifacts import sha256_json


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


CONDITIONS = (
    "full_trajectory", "acon_official", "decision_state_compiler", "acon_official_with_gdsc_state",
)


def _load(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, list) else value.get("rows") or value.get("sessions") or []


def _number(row: Mapping[str, Any], key: str) -> float | None:
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)) else None


def _binary(row: Mapping[str, Any], key: str) -> float | None:
    value = row.get(key)
    if isinstance(value, bool):
        return float(value)
    numeric = _number(row, key)
    return numeric if numeric is not None and numeric in {0.0, 1.0} else None


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _bootstrap_ci(values: Sequence[float], *, samples: int = 10_000, seed: int = 20260721) -> list[float] | None:
    if not values:
        return None
    if len(values) == 1:
        return [float(values[0]), float(values[0])]
    rng = random.Random(seed)
    means = sorted(statistics.fmean(rng.choice(values) for _ in values) for _ in range(samples))
    return [means[int(0.025 * (samples - 1))], means[int(0.975 * (samples - 1))]]


def _condition_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    fields = (
        "provider_input_tokens", "provider_output_tokens", "compiler_input_tokens",
        "compiler_output_tokens", "net_token_cost", "agent_actions", "repeated_actions",
        "recovery_action_index", "unsafe_omissions", "policy_violations", "collateral_damage",
    )
    successes = [value for row in rows if (value := _binary(row, "success")) is not None]
    result = {
        "sessions": len(rows),
        "success_rate": _mean(successes),
        "observed_success": len(successes),
    }
    for field in fields:
        values = [value for row in rows if (value := _number(row, field)) is not None]
        result[f"mean_{field}"] = _mean(values)
        result[f"observed_{field}"] = len(values)
    return result


def analyze(rows: Sequence[Mapping[str, Any]], *, bootstrap_samples: int = 10_000) -> dict[str, Any]:
    by_condition: defaultdict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_key: dict[tuple[str, str, int, str], Mapping[str, Any]] = {}
    duplicates = []
    for row in rows:
        condition = str(row.get("condition") or row.get("manager") or "")
        domain, task_id = str(row.get("domain") or ""), str(row.get("task_id") or "")
        seed = int(row.get("seed") if row.get("seed") is not None else row.get("trial") or 0)
        key = (domain, task_id, seed, condition)
        if key in by_key:
            duplicates.append(key)
        by_key[key] = row
        by_condition[condition].append(row)
    task_keys = sorted({key[:3] for key in by_key})
    complete_keys = [key for key in task_keys if all((*key, condition) in by_key for condition in CONDITIONS)]
    comparisons: dict[str, Any] = {}
    full_name = "full_trajectory"
    for comparator in CONDITIONS[1:]:
        paired = [(by_key[(*key, full_name)], by_key[(*key, comparator)]) for key in complete_keys]
        metric_deltas: dict[str, Any] = {}
        for field in (
            "provider_input_tokens", "net_token_cost", "success", "policy_violations",
            "collateral_damage", "agent_actions", "repeated_actions", "recovery_action_index",
            "unsafe_omissions",
        ):
            deltas = []
            for reference, candidate in paired:
                left = _binary(reference, field) if field == "success" else _number(reference, field)
                right = _binary(candidate, field) if field == "success" else _number(candidate, field)
                if left is not None and right is not None:
                    deltas.append(right - left)
            metric_deltas[field] = {
                "pairs": len(deltas), "mean_delta": _mean(deltas),
                "ci95": _bootstrap_ci(deltas, samples=bootstrap_samples, seed=20260721 + len(metric_deltas)),
            }
        comparisons[comparator] = {"pairs": len(paired), "deltas": metric_deltas}

    gdsc = comparisons.get("decision_state_compiler", {"pairs": 0, "deltas": {}})
    delta = gdsc.get("deltas", {})
    input_delta = delta.get("provider_input_tokens", {})
    reference_inputs = [
        _number(by_key[(*key, full_name)], "provider_input_tokens") for key in complete_keys
    ]
    reference_mean = _mean([value for value in reference_inputs if value is not None])
    input_reduction = (
        -float(input_delta["mean_delta"]) / reference_mean
        if reference_mean and input_delta.get("mean_delta") is not None else None
    )
    domains = sorted({key[0] for key in complete_keys})
    domain_direction = {}
    for domain in domains:
        domain_pairs = [key for key in complete_keys if key[0] == domain]
        input_ds, success_ds = [], []
        for key in domain_pairs:
            reference, candidate = by_key[(*key, full_name)], by_key[(*key, "decision_state_compiler")]
            left, right = _number(reference, "provider_input_tokens"), _number(candidate, "provider_input_tokens")
            if left is not None and right is not None:
                input_ds.append(right - left)
            left_success = _binary(reference, "success")
            right_success = _binary(candidate, "success")
            if left_success is not None and right_success is not None:
                success_ds.append(right_success - left_success)
        domain_direction[domain] = {
            "input_delta": _mean(input_ds), "success_risk_difference": _mean(success_ds),
            "supports_direction": bool(input_ds) and _mean(input_ds) < 0 and _mean(success_ds) >= -0.05,
        }
    gdsc_metrics = by_condition.get("decision_state_compiler", [])
    acon_compare = comparisons.get("acon_official", {})
    acon_rows = by_condition.get("acon_official", [])
    gdsc_success = _mean([value for row in gdsc_metrics if (value := _binary(row, "success")) is not None])
    acon_success = _mean([value for row in acon_rows if (value := _binary(row, "success")) is not None])
    gdsc_tokens = _mean([value for row in gdsc_metrics if (value := _number(row, "provider_input_tokens")) is not None])
    acon_tokens = _mean([value for row in acon_rows if (value := _number(row, "provider_input_tokens")) is not None])
    gdsc_unsafe = sum(_number(row, "unsafe_omissions") or 0 for row in gdsc_metrics)
    acon_unsafe = sum(_number(row, "unsafe_omissions") or 0 for row in acon_rows)
    matched_advantage = bool(
        gdsc_tokens is not None and acon_tokens is not None and gdsc_success is not None and acon_success is not None
        and ((gdsc_tokens <= acon_tokens * 1.05 and gdsc_unsafe < acon_unsafe)
             or (gdsc_tokens < acon_tokens and gdsc_success - acon_success >= -0.05))
    )
    success_ci = (delta.get("success") or {}).get("ci95")
    input_ci = input_delta.get("ci95")
    net_delta = delta.get("net_token_cost", {})
    safety_fields = ("policy_violations", "collateral_damage")
    behavior_fields = ("agent_actions", "repeated_actions", "recovery_action_index")
    required_fields = (
        "provider_input_tokens", "net_token_cost", "success", "policy_violations",
        "collateral_damage", "agent_actions", "repeated_actions", "recovery_action_index",
        "unsafe_omissions",
    )
    measurements_complete = bool(complete_keys) and all(
        all(
            (_binary(row, field) if field == "success" else _number(row, field)) is not None
            for field in required_fields
        )
        for key in complete_keys
        for row in (by_key[(*key, "full_trajectory")], by_key[(*key, "decision_state_compiler")])
    )
    checks = {
        "matrix_complete": not duplicates and bool(task_keys) and len(complete_keys) == len(task_keys),
        "primary_measurements_complete": measurements_complete,
        "gdsc_input_reduction_at_least_15_percent": input_reduction is not None and input_reduction >= 0.15,
        "gdsc_input_ci_upper_below_zero": input_ci is not None and input_ci[1] < 0,
        "session_net_token_cost_decreased": net_delta.get("mean_delta") is not None and net_delta["mean_delta"] < 0,
        "success_noninferiority": success_ci is not None and success_ci[0] >= -0.05,
        "policy_and_collateral_not_increased": all(
            (delta.get(field) or {}).get("mean_delta") is not None
            and delta[field]["mean_delta"] <= 0 for field in safety_fields
        ),
        "actions_repeats_recovery_not_worse": all(
            (delta.get(field) or {}).get("mean_delta") is not None
            and delta[field]["mean_delta"] <= 0 for field in behavior_fields
        ),
        "advantage_over_acon": matched_advantage and bool(acon_compare),
        "cross_domain_direction_consistent": len(domain_direction) >= 2
        and all(item["supports_direction"] for item in domain_direction.values()),
    }
    report: dict[str, Any] = {
        "schema_version": "gdsc_matrix_analysis_v1", "conditions": list(CONDITIONS),
        "row_count": len(rows), "complete_pair_count": len(complete_keys), "duplicate_keys": duplicates,
        "condition_metrics": {name: _condition_metrics(by_condition.get(name, [])) for name in CONDITIONS},
        "paired_comparisons_vs_full": comparisons, "gdsc_input_reduction": input_reduction,
        "domain_direction": domain_direction, "positive_gate_checks": checks,
        "development_positive_evidence": all(checks.values()),
        "allowed_claim": "tau3_cross_domain_development_positive_evidence" if all(checks.values()) else "no_positive_claim",
        "formal_aaai_gate_claimed": False,
    }
    report["report_sha256"] = sha256_json(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    args = parser.parse_args()
    report = analyze(_load(args.input), bootstrap_samples=args.bootstrap_samples)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
