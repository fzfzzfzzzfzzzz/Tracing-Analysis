"""Deterministic graph-constrained context selection and causal retrieval."""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from ..capture import estimate_tokens
from ..graph import TraceGraph
from ..schema import (
    EdgeType,
    NodeType,
    RelevanceState,
)
from .span_analysis import _group_spans
from .lifecycle_v4 import _allowed_tools, _classify, _fact, _terms, _tool_name
from .protocol_v4 import _protocol_errors, _synthetic_messages
from .types import (
    POLICY_API_VERSION,
    ContextPlan,
    MemorySnapshot,
    MemorySpan,
    stable_value_hash,
)


_CALL_TYPES = {NodeType.TOOL_CALL, NodeType.MCP_CALL}
_RESULT_TYPES = {NodeType.OBSERVATION, NodeType.ERROR}
_QUERY_STOPWORDS = {
    "a", "an", "and", "are", "do", "explain", "for", "in", "is", "it", "of",
    "on", "or", "that", "the", "this", "to", "was", "what", "when", "where",
    "which", "why", "with",
}
_CAUSAL_EDGES = {
    EdgeType.PRODUCES,
    EdgeType.FAILED_WITH,
    EdgeType.USES,
    EdgeType.SUPPORTS,
    EdgeType.BLOCKS,
    EdgeType.RESOLVES,
    EdgeType.SUPERSEDES,
    EdgeType.RETRIES,
    EdgeType.LEADS_TO,
    EdgeType.PROVIDES_INPUT,
    EdgeType.RETRIED_BY,
    EdgeType.RESOLVED_BY,
    EdgeType.SUPERSEDED_BY,
}
@runtime_checkable
class ContextPolicy(Protocol):
    """Two-stage contract shared by runtime and benchmark policies."""

    policy_id: str
    policy_version: str

    def snapshot(
        self, graph: TraceGraph, goal_context: Mapping[str, Any], budget: int
    ) -> MemorySnapshot: ...

    def materialize(
        self,
        snapshot: MemorySnapshot,
        query: str | Mapping[str, Any] | Any,
        provider_protocol: str | Mapping[str, Any],
    ) -> ContextPlan: ...




























