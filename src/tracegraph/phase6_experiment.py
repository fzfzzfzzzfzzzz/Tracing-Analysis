"""Local, zero-provider Phase 6 manager projections and deterministic scoring."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from .archive import ArchiveStore
from .capture import estimate_tokens
from .causal_projection import causal_reactivation
from .decision_state import stable_digest
from .goal_lifecycle import (
    GoalContext,
    GoalLifecycleState,
    ProjectionAction,
    analyze_goal_lifecycle,
)
from .message_protocol import close_message_protocol
from .phase6_scenarios import ScenarioFork, ScenarioPrefix
from .reactivation import (
    ReactivationResult,
    ReactivationTrigger,
    ReactivationTriggerType,
    detect_reactivation_trigger,
    flat_reactivation,
    retrieve_anchor_candidates,
)
from .schema import NodeType


MANAGER_IDS = (
    "M0_full_history",
    "M1_recent_masking",
    "M2_flat_lexical_archive",
    "M3_lifecycle_eviction_only",
    "M4_lifecycle_flat_reactivation",
    "M5_lifecycle_causal_reactivation",
)
ABLATION_IDS = (
    "A1_without_goal_membership",
    "A2_without_causal_edge_expansion",
    "A3_without_superseded_current_distinction",
    "A4_without_failure_reactivation",
    "A5_without_protocol_closure",
    "A6_every_turn_open_retrieval",
    "A7_archive_handles_only",
)
ALL_LOCAL_METHODS = (*MANAGER_IDS, *ABLATION_IDS)


def _event_tokens(prefix: ScenarioPrefix, event_ids: set[str]) -> int:
    return sum(
        prefix.graph.nodes[event_id].token_count
        or estimate_tokens(prefix.graph.nodes[event_id].content)
        for event_id in event_ids
    )


def _fixed_tokens(prefix: ScenarioPrefix) -> int:
    return estimate_tokens(
        {
            "system": [
                "Preserve policy, confirmation, receipts, current facts, and protocol closure."
            ],
            "tools": prefix.tool_schemas,
        }
    )


def _recent_tool_spans(prefix: ScenarioPrefix, lifecycle: Any, count: int = 2) -> set[str]:
    spans = []
    for span in lifecycle.spans:
        nodes = [prefix.graph.nodes[item] for item in span.event_ids]
        if not any(node.node_type in {NodeType.TOOL_CALL, NodeType.MCP_CALL} for node in nodes):
            continue
        spans.append((max(node.step_id for node in nodes), span.span_id))
    return {span_id for _, span_id in sorted(spans)[-count:]}


def _empty_reactivation(trigger: ReactivationTrigger, mode: str, reason: str) -> ReactivationResult:
    return ReactivationResult(
        retrieval_mode=mode,
        trigger=trigger,
        candidates=(),
        selected_anchor_ids=(),
        injected_event_ids=(),
        protocol_span_ids=(),
        injected_tokens=0,
        current_fact_ids=(),
        historical_fact_ids=(),
        abstention_reason=reason,
    )


def _force_open_trigger(trigger: ReactivationTrigger) -> ReactivationTrigger:
    if trigger.active:
        return trigger
    return replace(
        trigger,
        trigger_types=(ReactivationTriggerType.EXPLICIT_REFERENCE,),
    )


def _suppress_failure_trigger(trigger: ReactivationTrigger) -> ReactivationTrigger:
    remaining = tuple(
        item
        for item in trigger.trigger_types
        if item != ReactivationTriggerType.FAILURE_RECURRENCE
    )
    return replace(
        trigger,
        trigger_types=remaining,
        error_signature=None,
    )


def _close_protocol(
    prefix: ScenarioPrefix,
    selected_event_ids: set[str],
    *,
    enabled: bool,
) -> tuple[set[str], bool, tuple[dict[str, Any], ...], tuple[str, ...]]:
    ordinal_to_events: dict[int, set[str]] = {}
    selected_ordinals: set[int] = set()
    for event_id, node in prefix.graph.nodes.items():
        ordinal = node.metadata.get("source_message_ordinal")
        if not isinstance(ordinal, int):
            continue
        ordinal_to_events.setdefault(ordinal, set()).add(event_id)
        if event_id in selected_event_ids:
            selected_ordinals.add(ordinal)
    closed = close_message_protocol(prefix.messages, selected_ordinals)
    if not enabled:
        additions_required = set(closed.ordinals).difference(selected_ordinals)
        valid = not closed.errors and not additions_required
        return selected_event_ids, valid, (), tuple(sorted(closed.errors))
    expanded = set(selected_event_ids)
    for ordinal in closed.ordinals:
        expanded.update(ordinal_to_events.get(ordinal, ()))
    additions = tuple(
        {
            "ordinal": item.ordinal,
            "reason": item.reason,
            "trigger_ordinal": item.trigger_ordinal,
            "call_id": item.call_id,
        }
        for item in closed.additions
    )
    return expanded, closed.valid, additions, tuple(sorted(closed.errors))


def project_manager(
    prefix: ScenarioPrefix,
    request: Mapping[str, Any],
    manager_id: str,
    *,
    archive: ArchiveStore,
    reactivation_budget: int = 1024,
) -> dict[str, Any]:
    """Project one manager using no fork label and no gold fields."""

    if manager_id not in ALL_LOCAL_METHODS:
        raise ValueError(f"unsupported Phase 6 manager: {manager_id}")
    goal_context = prefix.goal_context
    if manager_id == "A1_without_goal_membership":
        goal_context = GoalContext(
            current_goal_id="goal-membership-ablated",
            referenced_entities=prefix.goal_context.referenced_entities,
        )
    lifecycle = analyze_goal_lifecycle(
        prefix.graph,
        goal_context,
        archive_reader=archive.get,
    )
    records = lifecycle.record_map()
    spans = lifecycle.span_map()
    all_event_ids = set(records)
    raw: set[str] = set()
    represented: set[str] = set()
    masked: set[str] = set()
    evicted: set[str] = set()
    visible_span_ids: set[str] = set()
    evicted_span_ids: set[str] = set()
    compact_tokens = 0

    if manager_id == "M0_full_history":
        raw = set(all_event_ids)
        visible_span_ids = set(spans)
        projection_family = "full"
    elif manager_id in {"M1_recent_masking", "M2_flat_lexical_archive"}:
        recent = _recent_tool_spans(prefix, lifecycle, count=2)
        projection_family = "recent_masking"
        for span in lifecycle.spans:
            protected = bool(
                set(span.state_summary).intersection(
                    {
                        GoalLifecycleState.PINNED,
                        GoalLifecycleState.ACTIVE,
                        GoalLifecycleState.UNCERTAIN,
                    }
                )
            )
            if protected or span.span_id in recent:
                raw.update(span.event_ids)
                visible_span_ids.add(span.span_id)
            else:
                evicted.update(span.event_ids)
                evicted_span_ids.add(span.span_id)
                masked.update(span.event_ids)
                compact_tokens += 4 * len(span.event_ids)
    else:
        projection_family = "lifecycle"
        for span in lifecycle.spans:
            if span.action == ProjectionAction.KEEP_RAW:
                raw.update(span.event_ids)
                visible_span_ids.add(span.span_id)
            elif span.action == ProjectionAction.KEEP_STRUCTURED:
                represented.update(span.event_ids)
                visible_span_ids.add(span.span_id)
                compact_tokens += 16 * len(span.event_ids)
            elif span.action == ProjectionAction.COMPRESS_TO_GUARD:
                represented.update(
                    event_id
                    for event_id in span.event_ids
                    if prefix.graph.nodes[event_id].metadata.get("guard_text")
                )
                evicted.update(span.event_ids)
                evicted_span_ids.add(span.span_id)
                compact_tokens += estimate_tokens(span.guard_text or "historical guard")
            elif span.action == ProjectionAction.ARCHIVE_WITH_HANDLE:
                evicted.update(span.event_ids)
                evicted_span_ids.add(span.span_id)
                compact_tokens += 8
            else:
                evicted.update(span.event_ids)
                evicted_span_ids.add(span.span_id)

    base_raw = set(raw)
    base_represented = set(represented)
    base_evicted = all_event_ids.difference(base_raw).difference(base_represented)
    trigger = detect_reactivation_trigger(request)
    if manager_id == "A4_without_failure_reactivation":
        trigger = _suppress_failure_trigger(trigger)
    if manager_id == "A6_every_turn_open_retrieval":
        trigger = _force_open_trigger(trigger)
    candidates = retrieve_anchor_candidates(prefix.graph, lifecycle, trigger)
    if manager_id in {"M2_flat_lexical_archive", "M4_lifecycle_flat_reactivation", "A2_without_causal_edge_expansion"}:
        reactivation = flat_reactivation(
            prefix.graph,
            lifecycle,
            trigger,
            candidates,
            token_budget=reactivation_budget,
        )
    elif manager_id in {
        "M5_lifecycle_causal_reactivation",
        "A1_without_goal_membership",
        "A3_without_superseded_current_distinction",
        "A4_without_failure_reactivation",
        "A5_without_protocol_closure",
        "A6_every_turn_open_retrieval",
    }:
        reactivation = causal_reactivation(
            prefix.graph,
            lifecycle,
            trigger,
            candidates,
            token_budget=reactivation_budget,
        )
    elif manager_id == "A7_archive_handles_only":
        reactivation = _empty_reactivation(trigger, "handles_only", "raw_dereference_disabled")
    else:
        reason = (
            "reactivation_disabled"
            if manager_id == "M3_lifecycle_eviction_only"
            else "manager_has_no_archive_retrieval"
        )
        reactivation = _empty_reactivation(trigger, "none", reason)

    injected = set(reactivation.injected_event_ids)
    selected_before_closure = set(raw).union(injected)
    closed_ids, protocol_valid, closure_additions, protocol_errors = _close_protocol(
        prefix,
        selected_before_closure,
        enabled=manager_id != "A5_without_protocol_closure",
    )
    closure_added_ids = closed_ids.difference(selected_before_closure)
    raw.update(closure_added_ids)
    injected.update(
        event_id
        for event_id in closure_added_ids
        if records[event_id].state
        in {
            GoalLifecycleState.DORMANT,
            GoalLifecycleState.SUPERSEDED,
            GoalLifecycleState.EPHEMERAL,
        }
    )
    evicted.difference_update(closed_ids)
    for event_id in closed_ids:
        visible_span_ids.add(records[event_id].protocol_span_id)
        evicted_span_ids.discard(records[event_id].protocol_span_id)

    active_raw_tokens = _event_tokens(prefix, raw.difference(injected))
    reactivation_tokens = reactivation.injected_tokens + _event_tokens(
        prefix, closure_added_ids.intersection(injected)
    )
    fixed_tokens = _fixed_tokens(prefix)
    active_context_tokens = (
        fixed_tokens + active_raw_tokens + compact_tokens + reactivation_tokens
    )
    gross_evicted_tokens = _event_tokens(prefix, evicted)
    if projection_family == "lifecycle":
        projection_hash_family = "lifecycle_v1"
    elif projection_family == "recent_masking":
        projection_hash_family = "recent_masking_v1"
    else:
        projection_hash_family = "full_history_v1"
    eviction_hash = stable_digest(
        {
            "prefix_event_hash": lifecycle.prefix_event_hash,
            "projection_family": projection_hash_family,
            "raw_before_reactivation": sorted(base_raw),
            "represented_before_reactivation": sorted(base_represented),
            "evicted_before_reactivation": sorted(base_evicted),
        }
    )
    current_fact_ids = {
        event_id
        for event_id in closed_ids.union(represented)
        if records[event_id].state in {GoalLifecycleState.ACTIVE, GoalLifecycleState.PINNED}
    }
    historical_fact_ids = closed_ids.union(represented).difference(current_fact_ids)
    if manager_id == "A3_without_superseded_current_distinction":
        current_fact_ids.update(
            event_id
            for event_id in injected
            if records[event_id].state == GoalLifecycleState.SUPERSEDED
        )
        historical_fact_ids.difference_update(current_fact_ids)
    available = closed_ids.union(represented)
    return {
        "schema_version": "phase6_manager_projection_v1",
        "manager_id": manager_id,
        "prefix_id": prefix.prefix_id,
        "request_hash": stable_digest(dict(request)),
        "lifecycle_hash": lifecycle.view_hash,
        "prefix_event_hash": lifecycle.prefix_event_hash,
        "eviction_hash": eviction_hash,
        "raw_event_ids": sorted(raw),
        "represented_event_ids": sorted(represented),
        "masked_event_ids": sorted(masked),
        "evicted_event_ids": sorted(evicted),
        "injected_event_ids": sorted(injected),
        "available_event_ids": sorted(available),
        "current_fact_ids": sorted(current_fact_ids),
        "historical_fact_ids": sorted(historical_fact_ids),
        "visible_span_ids": sorted(visible_span_ids),
        "evicted_span_ids": sorted(evicted_span_ids),
        "lifecycle": lifecycle.to_dict(),
        "trigger": trigger.to_dict(),
        "reactivation": reactivation.to_dict(),
        "protocol_closure_additions": list(closure_additions),
        "protocol_valid": protocol_valid,
        "protocol_errors": list(protocol_errors),
        "fixed_tokens": fixed_tokens,
        "active_raw_tokens": active_raw_tokens,
        "compact_tokens": compact_tokens,
        "reactivation_tokens": reactivation_tokens,
        "active_context_tokens": active_context_tokens,
        "serialized_episode_tokens": active_context_tokens,
        "gross_evicted_tokens": gross_evicted_tokens,
        "archive_integrity": not any(
            "archive_unavailable_or_unverified" in item
            for item in lifecycle.uncertainty_reasons
        ),
        "provider_requests": 0,
        "synthetic": True,
    }


def score_manager_projection(
    projection: Mapping[str, Any],
    prefix: ScenarioPrefix,
    fork: ScenarioFork,
) -> dict[str, Any]:
    """Score a completed projection; this is the only function allowed gold access."""

    available = set(map(str, projection.get("available_event_ids", ())))
    injected = set(map(str, projection.get("injected_event_ids", ())))
    evicted = set(map(str, projection.get("evicted_event_ids", ())))
    current_facts = set(map(str, projection.get("current_fact_ids", ())))
    gold = prefix.lifecycle_gold_by_event
    protected = {
        event_id
        for event_id, state in gold.items()
        if state in {GoalLifecycleState.PINNED, GoalLifecycleState.ACTIVE}
    }
    pinned = {
        event_id
        for event_id, state in gold.items()
        if state == GoalLifecycleState.PINNED
    }
    removable = {
        event_id
        for event_id, state in gold.items()
        if state
        in {
            GoalLifecycleState.DORMANT,
            GoalLifecycleState.SUPERSEDED,
            GoalLifecycleState.EPHEMERAL,
        }
    }
    unsafe = evicted.intersection(protected)
    correctly_evicted = evicted.intersection(removable)
    required = set(fork.required_subgraph_event_ids)
    anchors = set(fork.required_anchor_ids)
    selected_anchors = set(
        projection.get("reactivation", {}).get("selected_anchor_ids", ())
    )
    recalled = available.intersection(required)
    injected_required = injected.intersection(required)
    stale_current = current_facts.intersection(fork.forbidden_current_fact_ids)
    required_recall = len(recalled) / len(required) if required else 1.0
    required_precision = (
        len(injected_required) / len(injected) if injected else (1.0 if not required else 0.0)
    )
    anchor_accuracy = (
        len(selected_anchors.intersection(anchors)) / len(anchors)
        if anchors
        else (1.0 if not selected_anchors else 0.0)
    )
    current_preserved = protected.issubset(available)
    if fork.fork_type == "REACTIVATE":
        task_success = required.issubset(available) and not stale_current
    else:
        task_success = current_preserved and not stale_current
    historical_success = (
        bool(task_success) if fork.fork_type == "REACTIVATE" else None
    )
    distractor_false = bool(injected) if fork.fork_type == "DISTRACTOR" else False
    reacquisition_needed = fork.fork_type == "REACTIVATE" and not task_success
    node_unsafe_rate = len(unsafe) / len(evicted) if evicted else 0.0
    evicted_tokens = sum(prefix.graph.nodes[item].token_count for item in evicted)
    unsafe_tokens = sum(prefix.graph.nodes[item].token_count for item in unsafe)
    removable_tokens = sum(prefix.graph.nodes[item].token_count for item in removable)
    correctly_evicted_tokens = sum(
        prefix.graph.nodes[item].token_count for item in correctly_evicted
    )
    result = dict(projection)
    result.update(
        {
            "fork_id": fork.fork_id,
            "fork_type": fork.fork_type,
            "current_task_success": bool(task_success),
            "final_environment_state_match": bool(task_success),
            "historical_question_accuracy": historical_success,
            "goal_resumption_success": (
                historical_success
                if prefix.scenario_family == "F3_goal_resume"
                and fork.fork_type == "REACTIVATE"
                else None
            ),
            "repeated_failure_avoidance": (
                historical_success
                if prefix.scenario_family
                in {"F1_shell_switch", "F2_failed_approach"}
                and fork.fork_type == "REACTIVATE"
                else None
            ),
            "duplicate_side_effect_count": 0 if current_preserved else int(
                prefix.scenario_family == "F6_side_effect_audit"
            ),
            "repeated_failed_action_count": int(
                prefix.scenario_family
                in {"F1_shell_switch", "F2_failed_approach"}
                and fork.fork_type == "REACTIVATE"
                and not task_success
            ),
            "superseded_as_current_error": bool(stale_current),
            "unsafe_eviction_node_rate": node_unsafe_rate,
            "unsafe_eviction_token_rate": (
                unsafe_tokens / evicted_tokens if evicted_tokens else 0.0
            ),
            "dormant_eviction_node_coverage": (
                len(correctly_evicted) / len(removable) if removable else 1.0
            ),
            "dormant_eviction_token_coverage": (
                correctly_evicted_tokens / removable_tokens if removable_tokens else 1.0
            ),
            "required_subgraph_recall": required_recall,
            "required_subgraph_precision": required_precision,
            "anchor_accuracy": anchor_accuracy,
            "distractor_false_reactivation": distractor_false,
            "pinned_evidence_recall": (
                len(pinned.intersection(available)) / len(pinned) if pinned else 1.0
            ),
            "stale_current_label_accuracy": not stale_current,
            "reacquisition_tool_calls": (
                fork.reacquisition_calls if reacquisition_needed else 0
            ),
            "reacquisition_observation_tokens": (
                fork.reacquisition_observation_tokens if reacquisition_needed else 0
            ),
        }
    )
    result["manager_output_hash"] = stable_digest(result)
    return result


def run_local_method(
    prefix: ScenarioPrefix,
    fork: ScenarioFork,
    manager_id: str,
    *,
    archive_root: str | Path,
    reactivation_budget: int = 1024,
) -> dict[str, Any]:
    archive = ArchiveStore(archive_root)
    projection = project_manager(
        prefix,
        fork.request_payload(),
        manager_id,
        archive=archive,
        reactivation_budget=reactivation_budget,
    )
    return score_manager_projection(projection, prefix, fork)
