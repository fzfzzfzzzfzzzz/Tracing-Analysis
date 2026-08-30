from __future__ import annotations

import tempfile
from pathlib import Path

from tracegraph.archive import ArchiveStore
from tracegraph.causal_projection import causal_reactivation
from tracegraph.goal_lifecycle import analyze_goal_lifecycle
from tracegraph.phase6_scenarios import generate_trace_lifecycle_suite
from tracegraph.reactivation import (
    ReactivationResult,
    ReactivationTriggerType,
    detect_reactivation_trigger,
    flat_reactivation,
    retrieve_anchor_candidates,
)


def test_explicit_triggers_and_distractor_boundary() -> None:
    history = detect_reactivation_trigger(
        {"text": "Why did this fail previously?", "historical_intent": True}
    )
    assert ReactivationTriggerType.HISTORICAL_QUERY in history.trigger_types
    resume = detect_reactivation_trigger({"text": "resume", "resume_goal_id": "goal-a"})
    assert ReactivationTriggerType.GOAL_RESUME in resume.trigger_types
    failure = detect_reactivation_trigger(
        {"text": "failed", "error_signature": "shell_syntax_error"}
    )
    assert ReactivationTriggerType.FAILURE_RECURRENCE in failure.trigger_types
    distractor = detect_reactivation_trigger(
        {"text": "Use shell-script.ps1 for a new operation", "referenced_entities": ["shell-script.ps1"]}
    )
    assert not distractor.active


def test_flat_and_causal_use_same_anchor_but_different_closure() -> None:
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
        candidates = retrieve_anchor_candidates(prefix.graph, lifecycle, trigger)
        flat = flat_reactivation(prefix.graph, lifecycle, trigger, candidates)
        causal = causal_reactivation(prefix.graph, lifecycle, trigger, candidates)
        assert flat.selected_anchor_ids[0] in causal.selected_anchor_ids
        assert set(flat.injected_event_ids) < set(causal.injected_event_ids)
        assert set(fork.required_subgraph_event_ids).issubset(causal.injected_event_ids)
        assert causal.injected_tokens <= 1024
        assert ReactivationResult.from_dict(causal.to_dict()).result_hash == causal.result_hash


def test_causal_reactivation_abstains_when_budget_cannot_fit_anchor_span() -> None:
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
        candidates = retrieve_anchor_candidates(prefix.graph, lifecycle, trigger)
        result = causal_reactivation(
            prefix.graph, lifecycle, trigger, candidates, token_budget=1
        )
        assert not result.injected_event_ids
        assert result.abstention_reason == "anchor_protocol_span_exceeds_budget"
