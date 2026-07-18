"""Fail-closed completion and Go/No-Go audit for phase-three experiments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .schema import utc_now


P3_REQUIRED_CONDITIONS = {
    "full_trajectory",
    "ours_without_failure_retention",
    "raw_hard_failure_retention",
    "full_ours",
}


def _ci(report: Mapping[str, Any] | None, key: str) -> dict[str, Any]:
    value = report.get(key) if report else None
    return value if isinstance(value, dict) else {}


def _comparison(
    reports_by_reference: Mapping[str, Mapping[str, Any]],
    reference: str,
) -> Mapping[str, Any] | None:
    report = reports_by_reference.get(reference)
    if not report:
        return None
    value = (report.get("paired_comparisons") or {}).get("full_ours")
    return value if isinstance(value, dict) else None


def _p1_status(manifest: Mapping[str, Any]) -> dict[str, Any]:
    gate = manifest.get("mechanism_gate") or {}
    return {
        "manifest_complete": bool(gate.get("complete")),
        "all_graphs_valid": bool(gate.get("all_graphs_valid")),
        "engineering_gate_passed": bool(gate.get("p1_engineering_gate_passed")),
        "controlled_card_precision": gate.get("card_precision_controlled_gold"),
        "controlled_expiry_correctness": gate.get(
            "expiry_correctness_controlled_gold"
        ),
        "failure_type_consistency": bool(
            gate.get("all_failure_types_directionally_consistent")
        ),
    }


def evaluate_p2_status(report: Mapping[str, Any] | None) -> dict[str, Any]:
    if report is None:
        return {
            "provided": False,
            "complete": False,
            "passed": False,
            "reason": "P2 human failure-chain construct validation was not provided",
        }
    chain_count = int(report.get("chain_count") or report.get("n") or 0)
    kappa = report.get("cohen_kappa")
    precision = report.get("actionable_precision")
    recall = report.get("actionable_recall")
    expiry_precision = report.get("expiry_precision")
    scope_error = report.get("operation_scope_aggregation_error_rate")
    provenance = str(report.get("annotation_provenance") or "unknown")
    human_independent = bool(report.get("human_independent_annotations"))
    complete = bool(report.get("complete")) and chain_count >= 60
    passed = (
        complete
        and provenance == "human_independent"
        and human_independent
        and isinstance(kappa, (int, float))
        and float(kappa) >= 0.70
        and isinstance(precision, (int, float))
        and float(precision) >= 0.75
        and isinstance(recall, (int, float))
        and float(recall) >= 0.75
        and isinstance(expiry_precision, (int, float))
        and float(expiry_precision) >= 0.90
        and isinstance(scope_error, (int, float))
        and float(scope_error) <= 0.10
    )
    reason = None
    if not human_independent or provenance != "human_independent":
        reason = (
            "formal P2 requires two explicitly identified independent human "
            f"annotations; received provenance={provenance!r}"
        )
    return {
        "provided": True,
        "complete": complete,
        "passed": passed,
        "reason": reason,
        "annotation_provenance": provenance,
        "human_independent_annotations": human_independent,
        "chain_count": chain_count,
        "cohen_kappa": kappa,
        "actionable_precision": precision,
        "actionable_recall": recall,
        "expiry_precision": expiry_precision,
        "operation_scope_aggregation_error_rate": scope_error,
    }


def evaluate_phase3_gates(
    *,
    p1_manifest: Mapping[str, Any],
    p2_report: Mapping[str, Any] | None,
    p3_reports_by_reference: Mapping[str, Mapping[str, Any]],
    noninferiority_margin: float = -0.05,
) -> dict[str, Any]:
    """Evaluate formal-P3 completion and the preregistered P4 Go gate.

    At least three P3 analyses are expected, using Full Trajectory, Remove, and
    Raw Hard as reference managers. Missing evidence always fails closed.
    """

    p1 = _p1_status(p1_manifest)
    p2 = evaluate_p2_status(p2_report)
    p3_reports = list(p3_reports_by_reference.values())
    matrix_ids = {str(report.get("matrix_id")) for report in p3_reports}
    conditions = {
        condition
        for report in p3_reports
        for condition in (report.get("condition_metrics") or {})
    }
    p3_complete = (
        len(p3_reports_by_reference) >= 3
        and len(matrix_ids) == 1
        and all(bool(report.get("complete")) for report in p3_reports)
        and P3_REQUIRED_CONDITIONS <= conditions
    )
    provider_usage_complete = bool(p3_reports) and all(
        all(
            metrics.get("agent_provider_input_usage_coverage") == 1.0
            for condition, metrics in (report.get("condition_metrics") or {}).items()
            if condition in P3_REQUIRED_CONDITIONS
        )
        for report in p3_reports
    )
    full_ours_metrics = None
    if p3_reports:
        full_ours_metrics = (p3_reports[0].get("condition_metrics") or {}).get(
            "full_ours"
        )
    bounded_cards = bool(full_ours_metrics) and (
        full_ours_metrics.get("budget_infeasible_sessions") == 0
        and full_ours_metrics.get("mean_raw_failure_messages_selected") == 0.0
    )
    formal_p3_gate_passed = (
        p1["engineering_gate_passed"]
        and p2["passed"]
        and p3_complete
        and provider_usage_complete
        and bounded_cards
    )

    vs_raw = _comparison(p3_reports_by_reference, "raw_hard_failure_retention")
    vs_remove = _comparison(
        p3_reports_by_reference, "ours_without_failure_retention"
    )
    raw_input_ci = _ci(vs_raw, "agent_provider_input_token_delta_bootstrap")
    raw_protocol_ci = _ci(vs_raw, "protocol_closed_message_token_delta_bootstrap")
    raw_repeat_ci = _ci(vs_raw, "repeated_invalid_action_delta_bootstrap")
    remove_repeat_ci = _ci(vs_remove, "repeated_invalid_action_delta_bootstrap")
    remove_recovery_ci = _ci(vs_remove, "recovery_step_delta_bootstrap")
    raw_success_ci = _ci(vs_raw, "paired_bootstrap")
    remove_success_ci = _ci(vs_remove, "paired_bootstrap")

    card_reduces_raw_input = (
        isinstance(raw_input_ci.get("ci95_high"), (int, float))
        and float(raw_input_ci["ci95_high"]) < 0
        and isinstance(raw_protocol_ci.get("ci95_high"), (int, float))
        and float(raw_protocol_ci["ci95_high"]) < 0
    )
    card_does_not_increase_repeats_vs_raw = (
        isinstance(raw_repeat_ci.get("ci95_high"), (int, float))
        and float(raw_repeat_ci["ci95_high"]) <= 0
    )
    card_improves_mechanism_vs_remove = (
        isinstance(remove_repeat_ci.get("ci95_high"), (int, float))
        and float(remove_repeat_ci["ci95_high"]) < 0
    ) or (
        isinstance(remove_recovery_ci.get("ci95_high"), (int, float))
        and float(remove_recovery_ci["ci95_high"]) < 0
    )
    task_success_noninferior = all(
        isinstance(ci.get("ci95_low"), (int, float))
        and float(ci["ci95_low"]) >= noninferiority_margin
        for ci in (raw_success_ci, remove_success_ci)
    )
    p4_go_gate_passed = (
        formal_p3_gate_passed
        and p1["failure_type_consistency"]
        and card_reduces_raw_input
        and card_does_not_increase_repeats_vs_raw
        and card_improves_mechanism_vs_remove
        and task_success_noninferior
    )
    blockers = []
    checks = {
        "p1_engineering_gate": p1["engineering_gate_passed"],
        "p2_human_construct_gate": p2["passed"],
        "p3_matrix_complete": p3_complete,
        "provider_usage_complete": provider_usage_complete,
        "failure_cards_bounded_and_no_raw_replay": bounded_cards,
        "failure_type_consistency": p1["failure_type_consistency"],
        "card_reduces_raw_protocol_and_provider_input": card_reduces_raw_input,
        "card_does_not_increase_repeats_vs_raw": card_does_not_increase_repeats_vs_raw,
        "card_improves_repeats_or_recovery_vs_remove": card_improves_mechanism_vs_remove,
        "task_success_noninferior": task_success_noninferior,
    }
    for name, passed in checks.items():
        if not passed:
            blockers.append(name)
    return {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "matrix_ids": sorted(matrix_ids),
        "noninferiority_margin": noninferiority_margin,
        "p1": p1,
        "p2": p2,
        "p3": {
            "reports_provided": sorted(p3_reports_by_reference),
            "complete": p3_complete,
            "provider_usage_complete": provider_usage_complete,
            "bounded_cards": bounded_cards,
            "formal_p3_gate_passed": formal_p3_gate_passed,
        },
        "p4": {
            "go_gate_passed": p4_go_gate_passed,
            "checks": checks,
            "blockers": blockers,
        },
        "interpretation": (
            "P4 expansion is authorized by the preregistered gate."
            if p4_go_gate_passed
            else "P4 expansion is not authorized; missing or failed evidence must not be relabeled as completion."
        ),
    }


def write_phase3_gate_report(report: Mapping[str, Any], path: str | Path) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(dict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
