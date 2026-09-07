from __future__ import annotations

import tempfile
from pathlib import Path

from tracegraph.archive import ArchiveStore
from tracegraph.causal_projection import causal_reactivation
from tracegraph.goal_lifecycle import analyze_goal_lifecycle
from tracegraph.phase6.scenarios import generate_trace_lifecycle_suite
from tracegraph.reactivation import detect_reactivation_trigger, retrieve_anchor_candidates


def test_causal_projection_excludes_sibling_chain() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        prefix = generate_trace_lifecycle_suite(root / "archive")[0]
        fork = next(item for item in prefix.forks if item.fork_type == "REACTIVATE")
        lifecycle = analyze_goal_lifecycle(
            prefix.graph,
            prefix.goal_context,
            archive_reader=ArchiveStore(root / "archive").get,
        )
        trigger = detect_reactivation_trigger(fork.request_payload())
        result = causal_reactivation(
            prefix.graph,
            lifecycle,
            trigger,
            retrieve_anchor_candidates(prefix.graph, lifecycle, trigger),
        )
        assert all("ephemeral-check" not in item for item in result.injected_event_ids)
        assert result.closure_records


def test_superseded_fact_is_labeled_historical_not_current() -> None:
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        prefix = next(
            item
            for item in generate_trace_lifecycle_suite(root / "archive")
            if item.scenario_family == "F5_state_supersession"
        )
        fork = next(item for item in prefix.forks if item.fork_type == "REACTIVATE")
        lifecycle = analyze_goal_lifecycle(
            prefix.graph,
            prefix.goal_context,
            archive_reader=ArchiveStore(root / "archive").get,
        )
        trigger = detect_reactivation_trigger(fork.request_payload())
        result = causal_reactivation(
            prefix.graph,
            lifecycle,
            trigger,
            retrieve_anchor_candidates(prefix.graph, lifecycle, trigger),
        )
        old = set(fork.forbidden_current_fact_ids)
        assert old.issubset(result.historical_fact_ids)
        assert old.isdisjoint(result.current_fact_ids)
