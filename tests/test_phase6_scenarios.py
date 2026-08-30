from __future__ import annotations

import tempfile
from pathlib import Path

from tracegraph.decision_state import stable_digest
from tracegraph.goal_lifecycle import GoalLifecycleState
from tracegraph.phase6_scenarios import (
    FORK_TYPES,
    SCENARIO_FAMILIES,
    VARIANTS,
    generate_trace_lifecycle_suite,
)


def test_fixed_population_and_gold_completeness() -> None:
    with tempfile.TemporaryDirectory() as directory:
        suite = generate_trace_lifecycle_suite(Path(directory) / "archive")
        assert len(suite) == len(SCENARIO_FAMILIES) * len(VARIANTS) == 24
        assert sum(len(item.forks) for item in suite) == 72
        for prefix in suite:
            assert not prefix.graph.validate()
            assert {item.fork_type for item in prefix.forks} == set(FORK_TYPES)
            assert GoalLifecycleState.DORMANT in prefix.lifecycle_gold_by_event.values()
            assert any(
                state in {GoalLifecycleState.ACTIVE, GoalLifecycleState.PINNED}
                for state in prefix.lifecycle_gold_by_event.values()
            )
            reactivate = next(item for item in prefix.forks if item.fork_type == "REACTIVATE")
            assert reactivate.required_subgraph_event_ids
            assert "fork_type" not in reactivate.request_payload()
            assert "required_subgraph_event_ids" not in reactivate.request_payload()


def test_generation_is_byte_stable_across_archive_roots() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        first = generate_trace_lifecycle_suite(root / "a")
        second = generate_trace_lifecycle_suite(root / "b")
        first_payload = {
            "prefixes": [item.to_prefix_dict() for item in first],
            "gold": [item.to_gold_dict() for item in first],
        }
        second_payload = {
            "prefixes": [item.to_prefix_dict() for item in second],
            "gold": [item.to_gold_dict() for item in second],
        }
        assert stable_digest(first_payload) == stable_digest(second_payload)
