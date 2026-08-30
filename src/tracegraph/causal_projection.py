"""Budgeted minimal causal-subgraph recovery for Phase 6."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Sequence
from typing import Any

from .goal_lifecycle import GoalLifecycleState, GoalLifecycleView
from .graph import TraceGraph
from .reactivation import AnchorCandidate, ReactivationResult, ReactivationTrigger
from .schema import EdgeType


CAUSAL_EDGE_TYPES = frozenset(
    {
        EdgeType.FAILED_WITH,
        EdgeType.PRODUCES,
        EdgeType.RETRIES,
        EdgeType.RETRIED_BY,
        EdgeType.RESOLVES,
        EdgeType.RESOLVED_BY,
        EdgeType.SUPERSEDES,
        EdgeType.SUPERSEDED_BY,
        EdgeType.USES,
        EdgeType.SUPPORTS,
        EdgeType.BLOCKS,
        EdgeType.PROVIDES_INPUT,
        EdgeType.LEADS_TO,
    }
)


def _chain_id(graph: TraceGraph, event_id: str) -> str | None:
    value = graph.nodes[event_id].metadata.get("causal_chain_id")
    return str(value) if value else None


def _causal_distances(
    graph: TraceGraph,
    anchors: Sequence[str],
    visible_ids: set[str],
) -> tuple[dict[str, int], tuple[dict[str, Any], ...]]:
    distances = {event_id: 0 for event_id in anchors if event_id in visible_ids}
    anchor_chains = {_chain_id(graph, event_id) for event_id in distances}
    anchor_chains.discard(None)
    queue = deque(sorted(distances))
    records: list[dict[str, Any]] = []
    adjacency: dict[str, list[tuple[str, EdgeType, str]]] = defaultdict(list)
    for edge in graph.edges.values():
        if edge.edge_type not in CAUSAL_EDGE_TYPES:
            continue
        if edge.source not in visible_ids or edge.target not in visible_ids:
            continue
        adjacency[edge.source].append((edge.target, edge.edge_type, edge.edge_id))
        adjacency[edge.target].append((edge.source, edge.edge_type, edge.edge_id))
    while queue:
        current = queue.popleft()
        for neighbor, edge_type, edge_id in sorted(
            adjacency.get(current, ()), key=lambda item: (item[0], item[1].value, item[2])
        ):
            neighbor_chain = _chain_id(graph, neighbor)
            if anchor_chains and neighbor_chain not in anchor_chains:
                continue
            if neighbor in distances:
                continue
            distances[neighbor] = distances[current] + 1
            records.append(
                {
                    "edge_id": edge_id,
                    "edge_type": edge_type.value,
                    "from_event_id": current,
                    "to_event_id": neighbor,
                    "distance": distances[neighbor],
                }
            )
            queue.append(neighbor)
    return distances, tuple(records)


def causal_reactivation(
    event_graph: TraceGraph,
    lifecycle: GoalLifecycleView,
    trigger: ReactivationTrigger,
    candidates: Sequence[AnchorCandidate],
    *,
    token_budget: int = 1024,
) -> ReactivationResult:
    """Recover causal neighbors and complete protocol spans under a fixed budget."""

    if token_budget <= 0:
        raise ValueError("token_budget must be positive")
    if not trigger.active:
        return ReactivationResult(
            retrieval_mode="causal",
            trigger=trigger,
            candidates=tuple(candidates),
            selected_anchor_ids=(),
            injected_event_ids=(),
            protocol_span_ids=(),
            injected_tokens=0,
            current_fact_ids=(),
            historical_fact_ids=(),
            abstention_reason="no_reactivation_trigger",
        )
    if not candidates:
        return ReactivationResult(
            retrieval_mode="causal",
            trigger=trigger,
            candidates=(),
            selected_anchor_ids=(),
            injected_event_ids=(),
            protocol_span_ids=(),
            injected_tokens=0,
            current_fact_ids=(),
            historical_fact_ids=(),
            abstention_reason="no_matching_anchor",
        )

    top_score = candidates[0].score
    anchors = tuple(item.event_id for item in candidates if item.score == top_score)
    visible_ids = set(lifecycle.record_map())
    distances, closure_records = _causal_distances(event_graph, anchors, visible_ids)
    record_map = lifecycle.record_map()
    span_map = lifecycle.span_map()
    span_distances: dict[str, int] = {}
    for event_id, distance in distances.items():
        span_id = record_map[event_id].protocol_span_id
        span_distances[span_id] = min(distance, span_distances.get(span_id, distance))
    anchor_spans = {record_map[event_id].protocol_span_id for event_id in anchors}
    ranked_spans = sorted(
        span_distances,
        key=lambda span_id: (
            0 if span_id in anchor_spans else 1,
            span_distances[span_id],
            min(event_graph.nodes[item].step_id for item in span_map[span_id].event_ids),
            span_id,
        ),
    )
    injected: set[str] = set()
    selected_spans: list[str] = []
    spent = 0
    for span_id in ranked_spans:
        span = span_map[span_id]
        if spent + span.reactivation_token_count > token_budget:
            continue
        selected_spans.append(span_id)
        injected.update(span.event_ids)
        spent += span.reactivation_token_count
    if not set(anchors).issubset(injected):
        return ReactivationResult(
            retrieval_mode="causal",
            trigger=trigger,
            candidates=tuple(candidates),
            selected_anchor_ids=(),
            injected_event_ids=(),
            protocol_span_ids=(),
            injected_tokens=0,
            current_fact_ids=(),
            historical_fact_ids=(),
            omitted_event_ids=tuple(distances),
            abstention_reason="anchor_protocol_span_exceeds_budget",
            closure_records=closure_records,
        )
    current = tuple(
        event_id
        for event_id in injected
        if record_map[event_id].state
        in {GoalLifecycleState.ACTIVE, GoalLifecycleState.PINNED}
    )
    historical = tuple(event_id for event_id in injected if event_id not in current)
    return ReactivationResult(
        retrieval_mode="causal",
        trigger=trigger,
        candidates=tuple(candidates),
        selected_anchor_ids=anchors,
        injected_event_ids=tuple(injected),
        protocol_span_ids=tuple(selected_spans),
        injected_tokens=spent,
        current_fact_ids=current,
        historical_fact_ids=historical,
        omitted_event_ids=tuple(set(distances).difference(injected)),
        closure_records=closure_records,
    )