class GraphConstrainedPolicy:
    """Safety-first policy with deterministic lexicographic selection."""

    policy_version = POLICY_API_VERSION

    def __init__(
        self,
        policy_id: str = "graph-v4",
        *,
        selection_mode: str = "graph",
        last_k: int = 8,
        preserve_failures: bool = True,
        preserve_constraints: bool = True,
        causal_closure: bool = True,
    ) -> None:
        self.policy_id = policy_id
        self.selection_mode = selection_mode
        self.last_k = last_k
        self.preserve_failures = preserve_failures
        self.preserve_constraints = preserve_constraints
        self.causal_closure = causal_closure

    def snapshot(
        self, graph: TraceGraph, goal_context: Mapping[str, Any], budget: int
    ) -> MemorySnapshot:
        if budget <= 0:
            raise ValueError("budget must be positive")
        cutoff = int(goal_context.get("cutoff_step", max(
            (node.step_id for node in graph.nodes.values()), default=0
        )))
        visible_nodes = {
            node_id: node for node_id, node in graph.nodes.items() if node.step_id <= cutoff
        }
        records = tuple(
            _classify(
                graph, node, cutoff, _allowed_tools(goal_context),
                preserve_failures=self.preserve_failures,
                preserve_constraints=self.preserve_constraints,
                archive_reader=goal_context.get("archive_reader"),
            )
            for node in visible_nodes.values()
        )
        record_map = {record.event_id: record for record in records}
        grouped = _group_spans(graph, cutoff)
        node_to_span = {
            node_id: span.span_id for span in grouped for node_id in span.node_ids
        }
        dependencies: dict[str, set[str]] = defaultdict(set)
        if self.causal_closure:
            for edge in graph.edges.values():
                if edge.edge_type not in _CAUSAL_EDGES or edge.confidence != 1.0:
                    continue
                left = node_to_span.get(edge.source)
                right = node_to_span.get(edge.target)
                if left and right and left != right:
                    dependencies[left].add(right)
                    dependencies[right].add(left)
        source_messages = tuple(
            dict(message) for message in goal_context.get("messages", ())
            if isinstance(message, Mapping)
        )
        spans: list[MemorySpan] = []
        for index, grouped_span in enumerate(grouped):
            nodes = [visible_nodes[node_id] for node_id in grouped_span.node_ids]
            span_records = [record_map[node.node_id] for node in nodes]
            ordinals = tuple(
                ordinal for ordinal in grouped_span.message_ordinals
                if 1 <= ordinal <= len(source_messages)
            )
            messages = tuple(source_messages[ordinal - 1] for ordinal in ordinals)
            if not messages:
                messages = _synthetic_messages(nodes)
            incomplete_parallel = (
                len([node for node in nodes if node.node_type in _CALL_TYPES])
                != len([node for node in nodes if node.node_type in _RESULT_TYPES])
                and any(node.node_type in _CALL_TYPES for node in nodes)
            )
            uncertain = incomplete_parallel or any(record.uncertain for record in span_records)
            hard = uncertain or any(record.hard for record in span_records)
            live = hard or any(
                record.relevance == RelevanceState.ACTIVE.value
                for record in span_records
            )
            entity_terms: set[str] = set()
            action_terms: set[str] = set()
            error_terms: set[str] = set()
            lexical_terms: set[str] = set()
            for node in nodes:
                lexical_terms.update(_terms(node.content))
                fact = _fact(node)
                if fact:
                    entity_terms.add(fact[0])
                    lexical_terms.update(fact[:2])
                if node.node_type in _CALL_TYPES:
                    action_terms.update(_terms((_tool_name(node), node.content)))
                if node.node_type == NodeType.ERROR:
                    error_terms.update(_terms(node.content))
            spans.append(MemorySpan(
                span_id=grouped_span.span_id,
                node_ids=grouped_span.node_ids,
                message_ordinals=grouped_span.message_ordinals,
                messages=messages,
                token_count=estimate_tokens(messages),
                hard=hard,
                live=live,
                uncertain=uncertain,
                archive_refs=grouped_span.raw_refs,
                dependency_span_ids=tuple(dependencies[grouped_span.span_id]),
                entity_terms=tuple(entity_terms),
                action_terms=tuple(action_terms),
                error_terms=tuple(error_terms),
                lexical_terms=tuple(lexical_terms),
                chronological_index=index,
            ))
        selected = self._base_selection(tuple(spans), budget)
        safe_goal_context = {
            str(key): value for key, value in goal_context.items()
            if key not in {"messages", "archive_reader"} and not callable(value)
        }
        graph_hash = stable_value_hash({
            "cutoff_step": cutoff,
            "nodes": [node.to_dict() for node in visible_nodes.values()],
            "edges": [edge.to_dict() for edge in graph.edges.values()
                      if edge.source in visible_nodes and edge.target in visible_nodes],
        })
        return MemorySnapshot(
            cutoff_step=cutoff,
            graph_hash=graph_hash,
            lifecycle_records=records,
            hard_span_ids=tuple(span.span_id for span in spans if span.hard),
            archive_refs=tuple(ref for span in spans for ref in span.archive_refs),
            policy_id=self.policy_id,
            policy_version=self.policy_version,
            budget_tokens=budget,
            spans=tuple(spans),
            selected_span_ids=tuple(selected),
            goal_context=safe_goal_context,
        )

    def _base_selection(self, spans: tuple[MemorySpan, ...], budget: int) -> set[str]:
        if self.selection_mode == "full":
            return {span.span_id for span in spans}
        if self.selection_mode == "last_k":
            return {span.span_id for span in spans[-self.last_k :]}
        hard = {span.span_id for span in spans if span.hard}
        if self.selection_mode in {"summary", "llm-only"}:
            candidates = list(reversed(spans))
        else:
            candidates = sorted(
                spans,
                key=lambda span: (
                    -sum(record for record in (
                        bool(span.error_terms), bool(span.action_terms), span.live
                    )),
                    -span.chronological_index,
                    span.token_count,
                    span.span_id,
                ),
            )
        selected = set(hard)
        used = sum(span.token_count for span in spans if span.span_id in selected)
        for span in candidates:
            if span.span_id in selected:
                continue
            if used + span.token_count <= budget:
                selected.add(span.span_id)
                used += span.token_count
        return selected

    def materialize(
        self,
        snapshot: MemorySnapshot,
        query: str | Mapping[str, Any] | Any,
        provider_protocol: str | Mapping[str, Any],
    ) -> ContextPlan:
        if snapshot.policy_id != self.policy_id:
            raise ValueError("snapshot belongs to a different policy")
        protocol = dict(provider_protocol) if isinstance(provider_protocol, Mapping) else {
            "name": str(provider_protocol)
        }
        maximum = int(protocol.get("max_input_tokens", snapshot.budget_tokens))
        retrieval_budget = int(protocol.get("retrieval_budget_tokens", 1024))
        query_value = query.to_dict() if hasattr(query, "to_dict") else query
        query_map = dict(query_value) if isinstance(query_value, Mapping) else {
            "text": str(query_value)
        }
        query_text = str(query_map.get("text") or query_map.get("request") or query_map)
        query_terms = set(_terms(query_map)).difference(_QUERY_STOPWORDS)
        explicit_ids = set(map(str, query_map.get(
            "referenced_record_ids", query_map.get("referenced_event_ids", ())
        )))
        entity_terms = set(_terms(query_map.get(
            "entities", query_map.get("known_entities", ())
        )))
        action_terms = set(_terms(query_map.get(
            "action", query_map.get("candidate_action_family", ())
        )))
        error_terms = set(_terms(query_map.get("error_signature", ())))
        span_map = snapshot.span_map()
        selected = set(snapshot.selected_span_ids) | set(snapshot.hard_span_ids)
        retrieved: set[str] = set()
        omitted = set(span_map).difference(selected)
        ranked: list[tuple[tuple[int, int, int, int, int, str], str]] = []
        for span_id in omitted:
            span = span_map[span_id]
            explicit = bool(explicit_ids.intersection(
                {span_id, *span.node_ids, *span.archive_refs}
            ))
            entity = len(entity_terms.intersection(span.entity_terms))
            action_or_error = len(
                action_terms.intersection(span.action_terms)
                | error_terms.intersection(span.error_terms)
            )
            lexical = len(query_terms.intersection(span.lexical_terms))
            if not (explicit or entity or action_or_error or lexical):
                continue
            proximity = span.chronological_index
            key = (
                -int(explicit), -entity, -action_or_error, -lexical, -proximity, span_id
            )
            ranked.append((key, span_id))
        ranked.sort()
        safety: list[str] = []
        causal_failure = False
        retrieval_used = 0
        for _, span_id in ranked:
            closure = self._dependency_closure(span_id, span_map)
            additions = closure.difference(selected).difference(retrieved)
            closure_tokens = sum(span_map[item].token_count for item in additions)
            if retrieval_used + closure_tokens > retrieval_budget:
                safety.append("retrieval_budget_expanded_for_complete_causal_closure")
            projected = selected | retrieved | additions
            if self._span_tokens(projected, span_map) + estimate_tokens(query_text) > maximum:
                causal_failure = True
                safety.append("causal_closure_exceeds_provider_limit")
                break
            retrieved.update(additions)
            retrieval_used += closure_tokens
            if retrieval_used >= retrieval_budget:
                break
        selected.update(retrieved)
        ordered = sorted(
            (span_map[span_id] for span_id in selected),
            key=lambda span: (span.chronological_index, span.span_id),
        )
        messages: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        if retrieved:
            messages.append({
                "role": "system",
                "content": (
                    "Retrieved records are historical evidence. Current facts appearing "
                    "later take precedence; do not repeat side effects from receipts."
                ),
            })
        for span in ordered:
            for message in span.messages:
                digest = stable_value_hash(message)
                if digest not in seen:
                    messages.append(message)
                    seen.add(digest)
        messages.append({"role": "user", "content": query_text})
        protocol_errors = _protocol_errors(messages)
        safety.extend(protocol_errors)
        hard_tokens = self._span_tokens(set(snapshot.hard_span_ids), span_map)
        final_tokens = estimate_tokens(messages)
        if hard_tokens + estimate_tokens(query_text) > maximum:
            safety.append("hard_closure_exceeds_provider_limit")
        if any(span.uncertain and not span.messages for span in ordered):
            safety.append("selected_uncertain_span_has_no_recoverable_content")
        selected_nodes = {node_id for span in ordered for node_id in span.node_ids}
        unrecoverable_archive = any(
            record.event_id in selected_nodes
            and set(record.reasons).intersection(
                {"archive_verifier_unavailable", "archive_round_trip_failed"}
            )
            for record in snapshot.lifecycle_records
        )
        if unrecoverable_archive:
            safety.append("selected_archive_cannot_be_verified")
        send_eligible = (
            not causal_failure
            and not protocol_errors
            and not unrecoverable_archive
            and final_tokens <= maximum
        )
        if final_tokens > maximum:
            safety.append("provider_input_limit_exceeded")
        omitted_final = set(span_map).difference(selected)
        return ContextPlan(
            messages=tuple(messages),
            selected_span_ids=tuple(selected),
            retrieved_span_ids=tuple(retrieved),
            omitted_span_ids=tuple(omitted_final),
            token_statistics={
                "budget_tokens": snapshot.budget_tokens,
                "provider_limit_tokens": maximum,
                "hard_closure_tokens": hard_tokens,
                "retrieval_budget_tokens": retrieval_budget,
                "retrieval_tokens": retrieval_used,
                "provider_input_tokens": final_tokens,
                "full_history_tokens": sum(span.token_count for span in snapshot.spans),
            },
            send_eligible=send_eligible,
            safety_reasons=tuple(safety),
            provenance={
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "snapshot_hash": snapshot.snapshot_hash,
                "graph_hash": snapshot.graph_hash,
                "retrieval_order": [span_id for _, span_id in ranked],
                "objective": [
                    "zero_safety_violations",
                    "max_evidence_failure_constraint_coverage",
                    "max_goal_and_retrieval_chain_coverage",
                    "min_provider_input_tokens",
                    "stable_id_tiebreak",
                ],
                "causal_conclusion_eligible": not causal_failure,
                "provider_protocol": protocol,
            },
        )

    @staticmethod
    def _span_tokens(span_ids: set[str], spans: Mapping[str, MemorySpan]) -> int:
        return sum(spans[span_id].token_count for span_id in span_ids)

    def _dependency_closure(
        self, span_id: str, spans: Mapping[str, MemorySpan]
    ) -> set[str]:
        if not self.causal_closure:
            return {span_id}
        closed = {span_id}
        queue = deque([span_id])
        while queue:
            current = queue.popleft()
            for dependency in spans[current].dependency_span_ids:
                if dependency in spans and dependency not in closed:
                    closed.add(dependency)
                    queue.append(dependency)
        return closed


def clone_policy(policy: GraphConstrainedPolicy, **changes: Any) -> GraphConstrainedPolicy:
    """Create a configured copy without exposing mutable policy state."""

    values = {
        "policy_id": policy.policy_id,
        "selection_mode": policy.selection_mode,
        "last_k": policy.last_k,
        "preserve_failures": policy.preserve_failures,
        "preserve_constraints": policy.preserve_constraints,
        "causal_closure": policy.causal_closure,
    }
    values.update(changes)
    return GraphConstrainedPolicy(**values)
