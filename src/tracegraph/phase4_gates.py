"""Fail-closed Phase 4 engineering and empirical-claim gates."""

from __future__ import annotations

from typing import Any, Mapping


def evaluate_phase4_gates(
    *,
    migration_audit: Mapping[str, Any],
    v2_construct_report: Mapping[str, Any],
    trajectory_protocol_audit: Mapping[str, Any],
    post_failure_report: Mapping[str, Any],
    common_prefix_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Separate reproducibility readiness from unsupported empirical claims."""

    overall = post_failure_report.get("overall") or {}
    card = (post_failure_report.get("by_condition") or {}).get("full_ours") or {}
    protocol_checks = trajectory_protocol_audit.get("checks") or {}
    engineering_checks = {
        "v1_inputs_immutable": migration_audit.get("v1_inputs_modified") is False,
        "v2_migration_60_of_60": int(migration_audit.get("source_chain_count") or 0) == 60
        and int(migration_audit.get("migrated_key_count") or 0) == 60,
        "clean_human_sheets_60": int(migration_audit.get("clean_human_sheet_count") or 0) == 60,
        "v2_report_complete": bool(v2_construct_report.get("complete"))
        and int(v2_construct_report.get("unresolved_adjudications") or 0) == 0,
        "v2_provenance_not_misrepresented": bool(v2_construct_report.get("provisional_only"))
        and not bool(v2_construct_report.get("human_independent_annotations")),
        "trajectory_protocol_fault_injection_passed": bool(protocol_checks)
        and all(bool(value) for value in protocol_checks.values()),
        "post_failure_replay_complete": bool(post_failure_report.get("complete"))
        and int(post_failure_report.get("session_count") or 0) == 60
        and int(post_failure_report.get("event_count") or 0) > 0,
        "action_view_alignment_complete": int(overall.get("context_views_aligned") or 0)
        == int(overall.get("context_views_expected") or -1),
        "provider_usage_complete_for_observed_actions": int(
            overall.get("provider_input_usage_events") or 0
        )
        == int(overall.get("events_with_actions") or -1)
        and int(overall.get("provider_output_usage_events") or 0)
        == int(overall.get("events_with_actions") or -1),
        "card_exposure_observed": int(card.get("target_failure_card_visible_actions") or 0) > 0,
        "card_lane_reports_no_raw_replay": int(card.get("raw_failure_replay_observed_events") or 0)
        > 0
        and int(card.get("raw_failure_replay_actions") or 0) == 0,
    }
    engineering_passed = all(engineering_checks.values())

    prefix = common_prefix_report or {}
    empirical_checks = {
        "independent_human_v2_gold": bool(v2_construct_report.get("human_independent_annotations")),
        "common_prefix_fork_complete": bool(prefix.get("complete")),
        "card_vs_remove_mechanism_identified": bool(prefix.get("mechanism_identified")),
        "success_and_policy_safety_passed": bool(prefix.get("safety_gate_passed")),
        "not_single_failure_type_driven": bool(prefix.get("failure_type_robustness_passed")),
    }
    empirical_passed = engineering_passed and all(empirical_checks.values())
    blockers = [name for name, passed in empirical_checks.items() if not passed]
    return {
        "schema_version": "1.0",
        "phase4": {
            "engineering_gate_passed": engineering_passed,
            "engineering_checks": engineering_checks,
            "empirical_claim_gate_passed": empirical_passed,
            "empirical_checks": empirical_checks,
            "blockers": blockers,
        },
        "p3b_b": {
            "go_gate_passed": empirical_passed,
            "external_api_execution_authorized": False,
            "blockers": blockers,
        },
        "aaai_readiness": {
            "research_infrastructure_ready": engineering_passed,
            "positive_empirical_claim_ready": empirical_passed,
            "allowed_current_claim": (
                "submission-grade measurement and reproducibility infrastructure"
                if engineering_passed
                else "engineering work remains incomplete"
            ),
            "forbidden_current_claim": (
                None
                if empirical_passed
                else "Failure Cards causally improve natural tool-agent trajectories"
            ),
        },
    }
