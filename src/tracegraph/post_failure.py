"""Fixed-horizon, action-normalized metrics after actionable tool failures."""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from typing import Any, Mapping, Sequence

from .failure_cards import classify_failure
from .graph import TraceGraph
from .schema import EdgeType, FailureClass, Node, NodeType, SemanticOutcome


POST_FAILURE_SCHEMA_VERSION = "1.0"
_NEGATIVE_OUTCOMES = {
    SemanticOutcome.NEGATIVE.value,
    SemanticOutcome.POLICY_DENIED.value,
    SemanticOutcome.TEST_FAILED.value,
}
_ELIGIBLE_FAILURE_CLASSES = {
    FailureClass.ACTIONABLE,
    FailureClass.POLICY_DENIED,
    FailureClass.MALFORMED,
}


def _is_negative(node: Node) -> bool:
    return (
        node.node_type == NodeType.ERROR
        or str(node.metadata.get("semantic_outcome") or "") in _NEGATIVE_OUTCOMES
    )


def _producer(graph: TraceGraph, failure: Node) -> Node | None:
    edges = graph.incoming(failure.node_id, EdgeType.FAILED_WITH)
    edges += graph.incoming(failure.node_id, EdgeType.PRODUCES)
    return graph.nodes[edges[-1].source] if edges else None


def _results_for_action(graph: TraceGraph, action: Node) -> list[Node]:
    edges = graph.outgoing(action.node_id, EdgeType.FAILED_WITH)
    edges += graph.outgoing(action.node_id, EdgeType.PRODUCES)
    return [graph.nodes[edge.target] for edge in edges]


def _assistant_messages(
    messages: Sequence[Mapping[str, Any]],
) -> dict[int, Mapping[str, Any]]:
    return {
        ordinal: message
        for ordinal, message in enumerate(messages, start=1)
        if str(message.get("role") or "").lower() in {"assistant", "agent"}
    }


def _align_context_views(
    messages: Sequence[Mapping[str, Any]],
    context_views: Sequence[Mapping[str, Any]],
) -> tuple[dict[int, Mapping[str, Any]], dict[str, int]]:
    """Map each persisted prompt view to the next assistant response ordinal."""

    assistants = sorted(_assistant_messages(messages))
    mapping: dict[int, Mapping[str, Any]] = {}
    last_response = 0
    prefix_aligned = order_fallback = 0
    for index, view in enumerate(context_views):
        metadata = view.get("metadata") if isinstance(view, Mapping) else None
        metadata = metadata if isinstance(metadata, Mapping) else {}
        ordinals = [
            int(value)
            for value in metadata.get("selected_message_ordinals") or []
            if isinstance(value, int)
        ]
        prefix_end = max(ordinals, default=last_response)
        candidates = [
            ordinal
            for ordinal in assistants
            if ordinal > prefix_end and ordinal > last_response and ordinal not in mapping
        ]
        if candidates:
            response_ordinal = candidates[0]
            prefix_aligned += 1
        else:
            remaining = [
                ordinal
                for ordinal in assistants
                if ordinal > last_response and ordinal not in mapping
            ]
            if not remaining:
                continue
            response_ordinal = remaining[0]
            order_fallback += 1
        mapping[response_ordinal] = view
        last_response = response_ordinal
    return mapping, {
        "assistant_messages": len(assistants),
        "context_views": len(context_views),
        "mapped_views": len(mapping),
        "prefix_aligned_views": prefix_aligned,
        "order_fallback_views": order_fallback,
    }


