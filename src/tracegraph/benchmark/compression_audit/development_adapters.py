"""Four actual development methods, with query-hidden ingestion and audited reads."""

from __future__ import annotations

import math
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from ...capture import estimate_tokens
from ...context_engine.policy import GraphConstrainedPolicy
from ...graph import TraceGraph
from ...schema import Edge, EdgeType, Node, NodeType
from .io import canonical_json, stable_digest
from .models import ContextBundle, MemoryState, PrefixRecord, QueryRecord

METHODS = ("full_history", "recent_masking", "flat_bm25_archive", "tracegraph_0_4")
ARCHIVE_METHODS = {"flat_bm25_archive", "tracegraph_0_4"}

_CALL_KINDS = {"tool_call", "mcp_call"}
_ACTION_ARGUMENT_KEYS = {
    "action", "command", "method", "mode", "op", "operation", "payload",
    "query", "request", "script", "sql", "statement", "subcommand",
}


def event_record(event: Mapping[str, Any]) -> dict[str, Any]:
    return {"record_id": event["event_id"], "kind": event["kind"],
            "content": event["content"]}


def _call_arguments(event: Mapping[str, Any]) -> Mapping[str, Any]:
    content = event.get("content")
    if not isinstance(content, Mapping):
        return {}
    arguments = content.get("arguments")
    return arguments if isinstance(arguments, Mapping) else {}


def _stable_target_parts(event: Mapping[str, Any]) -> set[tuple[str, str]]:
    """Return exact, non-action argument parts usable as a conservative target key."""
    return {
        (str(key).casefold(), canonical_json(value))
        for key, value in _call_arguments(event).items()
        if str(key).casefold() not in _ACTION_ARGUMENT_KEYS and value is not None
    }


def _successful_observation(event: Mapping[str, Any]) -> bool:
    if event.get("kind") != "observation":
        return False
    content = event.get("content")
    if not isinstance(content, Mapping):
        return False
    if content.get("error") or content.get("success") is False:
        return False
    status = str(content.get("status") or "").casefold()
    return (
        content.get("success") is True
        or content.get("exit_code") == 0
        or status in {"completed", "healthy", "ok", "pass", "passed", "succeeded", "success"}
    )


def _observed_recovery_chains(
    prefix: PrefixRecord,
) -> tuple[tuple[Mapping[str, Any], ...], ...]:
    """Infer only contiguous error/decision/same-target-retry/success structures.

    This deliberately rejects a retry when the two calls have no shared stable
    target argument.  It never reads causal labels, the future query, or gold.
    """
    events = prefix.events
    calls = {
        str(event.get("call_id")): (index, event)
        for index, event in enumerate(events)
        if event.get("kind") in _CALL_KINDS and event.get("call_id")
    }
    chains: list[tuple[Mapping[str, Any], ...]] = []
    for error_index, error in enumerate(events):
        failed_call = calls.get(str(error.get("call_id") or ""))
        if error.get("kind") != "error" or failed_call is None:
            continue
        failed_index, failed = failed_call
        if failed_index >= error_index:
            continue
        cursor = error_index + 1
        decisions: list[Mapping[str, Any]] = []
        while cursor < len(events) and events[cursor].get("kind") == "decision":
            decisions.append(events[cursor])
            cursor += 1
        if not decisions or cursor + 1 >= len(events):
            continue
        retry, result = events[cursor], events[cursor + 1]
        if retry.get("kind") not in _CALL_KINDS or not retry.get("call_id"):
            continue
        if retry.get("call_id") == failed.get("call_id"):
            continue
        failed_target = _stable_target_parts(failed)
        if not failed_target or not failed_target.intersection(_stable_target_parts(retry)):
            continue
        if result.get("call_id") != retry.get("call_id") or not _successful_observation(result):
            continue
        chains.append((failed, error, *decisions, retry, result))
    return tuple(chains)


