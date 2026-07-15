import unittest

from tracegraph.budget_sweep import choose_budget, summarize_budget


def _manager_row(tokens: int, *, unsafe: int = 0) -> dict:
    return {
        "input_tokens": tokens,
        "compression_ratio": 0.75,
        "constraint_retention": 1.0,
        "unresolved_failure_retention": 1.0,
        "evidence_retention": 1.0,
        "unsafe_removal_count": unsafe,
    }


class BudgetSweepTests(unittest.TestCase):
    def test_selects_smallest_structurally_feasible_budget(self):
        manager_rows = [_manager_row(4096), _manager_row(5000), _manager_row(6000)]
        oracle_rows = [{"input_tokens": value} for value in (3000, 5000, 6000)]
        low = summarize_budget(
            budget=4096,
            manager_rows=manager_rows,
            oracle_rows=oracle_rows,
            maximum_overflow_rate=0.34,
        )
        high = summarize_budget(
            budget=8192,
            manager_rows=manager_rows,
            oracle_rows=oracle_rows,
            maximum_overflow_rate=0.34,
        )
        self.assertFalse(low["feasible"])
        self.assertGreater(low["full_ours"]["budget_overflow_rate"], 0.34)
        self.assertTrue(high["feasible"])
        self.assertEqual(high["mandatory_context"]["p95_tokens"], 6000)
        self.assertEqual(choose_budget([high, low]), 8192)

    def test_unsafe_removal_blocks_recommendation(self):
        summary = summarize_budget(
            budget=8192,
            manager_rows=[_manager_row(5000, unsafe=1)],
            oracle_rows=[{"input_tokens": 4000}],
            maximum_overflow_rate=0.05,
        )
        self.assertFalse(summary["structural_safe"])
        self.assertIsNone(choose_budget([summary]))

    def test_rejects_invalid_budget(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            summarize_budget(
                budget=0,
                manager_rows=[_manager_row(1)],
                oracle_rows=[{"input_tokens": 1}],
                maximum_overflow_rate=0.05,
            )

    def test_rejects_misaligned_rows(self):
        with self.assertRaisesRegex(ValueError, "equal length"):
            summarize_budget(
                budget=4096,
                manager_rows=[_manager_row(1), _manager_row(2)],
                oracle_rows=[{"input_tokens": 1}],
                maximum_overflow_rate=0.05,
            )


if __name__ == "__main__":
    unittest.main()
