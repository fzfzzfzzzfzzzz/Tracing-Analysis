from __future__ import annotations

import unittest

from tracegraph.phase3_gates import evaluate_phase3_gates


def _p1() -> dict:
    return {
        "mechanism_gate": {
            "complete": True,
            "all_graphs_valid": True,
            "p1_engineering_gate_passed": True,
            "card_precision_controlled_gold": 1.0,
            "expiry_correctness_controlled_gold": 1.0,
            "all_failure_types_directionally_consistent": True,
        }
    }


def _p2() -> dict:
    return {
        "complete": True,
        "chain_count": 60,
        "annotation_provenance": "human_independent",
        "human_independent_annotations": True,
        "cohen_kappa": 0.75,
        "actionable_precision": 0.8,
        "actionable_recall": 0.8,
        "expiry_precision": 0.95,
        "operation_scope_aggregation_error_rate": 0.05,
    }


def _bootstrap(low: float, high: float) -> dict:
    return {"ci95_low": low, "ci95_high": high, "mean_delta": (low + high) / 2}


def _report(reference: str) -> dict:
    comparison = {
        "paired_bootstrap": _bootstrap(-0.02, 0.01),
        "agent_provider_input_token_delta_bootstrap": _bootstrap(-200, -20),
        "protocol_closed_message_token_delta_bootstrap": _bootstrap(-150, -10),
        "repeated_invalid_action_delta_bootstrap": _bootstrap(-1, 0),
        "recovery_step_delta_bootstrap": _bootstrap(-1, -0.1),
    }
    return {
        "matrix_id": "p3-test",
        "complete": True,
        "reference_manager": reference,
        "condition_metrics": {
            name: {
                "agent_provider_input_usage_coverage": 1.0,
                **(
                    {
                        "budget_infeasible_sessions": 0,
                        "mean_raw_failure_messages_selected": 0.0,
                    }
                    if name == "full_ours"
                    else {}
                ),
            }
            for name in (
                "full_trajectory",
                "ours_without_failure_retention",
                "raw_hard_failure_retention",
                "full_ours",
            )
        },
        "paired_comparisons": {"full_ours": comparison},
    }


class Phase3GateTests(unittest.TestCase):
    def test_missing_p2_fails_closed(self) -> None:
        report = evaluate_phase3_gates(
            p1_manifest=_p1(),
            p2_report=None,
            p3_reports_by_reference={},
        )
        self.assertFalse(report["p3"]["formal_p3_gate_passed"])
        self.assertFalse(report["p4"]["go_gate_passed"])
        self.assertIn("p2_human_construct_gate", report["p4"]["blockers"])

    def test_complete_evidence_can_authorize_p4(self) -> None:
        reports = {
            reference: _report(reference)
            for reference in (
                "full_trajectory",
                "ours_without_failure_retention",
                "raw_hard_failure_retention",
            )
        }
        report = evaluate_phase3_gates(
            p1_manifest=_p1(),
            p2_report=_p2(),
            p3_reports_by_reference=reports,
        )
        self.assertTrue(report["p3"]["formal_p3_gate_passed"])
        self.assertTrue(report["p4"]["go_gate_passed"])
        self.assertEqual(report["p4"]["blockers"], [])

    def test_codex_labels_do_not_pass_formal_human_gate(self) -> None:
        p2 = _p2()
        p2["annotation_provenance"] = "codex_provisional"
        p2["human_independent_annotations"] = False
        report = evaluate_phase3_gates(
            p1_manifest=_p1(), p2_report=p2, p3_reports_by_reference={}
        )
        self.assertFalse(report["p2"]["passed"])
        self.assertIn("independent human", report["p2"]["reason"])


if __name__ == "__main__":
    unittest.main()
