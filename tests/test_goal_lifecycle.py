from __future__ import annotations

import tempfile
from pathlib import Path

from tracegraph.archive import ArchiveStore
from tracegraph.goal_lifecycle import (
    GoalLifecycleState,
    GoalLifecycleView,
    ProjectionAction,
    analyze_goal_lifecycle,
)
from tracegraph.phase6_scenarios import generate_trace_lifecycle_suite


def test_goal_lifecycle_matches_independent_scenario_gold_and_round_trips() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        suite = generate_trace_lifecycle_suite(root / "archive")
        archive = ArchiveStore(root / "archive")
        observed_states = set()
        observed_actions = set()
        for prefix in suite:
            original = prefix.graph.to_dict()
            view = analyze_goal_lifecycle(
                prefix.graph,
                prefix.goal_context,
                archive_reader=archive.get,
            )
            assert {item.event_id: item.state for item in view.records} == dict(
                prefix.lifecycle_gold_by_event
            )
            assert GoalLifecycleView.from_dict(view.to_dict()).view_hash == view.view_hash
            assert prefix.graph.to_dict() == original
            observed_states.update(item.state for item in view.records)
            observed_actions.update(item.action for item in view.spans)
        assert observed_states == set(GoalLifecycleState)
        assert observed_actions == set(ProjectionAction)


def test_mixed_protocol_span_fails_closed_to_keep_raw() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        prefix = generate_trace_lifecycle_suite(root / "archive")[0]
        old_result = next(
            node
            for node in prefix.graph.nodes.values()
            if node.node_id.endswith("failed-attempt:result")
        )
        old_result.metadata["uncertain"] = True
        view = analyze_goal_lifecycle(
            prefix.graph,
            prefix.goal_context,
            archive_reader=ArchiveStore(root / "archive").get,
        )
        record = view.record_map()[old_result.node_id]
        assert record.state == GoalLifecycleState.UNCERTAIN
        assert view.span_map()[record.protocol_span_id].action == ProjectionAction.KEEP_RAW


def test_missing_archive_reader_defaults_tool_history_to_uncertain() -> None:
    with tempfile.TemporaryDirectory() as directory:
        prefix = generate_trace_lifecycle_suite(Path(directory) / "archive")[0]
        view = analyze_goal_lifecycle(prefix.graph, prefix.goal_context)
        assert view.uncertainty_reasons
        assert any(item.state == GoalLifecycleState.UNCERTAIN for item in view.records)