def _usage_value(usage: Mapping[str, Any], keys: Sequence[str]) -> float | None:
    for key in keys:
        value = usage.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _window_usage(
    action_ordinals: Sequence[int],
    messages: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    by_ordinal = _assistant_messages(messages)
    unique_ordinals = sorted(set(action_ordinals))
    input_values: list[float] = []
    output_values: list[float] = []
    covered_messages = 0
    for ordinal in unique_ordinals:
        message = by_ordinal.get(ordinal, {})
        usage = message.get("usage") if isinstance(message, Mapping) else None
        if not isinstance(usage, Mapping):
            continue
        input_value = _usage_value(usage, ("prompt_tokens", "input_tokens", "input_token_count"))
        output_value = _usage_value(
            usage,
            ("completion_tokens", "output_tokens", "output_token_count"),
        )
        if input_value is not None or output_value is not None:
            covered_messages += 1
        if input_value is not None:
            input_values.append(input_value)
        if output_value is not None:
            output_values.append(output_value)
    return {
        "post_failure_provider_input_tokens": (
            sum(input_values)
            if unique_ordinals and len(input_values) == len(unique_ordinals)
            else None
        ),
        "post_failure_provider_output_tokens": (
            sum(output_values)
            if unique_ordinals and len(output_values) == len(unique_ordinals)
            else None
        ),
        "provider_usage_messages": covered_messages,
        "provider_usage_expected_messages": len(unique_ordinals),
    }


def _view_metrics(
    failure: Node,
    producer: Node | None,
    action_ordinals: Sequence[int],
    view_by_response: Mapping[int, Mapping[str, Any]],
) -> dict[str, Any]:
    target_ids = {failure.node_id}
    if producer is not None:
        target_ids.add(producer.node_id)
    target_operation_key = str(producer.metadata.get("operation_key") or "") if producer else ""
    raw_ordinals = {
        int(value)
        for value in (
            failure.metadata.get("source_message_ordinal"),
            producer.metadata.get("source_message_ordinal") if producer else None,
        )
        if isinstance(value, int)
    }
    any_card_visible = target_card_visible = 0
    any_card_tokens = target_card_tokens = 0
    ordinal_overlap_views = explicit_raw_replay_views = raw_replay_observable_views = 0
    aligned = 0
    for ordinal in sorted(set(action_ordinals)):
        view = view_by_response.get(ordinal)
        if not isinstance(view, Mapping):
            continue
        aligned += 1
        metadata = view.get("metadata")
        metadata = metadata if isinstance(metadata, Mapping) else {}
        count = int(metadata.get("failure_card_count") or 0)
        tokens = int(metadata.get("failure_card_tokens") or 0)
        any_card_visible += int(count > 0)
        any_card_tokens += tokens
        target_items = []
        for item in view.get("items") or []:
            if not isinstance(item, Mapping):
                continue
            source_ids = {str(value) for value in item.get("source_node_ids") or []}
            reason = str(item.get("reason") or "")
            content = item.get("content")
            item_scope = (
                str(content.get("operation_scope") or "") if isinstance(content, Mapping) else ""
            )
            if reason.startswith("failure_card") and (
                source_ids & target_ids
                or (target_operation_key and item_scope == target_operation_key)
            ):
                target_items.append(item)
        if target_items:
            target_card_visible += 1
            target_card_tokens += sum(int(item.get("token_count") or 0) for item in target_items)
        selected_ordinals = {
            int(value)
            for value in metadata.get("selected_message_ordinals") or []
            if isinstance(value, int)
        }
        ordinal_overlap_views += int(bool(raw_ordinals & selected_ordinals))
        if "raw_failure_messages_selected" in metadata:
            raw_replay_observable_views += 1
            explicit_raw_replay_views += int(
                int(metadata.get("raw_failure_messages_selected") or 0) > 0
            )
    return {
        "context_views_aligned": aligned,
        "context_views_expected": len(set(action_ordinals)),
        "any_failure_card_visible_actions": any_card_visible,
        "any_failure_card_tokens": any_card_tokens,
        "target_failure_card_visible_actions": target_card_visible,
        "target_failure_card_tokens": target_card_tokens,
        "target_failure_ordinal_overlap_actions": ordinal_overlap_views,
        "raw_failure_replay_observable_actions": raw_replay_observable_views,
        "raw_failure_replay_actions": (
            explicit_raw_replay_views
            if aligned > 0 and raw_replay_observable_views == aligned
            else None
        ),
    }


def analyze_post_failure_windows(
    graph: TraceGraph,
    *,
    messages: Sequence[Mapping[str, Any]],
    context_views: Sequence[Mapping[str, Any]] = (),
    horizon: int = 3,
) -> dict[str, Any]:
    """Measure the next ``horizon`` agent tool actions after each eligible failure."""

    if horizon <= 0:
        raise ValueError("horizon must be positive")
    view_by_response, alignment = _align_context_views(messages, context_views)
    actions = [
        node
        for node in graph.find_nodes(node_types={NodeType.TOOL_CALL, NodeType.MCP_CALL})
        if str(node.metadata.get("requestor") or "assistant").lower() not in {"user", "customer"}
    ]
    events: list[dict[str, Any]] = []
    for failure in graph.find_nodes(node_types={NodeType.ERROR, NodeType.OBSERVATION}):
        if not _is_negative(failure):
            continue
        failure_class = classify_failure(failure)
        if failure_class not in _ELIGIBLE_FAILURE_CLASSES:
            continue
        producer = _producer(graph, failure)
        window_actions = [action for action in actions if action.step_id > failure.step_id][
            :horizon
        ]
        action_records: list[dict[str, Any]] = []
        repeated_invalid = admissible_corrections = corrective_retries = 0
        action_ordinals: list[int] = []
        result_ids_by_action: dict[str, set[str]] = {}
        for index, action in enumerate(window_actions, start=1):
            results = _results_for_action(graph, action)
            result_ids_by_action[action.node_id] = {result.node_id for result in results}
            later_negative = any(_is_negative(result) for result in results)
            retry_edges = (
                [
                    edge
                    for edge in graph.outgoing(producer.node_id, EdgeType.RETRIED_BY)
                    if edge.target == action.node_id
                ]
                if producer is not None
                else []
            )
            match_types = {
                str(edge.metadata.get("match_type") or "unknown") for edge in retry_edges
            }
            exact_repeat = bool(retry_edges) and "exact_signature" in match_types
            correction = bool(match_types & {"structural_operation", "argument_completion"})
            admissible = correction and bool(results) and not later_negative
            repeated_invalid += int(exact_repeat and later_negative)
            corrective_retries += int(correction)
            admissible_corrections += int(admissible)
            ordinal = action.metadata.get("source_message_ordinal")
            if isinstance(ordinal, int):
                action_ordinals.append(ordinal)
            action_records.append(
                {
                    "action_index": index,
                    "action_node_id": action.node_id,
                    "step_id": action.step_id,
                    "source_message_ordinal": ordinal,
                    "tool_name": action.metadata.get("tool_name"),
                    "operation_key": action.metadata.get("operation_key"),
                    "retry_match_types": sorted(match_types),
                    "result_node_ids": sorted(result_ids_by_action[action.node_id]),
                    "result_is_negative": later_negative,
                    "repeated_same_invalid_operation": exact_repeat and later_negative,
                    "corrective_retry": correction,
                    "admissible_correction": admissible,
                }
            )

        resolved_targets = {
            edge.target for edge in graph.outgoing(failure.node_id, EdgeType.RESOLVED_BY)
        }
        recovery_action_index = None
        for index, action in enumerate(window_actions, start=1):
            if resolved_targets & (
                {action.node_id} | result_ids_by_action.get(action.node_id, set())
            ):
                recovery_action_index = index
                break
        usage = _window_usage(action_ordinals, messages)
        view_metrics = _view_metrics(failure, producer, action_ordinals, view_by_response)
        action_count = len(window_actions)
        input_tokens = usage["post_failure_provider_input_tokens"]
        output_tokens = usage["post_failure_provider_output_tokens"]
        events.append(
            {
                "schema_version": POST_FAILURE_SCHEMA_VERSION,
                "session_id": graph.session_id,
                "failure_node_id": failure.node_id,
                "producer_node_id": producer.node_id if producer else None,
                "failure_step": failure.step_id,
                "failure_class": failure_class.value,
                "operation_key": (producer.metadata.get("operation_key") if producer else None),
                "horizon": horizon,
                "observed_agent_actions": action_count,
                "window_censored": action_count < horizon,
                "repeated_same_invalid_count": repeated_invalid,
                "repeated_same_invalid": repeated_invalid > 0,
                "corrective_retry_count": corrective_retries,
                "admissible_correction_count": admissible_corrections,
                "admissible_correction": admissible_corrections > 0,
                "resolved_within_window": recovery_action_index is not None,
                "recovery_action_index": recovery_action_index,
                **usage,
                "provider_input_tokens_per_action": (
                    float(input_tokens) / action_count
                    if input_tokens is not None and action_count
                    else None
                ),
                "provider_output_tokens_per_action": (
                    float(output_tokens) / action_count
                    if output_tokens is not None and action_count
                    else None
                ),
                **view_metrics,
                "actions": action_records,
            }
        )

    return {
        "schema_version": POST_FAILURE_SCHEMA_VERSION,
        "session_id": graph.session_id,
        "horizon": horizon,
        "eligible_failure_classes": sorted(item.value for item in _ELIGIBLE_FAILURE_CLASSES),
        "alignment": alignment,
        "events": events,
        "summary": aggregate_post_failure_events(events),
        "interpretation_warning": (
            "Natural-trajectory windows are post-hoc diagnostics; causal estimates "
            "require Card/Remove branches from an identical frozen prefix."
        ),
    }


def _mean(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def aggregate_post_failure_events(
    events: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Aggregate event rows while preserving missing-usage denominators."""

    input_values = [
        float(event["post_failure_provider_input_tokens"])
        for event in events
        if isinstance(event.get("post_failure_provider_input_tokens"), (int, float))
    ]
    output_values = [
        float(event["post_failure_provider_output_tokens"])
        for event in events
        if isinstance(event.get("post_failure_provider_output_tokens"), (int, float))
    ]
    per_action_input = [
        float(event["provider_input_tokens_per_action"])
        for event in events
        if isinstance(event.get("provider_input_tokens_per_action"), (int, float))
    ]
    per_action_output = [
        float(event["provider_output_tokens_per_action"])
        for event in events
        if isinstance(event.get("provider_output_tokens_per_action"), (int, float))
    ]
    recovery = [
        float(event["recovery_action_index"])
        for event in events
        if isinstance(event.get("recovery_action_index"), int)
    ]
    raw_replay_values = [
        int(event["raw_failure_replay_actions"])
        for event in events
        if isinstance(event.get("raw_failure_replay_actions"), int)
    ]
    return {
        "event_count": len(events),
        "failure_class_counts": dict(Counter(str(event.get("failure_class")) for event in events)),
        "events_with_actions": sum(
            int(event.get("observed_agent_actions") or 0) > 0 for event in events
        ),
        "window_censored_events": sum(bool(event.get("window_censored")) for event in events),
        "repeated_same_invalid_events": sum(
            bool(event.get("repeated_same_invalid")) for event in events
        ),
        "admissible_correction_events": sum(
            bool(event.get("admissible_correction")) for event in events
        ),
        "resolved_within_window_events": sum(
            bool(event.get("resolved_within_window")) for event in events
        ),
        "mean_recovery_action_index": _mean(recovery),
        "provider_input_usage_events": len(input_values),
        "provider_output_usage_events": len(output_values),
        "mean_post_failure_provider_input_tokens": _mean(input_values),
        "mean_post_failure_provider_output_tokens": _mean(output_values),
        "mean_provider_input_tokens_per_action": _mean(per_action_input),
        "mean_provider_output_tokens_per_action": _mean(per_action_output),
        "target_failure_card_visible_actions": sum(
            int(event.get("target_failure_card_visible_actions") or 0) for event in events
        ),
        "target_failure_card_tokens": sum(
            int(event.get("target_failure_card_tokens") or 0) for event in events
        ),
        "any_failure_card_visible_actions": sum(
            int(event.get("any_failure_card_visible_actions") or 0) for event in events
        ),
        "any_failure_card_tokens": sum(
            int(event.get("any_failure_card_tokens") or 0) for event in events
        ),
        "raw_failure_replay_observed_events": len(raw_replay_values),
        "raw_failure_replay_actions": (sum(raw_replay_values) if raw_replay_values else None),
        "target_failure_ordinal_overlap_actions": sum(
            int(event.get("target_failure_ordinal_overlap_actions") or 0) for event in events
        ),
        "context_views_aligned": sum(
            int(event.get("context_views_aligned") or 0) for event in events
        ),
        "context_views_expected": sum(
            int(event.get("context_views_expected") or 0) for event in events
        ),
    }


def aggregate_by_condition(
    events: Sequence[Mapping[str, Any]], condition_field: str = "manager"
) -> dict[str, Any]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        grouped[str(event.get(condition_field) or "unknown")].append(event)
    return {
        condition: aggregate_post_failure_events(values)
        for condition, values in sorted(grouped.items())
    }
