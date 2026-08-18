from __future__ import annotations

import unittest

from tracegraph.cost_attribution import summarize_cost_attribution


def _row(point: str, fixed: float, constructive: float) -> dict:
    return {
        "decision_point_id": point,
        "domain": "retail",
        "raw_serialized_tokens": 1000,
        "compiled_serialized_tokens": 850,
        "current_serialized_reduction": 0.15,
        "raw_fixed_policy_tools_tokens": 750,
        "fixed_floor_max_reduction": fixed,
        "constructive_hard_floor_tokens": int(1000 * (1 - constructive)),
        "constructive_hard_floor_reduction": constructive,
        "request_hash_matches_baseline": True,
        "runtime_prompt_hash_matches": True,
        "raw_cost_matches_baseline": True,
        "compiled_cost_matches_baseline": True,
        "policy_exposed_exactly_once": True,
        "tool_schema_top_level_exact": True,
        "constructive_hard_coverage": True,
    }


class CostAttributionSummaryTests(unittest.TestCase):
    def test_unreachable_when_optimistic_fixed_floor_misses_threshold(self) -> None:
        report = summarize_cost_attribution(
            [_row("p1", 0.20, 0.10), _row("p2", 0.25, 0.15)]
        )
        self.assertEqual(
            report["attainability_decision"],
            "unreachable_under_frozen_fixed_cost",
        )
        self.assertTrue(report["diagnostic_gate_passed"])

    def test_reachable_requires_constructive_hard_coverage(self) -> None:
        report = summarize_cost_attribution(
            [_row("p1", 0.50, 0.35), _row("p2", 0.45, 0.40)]
        )
        self.assertEqual(
            report["attainability_decision"],
            "reachable_with_verified_constructive_request",
        )

    def test_measurement_mismatch_fails_closed(self) -> None:
        row = _row("p1", 0.20, 0.10)
        row["request_hash_matches_baseline"] = False
        report = summarize_cost_attribution([row])
        self.assertEqual(report["attainability_decision"], "measurement_invalid")
        self.assertFalse(report["diagnostic_gate_passed"])


if __name__ == "__main__":
    unittest.main()
