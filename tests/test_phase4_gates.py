from __future__ import annotations

import unittest

from tracegraph.phase4_gates import evaluate_phase4_gates


class Phase4GateTests(unittest.TestCase):
    def test_engineering_can_pass_while_empirical_claim_fails_closed(self) -> None:
        report = evaluate_phase4_gates(
            migration_audit={
                "v1_inputs_modified": False,
                "source_chain_count": 60,
                "migrated_key_count": 60,
                "clean_human_sheet_count": 60,
            },
            v2_construct_report={
                "complete": True,
                "unresolved_adjudications": 0,
                "provisional_only": True,
                "human_independent_annotations": False,
            },
            trajectory_protocol_audit={"checks": {"immutable": True, "retry": True}},
            post_failure_report={
                "complete": True,
                "session_count": 60,
                "event_count": 10,
                "overall": {
                    "context_views_aligned": 20,
                    "context_views_expected": 20,
                    "events_with_actions": 9,
                    "provider_input_usage_events": 9,
                    "provider_output_usage_events": 9,
                },
                "by_condition": {
                    "full_ours": {
                        "target_failure_card_visible_actions": 4,
                        "raw_failure_replay_observed_events": 3,
                        "raw_failure_replay_actions": 0,
                    }
                },
            },
        )
        self.assertTrue(report["phase4"]["engineering_gate_passed"])
        self.assertFalse(report["phase4"]["empirical_claim_gate_passed"])
        self.assertFalse(report["p3b_b"]["go_gate_passed"])
        self.assertFalse(report["p3b_b"]["external_api_execution_authorized"])
        self.assertIn("independent_human_v2_gold", report["phase4"]["blockers"])

    def test_missing_usage_fails_engineering_gate(self) -> None:
        report = evaluate_phase4_gates(
            migration_audit={},
            v2_construct_report={},
            trajectory_protocol_audit={},
            post_failure_report={},
        )
        self.assertFalse(report["phase4"]["engineering_gate_passed"])
        self.assertFalse(report["aaai_readiness"]["research_infrastructure_ready"])


if __name__ == "__main__":
    unittest.main()