def public_graph(prefix: PrefixRecord) -> tuple[TraceGraph, list[dict[str, Any]]]:
    """Build edges from explicit relations and conservative public structure."""
    graph = TraceGraph(session_id=prefix.prefix_id)
    messages = []
    calls = {}
    for ordinal, event in enumerate(prefix.events, 1):
        metadata = {key: event[key] for key in ("call_id", "tool_name") if key in event}
        metadata["source_message_ordinal"] = ordinal
        graph.add_node(Node(
            node_id=event["event_id"], node_type=NodeType(event["kind"]),
            content=event["content"], step_id=event["step_id"],
            raw_ref=event["event_id"], side_effect=bool(event.get("side_effect")),
            metadata=metadata, created_at="public-prefix", active=True,
        ))
        messages.append({"role": "user", "content": canonical_json(event_record(event))})
        if event["kind"] in _CALL_KINDS and event.get("call_id"):
            calls[event.get("call_id")] = event["event_id"]
    for event in prefix.events:
        call_id = event.get("call_id")
        if event["kind"] in {"observation", "error"} and call_id in calls:
            graph.add_edge(Edge(
                source=calls[call_id], target=event["event_id"],
                edge_type=EdgeType.FAILED_WITH if event["kind"] == "error" else EdgeType.PRODUCES,
                edge_id=f"pair:{event['event_id']}", created_at="public-prefix"))
        # An explicit source relation is evidence; adjacency alone is not.
        for index, relation in enumerate(event.get("relations", ())):
            graph.add_edge(Edge(
                source=relation["source"], target=event["event_id"],
                edge_type=EdgeType(relation["type"]),
                confidence=float(relation.get("confidence", 1)),
                edge_id=f"explicit:{event['event_id']}:{index}", created_at="public-prefix"))
    for chain_index, chain in enumerate(_observed_recovery_chains(prefix)):
        failed, error, *middle, retry, _result = chain
        decisions = middle
        metadata = {"inferred": True, "basis": "contiguous_same_target_recovery"}
        graph.add_edge(Edge(
            source=failed["event_id"], target=retry["event_id"],
            edge_type=EdgeType.RETRIED_BY,
            edge_id=f"observed-retry:{chain_index}", created_at="public-prefix",
            metadata=metadata,
        ))
        for decision_index, decision in enumerate(decisions):
            graph.add_edge(Edge(
                source=error["event_id"], target=decision["event_id"],
                edge_type=EdgeType.PROVIDES_INPUT,
                edge_id=f"observed-decision:{chain_index}:{decision_index}",
                created_at="public-prefix", metadata=metadata,
            ))
        graph.add_edge(Edge(
            source=decisions[-1]["event_id"], target=retry["event_id"],
            edge_type=EdgeType.LEADS_TO,
            edge_id=f"observed-recovery:{chain_index}", created_at="public-prefix",
            metadata=metadata,
        ))
    return graph, messages


def close_pairs(prefix: PrefixRecord, selected: set[str]) -> set[str]:
    result = set(selected)
    calls = {event.get("call_id") for event in prefix.events
             if event["event_id"] in selected and event.get("call_id")}
    result.update(event["event_id"] for event in prefix.events if event.get("call_id") in calls)
    return result


