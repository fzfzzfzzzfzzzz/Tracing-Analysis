"""Lifecycle-resident memory with deterministic, query-gated archive retrieval.

This module intentionally keeps the two existing TraceGraph mechanisms distinct:
``GraphLifecycleManager`` owns the resident working context, while
``GraphConstrainedPolicy`` may restore raw causal evidence from the archive after
the public query is known.  It is an experimental bridge, not an alias for the
legacy ``tracegraph_0_4`` benchmark method.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Mapping
from typing import Any

from ...context_engine.graph_policy import GraphLifecycleManager
from ...context_engine.policy import GraphConstrainedPolicy
from ...schema import NodeType
from ..compression_audit.development_adapters import event_record, public_graph
from ..compression_audit.io import stable_digest
from ..compression_audit.models import ContextBundle, MemoryState, PrefixRecord, QueryRecord


CAUSAL_RETRIEVAL = "tracegraph_causal_retrieval"
LIFECYCLE_CARDS = "tracegraph_lifecycle_cards"
LIFECYCLE_RETRIEVAL = "tracegraph_lifecycle_retrieval"
METHODS = (CAUSAL_RETRIEVAL, LIFECYCLE_CARDS, LIFECYCLE_RETRIEVAL)

_CURRENT_ONLY_TERMS = {"current", "unrelated"}
_ARCHIVE_TRIGGER_TERMS = {
    "cause",
    "earlier",
    "evidence",
    "fail",
    "failed",
    "failure",
    "reconstruct",
    "replacement",
    "sequence",
    "unsuccessful",
}
_HARD_REASON_PREFIXES = (
    "current_goal",
    "current_subgoal",
    "active_constraint",
    "pending_irreversible_confirmation",
    "unique_unrecoverable_critical_evidence",
    "unique_unrecoverable_final_evidence",
)


def archive_trigger_terms(query: str) -> tuple[str, ...]:
    """Return the frozen public lexical reasons for an archive read.

    Current-only requests are an explicit negative guard.  The rule consumes
    only public query text; query type, rubric fields, causal labels and gold are
    deliberately unavailable.
    """

    terms = set(re.findall(r"\w+", query.casefold()))
    if _CURRENT_ONLY_TERMS <= terms:
        return ()
    return tuple(sorted(terms & _ARCHIVE_TRIGGER_TERMS))


class LifecycleRetrievalAdapter:
    """Two-stage benchmark adapter for lifecycle cards and their hybrid.

    The lifecycle-only condition never reads the raw archive after ingestion.
    The hybrid condition restores complete raw spans only when the frozen public
    lexical gate fires.  Raw visibility is credited only to raw records, never
    merely because a compact card names its source nodes.
    """

    def __init__(
        self,
        method_id: str,
        *,
        token_counter: Callable[[Any], int],
    ) -> None:
        if method_id not in {LIFECYCLE_CARDS, LIFECYCLE_RETRIEVAL}:
            raise ValueError(f"unsupported lifecycle retrieval method: {method_id}")
        self.method_id = method_id
        self.count = token_counter
        self.manager = GraphLifecycleManager()
        self.policy = GraphConstrainedPolicy(token_counter=token_counter)
        self.prefixes: dict[str, PrefixRecord] = {}
        self.snapshots: dict[str, Any] = {}
        self.timings: dict[str, float] = {}

    @staticmethod
    def _is_raw_item(graph: Any, item: Any) -> bool:
        node = graph.nodes.get(item.node_id)
        return bool(
            node is not None
            and item.node_type == node.node_type
            and item.content == node.content
            and tuple(item.source_node_ids) in {(), (node.node_id,)}
        )

    @staticmethod
    def _is_hard(entry: Mapping[str, Any]) -> bool:
        reason = str(entry["reason"])
        return reason.startswith(_HARD_REASON_PREFIXES)

    @staticmethod
    def _is_card(entry: Mapping[str, Any]) -> bool:
        return str(entry["reason"]).startswith("failure_card")

    def _entries(self, graph: Any, view: Any) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for index, item in enumerate(view.items):
            raw = self._is_raw_item(graph, item)
            if raw:
                record = event_record({
                    "event_id": item.node_id,
                    "kind": item.node_type.value,
                    "content": item.content,
                })
            else:
                record = {
                    "record_id": f"lifecycle-memory-{index:04d}",
                    "kind": (
                        "failure_card"
                        if item.node_type == NodeType.SUMMARY and self._is_card({"reason": item.reason})
                        else item.node_type.value
                    ),
                    "content": {
                        "memory": item.content,
                        "reason": item.reason,
                        "source_event_ids": list(item.source_node_ids),
                        "raw_archive_ref": item.raw_ref,
                    },
                }
            step = graph.nodes[item.node_id].step_id if item.node_id in graph.nodes else 10**12
            entries.append({
                "record": record,
                "raw": raw,
                "reason": item.reason,
                "source_node_ids": list(item.source_node_ids),
                "step": step,
            })
        return entries

    def _fit_resident_entries(
        self,
        entries: list[dict[str, Any]],
        budget_tokens: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Re-fit the manager view using the benchmark's final record wrapper.

        The runtime manager counts item payloads.  The benchmark sends structured
        records with IDs and kinds, so wrapper overhead must be checked again.
        Hard lifecycle obligations and failure cards are indivisible; optional
        records are admitted newest first.
        """

        required = [
            entry for entry in entries
            if self._is_hard(entry) or self._is_card(entry)
        ]
        required_records = [entry["record"] for entry in required]
        if self.count(required_records) > budget_tokens:
            return required, False
        selected = list(required)
        optional = [entry for entry in entries if entry not in required]
        for entry in sorted(
            optional,
            key=lambda item: (-int(item["step"]), str(item["reason"])),
        ):
            candidate = [item["record"] for item in (*selected, entry)]
            if self.count(candidate) <= budget_tokens:
                selected.append(entry)
        return sorted(selected, key=lambda item: (int(item["step"]), str(item["reason"]))), True

    def ingest(self, prefix: PrefixRecord, budget_tokens: int) -> MemoryState:
        started = time.perf_counter()
        prefix = PrefixRecord.from_dict(prefix.to_dict())
        # The lifecycle engine mutates storage/lifecycle profiles.  Keep archive
        # retrieval on an independent public graph so those runtime mutations do
        # not make every archived node look like unverifiable hard evidence.
        archive_graph, messages = public_graph(prefix)
        archive_resident_budget = max(128, budget_tokens // 2)
        snapshot = self.policy.snapshot(
            archive_graph,
            {"messages": messages, "tool_schemas": list(prefix.tool_schemas)},
            archive_resident_budget,
        )
        graph, _ = public_graph(prefix)
        view = self.manager.select(graph, budget=budget_tokens)
        attempted_entries = self._entries(graph, view)
        entries, resident_fit = self._fit_resident_entries(
            attempted_entries, budget_tokens
        )
        records = [entry["record"] for entry in entries]
        raw_resident = {
            str(entry["record"]["record_id"])
            for entry in entries
            if entry["raw"]
        }
        all_ids = {str(event["event_id"]) for event in prefix.events}
        resident_tokens = self.count(records)
        usage = {
            "implementation": self.method_id,
            "policy_class": (
                "tracegraph.context_engine.graph_policy.GraphLifecycleManager+"
                "tracegraph.context_engine.policy.GraphConstrainedPolicy"
            ),
            "policy_version": self.policy.policy_version,
            "prefix_hash": prefix.prefix_hash,
            "future_query_observed": False,
            "hidden_gold_observed": False,
            "archive_access": self.method_id == LIFECYCLE_RETRIEVAL,
            "index_build_event_ids": sorted(all_ids),
            "resident_raw_event_ids": sorted(raw_resident),
            "resident_tokens": resident_tokens,
            "ingest_budget_tokens": budget_tokens,
            "ingest_eligible": resident_fit and resident_tokens <= budget_tokens,
            "resident_view_records_dropped": len(attempted_entries) - len(entries),
            "lifecycle_view": view.to_dict(),
            "snapshot_hash": snapshot.snapshot_hash,
            "graph_hash": snapshot.graph_hash,
            "archive_trigger_revision": "public_lexical_gate_v1",
            "archive_resident_budget_tokens": archive_resident_budget,
            "provider_input_tokens": 0,
            "provider_output_tokens": 0,
            "cost_cny": 0,
        }
        state = MemoryState.create(
            prefix_id=prefix.prefix_id,
            method_id=self.method_id,
            budget_tokens=budget_tokens,
            retained_event_ids=raw_resident,
            archived_event_ids=all_ids - raw_resident,
            summaries=entries,
            ingestion_usage=usage,
        )
        self.prefixes[prefix.prefix_id] = prefix
        self.snapshots[prefix.prefix_id] = snapshot
        self.timings[state.state_hash] = time.perf_counter() - started
        return state

    def _bundle(
        self,
        state: MemoryState,
        query: QueryRecord,
        *,
        records: list[dict[str, Any]],
        visible: set[str],
        retrieved: set[str],
        usage: dict[str, Any],
        budget_tokens: int,
    ) -> ContextBundle:
        attempted_tokens = self.count(records)
        if attempted_tokens > budget_tokens or not usage.get("send_eligible", True):
            if attempted_tokens > budget_tokens:
                usage["safety_reasons"] = list(dict.fromkeys([
                    *usage.get("safety_reasons", ()),
                    "history_budget_exceeded",
                ]))
            usage.update(
                send_eligible=False,
                attempted_records=records,
                attempted_tokens=attempted_tokens,
            )
            records, visible, retrieved = [], set(), set()
        return ContextBundle.create(
            prefix_id=state.prefix_id,
            query_id=query.query_id,
            method_id=self.method_id,
            condition_id="candidate",
            records=records,
            visible_event_ids=visible,
            retrieved_event_ids=retrieved,
            budget_tokens=budget_tokens,
            retrieval_usage=usage,
            token_counter=self.count,
        )

    def materialize(
        self,
        state: MemoryState,
        query: QueryRecord,
        budget_tokens: int,
    ) -> ContextBundle:
        started = time.perf_counter()
        state.verify_immutable()
        if state.method_id != self.method_id or state.prefix_id != query.prefix_id:
            raise ValueError("state/query/adapter mismatch")
        if budget_tokens != state.budget_tokens:
            raise ValueError("budget changed after query-hidden ingestion")
        prefix = self.prefixes[state.prefix_id]
        if prefix.prefix_hash != state.ingestion_usage["prefix_hash"]:
            raise ValueError("prefix changed after query-hidden ingestion")

        entries = [dict(entry) for entry in state.summaries]
        base_records = [dict(entry["record"]) for entry in entries]
        base_visible = {
            str(entry["record"]["record_id"])
            for entry in entries
            if entry["raw"]
        }
        triggers = (
            archive_trigger_terms(query.text)
            if self.method_id == LIFECYCLE_RETRIEVAL
            else ()
        )
        usage: dict[str, Any] = {
            "send_eligible": bool(state.ingestion_usage["ingest_eligible"]),
            "safety_reasons": [],
            "archive_access": self.method_id == LIFECYCLE_RETRIEVAL,
            "archive_triggered": bool(triggers),
            "archive_trigger_terms": list(triggers),
            "archive_trigger_revision": "public_lexical_gate_v1",
            "read_event_ids": [],
            "observation_tokens": 0,
            "construction_seconds": self.timings[state.state_hash],
            "materialization_seconds": 0.0,
            "implementation": self.method_id,
            "policy_version": state.ingestion_usage["policy_version"],
            "public_query_hash": stable_digest(query.text),
            "resident_tokens": state.ingestion_usage["resident_tokens"],
        }
        if not state.ingestion_usage["ingest_eligible"]:
            usage["safety_reasons"].append("lifecycle_resident_budget_exceeded")
            usage["materialization_seconds"] = time.perf_counter() - started
            return self._bundle(
                state,
                query,
                records=base_records,
                visible=base_visible,
                retrieved=set(),
                usage=usage,
                budget_tokens=budget_tokens,
            )

        if not triggers:
            usage["materialization_seconds"] = time.perf_counter() - started
            return self._bundle(
                state,
                query,
                records=base_records,
                visible=base_visible,
                retrieved=set(),
                usage=usage,
                budget_tokens=budget_tokens,
            )

        snapshot = self.snapshots[state.prefix_id]
        if snapshot.snapshot_hash != state.ingestion_usage["snapshot_hash"]:
            raise ValueError("archive snapshot changed after ingestion")
        plan = self.policy.materialize(
            snapshot,
            query.text,
            {
                "max_input_tokens": budget_tokens,
                "retrieval_budget_tokens": (
                    budget_tokens
                    - int(state.ingestion_usage["archive_resident_budget_tokens"])
                ),
            },
        )
        span_map = snapshot.span_map()
        selected_ids = {
            node_id
            for span_id in plan.selected_span_ids
            for node_id in span_map[span_id].node_ids
        }
        retrieved = selected_ids - base_visible
        raw_records = [
            event_record(event)
            for event in prefix.events
            if str(event["event_id"]) in selected_ids
        ]

        # A card fully covered by restored raw evidence is redundant.  Hard
        # current constraints remain mandatory; other resident items are added
        # from newest to oldest only while the total fixed budget permits.
        required_entries = [
            entry
            for entry in entries
            if self._is_hard(entry)
            or (
                self._is_card(entry)
                and not set(map(str, entry["source_node_ids"])) <= selected_ids
            )
        ]
        optional_entries = [entry for entry in entries if entry not in required_entries]
        required_records = [
            dict(entry["record"])
            for entry in required_entries
            if not (entry["raw"] and str(entry["record"]["record_id"]) in selected_ids)
        ]
        records = [*raw_records, *required_records]
        if self.count(records) <= budget_tokens:
            for entry in sorted(optional_entries, key=lambda item: (-int(item["step"]), str(item["reason"]))):
                record = dict(entry["record"])
                if entry["raw"] and str(record["record_id"]) in selected_ids:
                    continue
                candidate = [*records, record]
                if self.count(candidate) <= budget_tokens:
                    records = candidate

        positions = {
            str(event["event_id"]): index for index, event in enumerate(prefix.events)
        }
        records.sort(key=lambda record: positions.get(str(record["record_id"]), 10**12))
        usage.update(
            send_eligible=plan.send_eligible and self.count(records) <= budget_tokens,
            safety_reasons=list(plan.safety_reasons),
            read_event_ids=sorted(retrieved),
            observation_tokens=self.count([
                event_record(event)
                for event in prefix.events
                if str(event["event_id"]) in retrieved
            ]),
            policy_plan=plan.to_dict(),
            resident_records_dropped=len(base_records) - len([
                record for record in records
                if str(record["record_id"]).startswith("lifecycle-memory-")
                or str(record["record_id"]) in base_visible
            ]),
            materialization_seconds=time.perf_counter() - started,
        )
        if not plan.send_eligible:
            usage["safety_reasons"] = [
                *usage["safety_reasons"], "archive_plan_not_send_eligible"
            ]
        return self._bundle(
            state,
            query,
            records=records,
            visible=selected_ids | {
                str(record["record_id"])
                for record in records
                if str(record["record_id"]) in base_visible
            },
            retrieved=retrieved,
            usage=usage,
            budget_tokens=budget_tokens,
        )