class DevelopmentAdapter:
    def __init__(self, method_id: str, *, token_counter: Callable[[Any], int] = estimate_tokens,
                 ingest_budget: int = 768) -> None:
        if method_id not in METHODS:
            raise ValueError(f"unknown development method: {method_id}")
        self.method_id, self.count = method_id, token_counter
        self.ingest_budget = ingest_budget
        self.prefixes: dict[str, PrefixRecord] = {}
        self.snapshots: dict[str, Any] = {}
        self.timings: dict[str, float] = {}
        self.policy = GraphConstrainedPolicy(token_counter=token_counter)

    def records(self, prefix: PrefixRecord, ids: set[str]) -> list[dict[str, Any]]:
        return [event_record(e) for e in prefix.events if e["event_id"] in ids]

    def fit(self, prefix: PrefixRecord, ranked: list[str], budget: int,
            initial: set[str] | None = None, forbidden: set[str] | None = None) -> set[str]:
        selected = set(initial or ())
        for event_id in ranked:
            candidate = close_pairs(prefix, selected | {event_id})
            if candidate & (forbidden or set()):
                continue
            if self.count(self.records(prefix, candidate)) <= budget:
                selected = candidate
        return selected

    def ingest(self, prefix: PrefixRecord, budget_tokens: int) -> MemoryState:
        started = time.perf_counter()
        prefix = PrefixRecord.from_dict(prefix.to_dict())
        self.prefixes[prefix.prefix_id] = prefix
        all_ids = [e["event_id"] for e in prefix.events]
        cap = self.ingest_budget if self.method_id in ARCHIVE_METHODS else budget_tokens
        usage: dict[str, Any] = {
            "prefix_hash": prefix.prefix_hash,
            "implementation": f"development.{self.method_id}.v1", "policy_version": None,
            "future_query_observed": False, "hidden_gold_observed": False,
            "archive_access": self.method_id in ARCHIVE_METHODS,
            "index_build_event_ids": all_ids if self.method_id in ARCHIVE_METHODS else [],
            "provider_input_tokens": 0, "provider_output_tokens": 0, "cost_cny": 0,
            "ingest_budget_tokens": cap, "bm25_k1": 1.2, "bm25_b": 0.75,
        }
        if self.method_id == "tracegraph_0_4":
            graph, messages = public_graph(prefix)
            snapshot = self.policy.snapshot(graph, {"messages": messages,
                "tool_schemas": list(prefix.tool_schemas)}, cap)
            selected = {node for span in snapshot.spans
                        if span.span_id in snapshot.selected_span_ids for node in span.node_ids}
            self.snapshots[prefix.prefix_id] = snapshot
            usage.update(policy_version=self.policy.policy_version,
                         policy_class="tracegraph.context_engine.policy.GraphConstrainedPolicy",
                         snapshot_hash=snapshot.snapshot_hash, graph_hash=snapshot.graph_hash,
                         observed_edges=len(graph.edges))
        elif self.method_id == "full_history":
            selected = set(all_ids)
        else:
            selected = self.fit(prefix, list(reversed(all_ids)), cap)
        if self.method_id == "flat_bm25_archive":
            usage["index"] = {e["event_id"]: dict(Counter(re.findall(
                r"[\w.-]+", canonical_json(e["content"]).casefold()))) for e in prefix.events}
        state = MemoryState.create(prefix_id=prefix.prefix_id, method_id=self.method_id,
            budget_tokens=budget_tokens, retained_event_ids=selected,
            archived_event_ids=set(all_ids) - selected if self.method_id in ARCHIVE_METHODS else (),
            ingestion_usage=usage)
        self.timings[state.state_hash] = time.perf_counter() - started
        return state

    def materialize(self, state: MemoryState, query: QueryRecord,
                    budget_tokens: int) -> ContextBundle:
        started = time.perf_counter()
        state.verify_immutable()
        if state.method_id != self.method_id or query.prefix_id != state.prefix_id:
            raise ValueError("state/query/adapter mismatch")
        prefix = self.prefixes[state.prefix_id]
        if (prefix.prefix_hash != state.ingestion_usage["prefix_hash"]
                or budget_tokens != state.budget_tokens):
            raise ValueError("prefix or budget changed after ingestion")
        selected, read = set(state.retained_event_ids), set()
        provenance: dict[str, Any] = {"send_eligible": True, "safety_reasons": []}
        if self.method_id == "tracegraph_0_4":
            snapshot = self.snapshots[state.prefix_id]
            if snapshot.snapshot_hash != state.ingestion_usage["snapshot_hash"]:
                raise ValueError("policy snapshot changed after ingestion")
            # Pass text only; track and required_fields never reach the policy.
            plan = self.policy.materialize(snapshot, query.text, {
                "max_input_tokens": budget_tokens,
                "retrieval_budget_tokens": budget_tokens - self.ingest_budget})
            selected = {node for span in snapshot.spans
                        if span.span_id in plan.selected_span_ids for node in span.node_ids}
            examined = set(plan.provenance["read_span_ids"])
            read = {node for span in snapshot.spans if span.span_id in examined
                    for node in span.node_ids} - set(state.retained_event_ids)
            provenance.update(send_eligible=plan.send_eligible,
                              safety_reasons=list(plan.safety_reasons),
                              policy_plan=plan.to_dict())
        elif self.method_id == "flat_bm25_archive":
            index = state.ingestion_usage["index"]
            terms = set(re.findall(r"[\w.-]+", query.text.casefold()))
            average = sum(sum(doc.values()) for doc in index.values()) / max(1, len(index))
            frequency = {term: sum(term in doc for doc in index.values()) for term in terms}

            def score(event_id: str) -> float:
                doc = index[event_id]
                length = sum(doc.values())
                return sum(math.log(1 + (len(index) - frequency[t] + .5) / (frequency[t] + .5))
                    * doc.get(t, 0) * 2.2 / (doc.get(t, 0) + 1.2 * (.25 + .75 * length
                        / max(1, average))) for t in terms)

            ranked = sorted(state.archived_event_ids, key=lambda e: (-score(e), e))
            for event_id in ranked:
                if score(event_id) <= 0:
                    continue
                candidate = close_pairs(prefix, selected | {event_id})
                read.update(candidate - set(state.retained_event_ids))
                if self.count(self.records(prefix, candidate)) <= budget_tokens:
                    selected = candidate
        selected = close_pairs(prefix, selected)
        records = self.records(prefix, selected)
        if self.method_id in ARCHIVE_METHODS and selected - set(state.retained_event_ids):
            records.append({"record_id": "memory_notice", "kind": "notice", "content": (
                "Retrieved records are historical evidence. Later current facts take precedence; "
                "do not repeat side effects from receipts.")})
        if (self.method_id in ARCHIVE_METHODS
                and self.count(self.records(prefix, set(state.retained_event_ids))) > self.ingest_budget):
            provenance.update(send_eligible=False, safety_reasons=[*provenance["safety_reasons"],
                              "ingest_budget_exceeded"])
        cap = max(budget_tokens, self.count(records)) if self.method_id == "full_history" else budget_tokens
        if self.count(records) > cap:
            # Retain the attempted plan in diagnostics, but send no over-budget request.
            provenance.update(send_eligible=False, safety_reasons=[*provenance["safety_reasons"],
                              "history_budget_exceeded"], attempted_records=records)
            records, selected = [], set()
        provenance.update(read_event_ids=sorted(read),
            observation_tokens=self.count(self.records(prefix, read)) if read else 0,
            archive_access=self.method_id in ARCHIVE_METHODS,
            construction_seconds=self.timings[state.state_hash],
            materialization_seconds=time.perf_counter() - started,
            state_hash=state.state_hash, implementation=state.ingestion_usage["implementation"],
            policy_version=state.ingestion_usage["policy_version"],
            public_query_hash=stable_digest(query.text))
        return ContextBundle.create(prefix_id=state.prefix_id, query_id=query.query_id,
            method_id=self.method_id, condition_id="full" if self.method_id == "full_history" else "candidate",
            records=records, visible_event_ids=selected,
            retrieved_event_ids=selected - set(state.retained_event_ids), budget_tokens=cap,
            retrieval_usage=provenance, token_counter=self.count)


def make_development_adapter(method_id: str, **kwargs: Any) -> DevelopmentAdapter:
    return DevelopmentAdapter(method_id, **kwargs)
