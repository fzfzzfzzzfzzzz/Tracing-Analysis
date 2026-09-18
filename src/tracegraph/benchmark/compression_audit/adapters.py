"""Definitions moved from ``tracegraph.compression_audit_runtime``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence
from ...capture import estimate_tokens
from ...message_protocol import close_message_protocol
from ...reactivation import detect_reactivation_trigger, tokenize_terms
from ...compression_audit import BENCHMARK_ID, SCHEMA_VERSION, ContextBundle, EpisodeRecord, FailureChainGold, MemoryArtifact, MemoryState, PrefixRecord, QueryRecord, canonical_json, file_sha256, git_provenance, implementation_provenance, load_config, load_jsonl, stable_digest, verify_file_manifest, write_file_manifest

from .runtime_constants import (
    REFERENCE_METHODS as REFERENCE_METHODS,
)



class CompressionAdapter(Protocol):
    """Query-hidden ingestion followed by query-aware materialization."""

    method_id: str

    def ingest(self, prefix: PrefixRecord, budget_tokens: int) -> MemoryState: ...

    def materialize(
        self,
        state: MemoryState,
        query: QueryRecord,
        budget_tokens: int,
    ) -> ContextBundle: ...


def _event_tokens(event: Mapping[str, Any]) -> int:
    value = event.get("token_count")
    return int(value) if isinstance(value, (int, float)) and value > 0 else estimate_tokens(event)


def _event_map(prefix: PrefixRecord) -> dict[str, Mapping[str, Any]]:
    return {str(item["event_id"]): item for item in prefix.events}


def _record(event: Mapping[str, Any], *, representation: str = "raw") -> dict[str, Any]:
    value = {
        "record_id": str(event["event_id"]),
        "kind": str(event.get("kind", "record")),
        "representation": representation,
        "content": event.get("content"),
    }
    if event.get("tool_name"):
        value["tool_name"] = str(event["tool_name"])
    return value


def _close_event_protocol(prefix: PrefixRecord, event_ids: Sequence[str]) -> tuple[str, ...]:
    """Reuse message closure and preserve call/result pairs without gold labels."""

    selected = set(event_ids)
    call_ids = {
        str(event["call_id"])
        for event in prefix.events
        if str(event["event_id"]) in selected and event.get("call_id")
    }
    selected.update(
        str(event["event_id"])
        for event in prefix.events
        if event.get("call_id") and str(event["call_id"]) in call_ids
    )
    ordinals = {
        int(event["source_message_ordinal"])
        for event in prefix.events
        if str(event["event_id"]) in selected and event.get("source_message_ordinal")
    }
    if ordinals and prefix.messages:
        closure = close_message_protocol([dict(item) for item in prefix.messages], ordinals)
        if not closure.valid:
            raise ValueError("invalid source message protocol: " + "; ".join(closure.errors))
        selected.update(
            str(event["event_id"])
            for event in prefix.events
            if event.get("source_message_ordinal") in closure.ordinals
        )
    return tuple(
        str(event["event_id"])
        for event in prefix.events
        if str(event["event_id"]) in selected
    )


def _observed_failure_chain_ids(prefix: PrefixRecord) -> tuple[str, ...]:
    """Infer candidate chains only from visible event kinds, call IDs and outcomes."""

    events = list(prefix.events)
    call_positions = {
        str(event["call_id"]): index
        for index, event in enumerate(events)
        if event.get("kind") in {"tool_call", "mcp_call"} and event.get("call_id")
    }
    selected: set[str] = set()
    for index, event in enumerate(events):
        content = event.get("content")
        explicit_error = event.get("kind") == "error" or (
            isinstance(content, dict) and bool(content.get("error"))
        )
        if not explicit_error:
            continue
        call_id = str(event.get("call_id") or "")
        start = call_positions.get(call_id)
        if start is None:
            start = next(
                (
                    position
                    for position in range(index - 1, -1, -1)
                    if events[position].get("kind") in {"tool_call", "mcp_call"}
                ),
                index,
            )
        end = index
        for later_index in range(index + 1, len(events)):
            later = events[later_index]
            later_content = later.get("content")
            if later.get("kind") not in {"observation", "tool_result"}:
                continue
            if isinstance(later_content, dict) and later_content.get("error"):
                continue
            later_call = str(later.get("call_id") or "")
            if later_call and later_call != call_id:
                end = later_index
                break
        selected.update(str(item["event_id"]) for item in events[start : end + 1])
    return _close_event_protocol(prefix, tuple(selected))


def _observed_current_ids(prefix: PrefixRecord) -> tuple[str, ...]:
    selected = {
        str(event["event_id"])
        for event in prefix.events
        if event.get("kind") == "constraint"
    }
    if prefix.events:
        selected.add(str(prefix.events[-1]["event_id"]))
    return _close_event_protocol(prefix, tuple(selected))


def _fit_event_ids(
    prefix: PrefixRecord,
    event_ids: Sequence[str],
    budget_tokens: int,
    *,
    newest_first: bool = False,
    forbidden_event_ids: Sequence[str] = (),
    representation: str = "raw",
    extra_records: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, ...]:
    events = _event_map(prefix)
    ordered = [event_id for event_id in event_ids if event_id in events]
    if newest_first:
        ordered = list(reversed(ordered))
    selected: set[str] = set()
    forbidden = set(forbidden_event_ids)
    for event_id in ordered:
        candidate = _close_event_protocol(prefix, tuple(selected.union({event_id})))
        if not forbidden.intersection(candidate) and estimate_tokens(
            [
                *(_record(events[item], representation=representation) for item in candidate),
                *(dict(item) for item in extra_records),
            ]
        ) <= budget_tokens:
            selected.update(candidate)
    return tuple(str(item["event_id"]) for item in prefix.events if str(item["event_id"]) in selected)


def _query_terms(query: QueryRecord) -> set[str]:
    return {item for item in tokenize_terms(query.text) if len(item) >= 4}


def _event_terms(event: Mapping[str, Any]) -> set[str]:
    return set(re.findall(r"[a-z0-9_\-]+", canonical_json(event.get("content")).lower()))


class ReferenceMemoryAdapter:
    """Deterministic reference implementation for M0-M6 and deletion controls."""

    def __init__(
        self,
        method_id: str,
        *,
        deletion_event_ids: Sequence[str] | None = None,
    ) -> None:
        if method_id not in REFERENCE_METHODS:
            raise ValueError(f"unsupported compression-audit method: {method_id}")
        self.method_id = method_id
        self.deletion_event_ids = (
            tuple(deletion_event_ids) if deletion_event_ids is not None else None
        )
        self._prefixes: dict[str, PrefixRecord] = {}
        self._ingestion_seconds: dict[str, float] = {}

    def ingest(self, prefix: PrefixRecord, budget_tokens: int) -> MemoryState:
        """Ingest without accepting or deriving any future query."""

        started = time.perf_counter()
        self._prefixes[prefix.prefix_id] = PrefixRecord.from_dict(prefix.to_dict())
        event_ids = tuple(str(item["event_id"]) for item in prefix.events)
        inferred_chain = _observed_failure_chain_ids(prefix)
        current_ids = _observed_current_ids(prefix)
        summaries: list[dict[str, Any]] = []
        if self.method_id == "M0_full_history":
            retained = event_ids
            archived: tuple[str, ...] = ()
        elif self.method_id == "M1_recent_masking":
            retained = _fit_event_ids(prefix, event_ids, budget_tokens, newest_first=True)
            archived = ()
        elif self.method_id == "M2_flat_lexical_archive":
            retained = _fit_event_ids(prefix, event_ids, budget_tokens, newest_first=True)
            archived = tuple(item for item in event_ids if item not in set(retained))
        elif self.method_id in {
            "M3_lifecycle_eviction_only",
            "M4_lifecycle_flat_reactivation",
            "M5_lifecycle_causal_reactivation",
        }:
            retained = _fit_event_ids(prefix, current_ids, budget_tokens)
            archived = tuple(item for item in event_ids if item not in set(retained))
            summaries.append(
                {
                    "record_id": f"{prefix.prefix_id}:S001",
                    "kind": "archive_handle",
                    "representation": "archive_handle",
                    "content": "Older historical records are available in the archive.",
                }
            )
        elif self.method_id == "M6_fixed_handoff":
            chain = [
                item for item in prefix.events if str(item["event_id"]) in set(inferred_chain)
            ]
            current = [
                item for item in prefix.events if str(item["event_id"]) in set(current_ids)
            ]
            retained = tuple(str(item["event_id"]) for item in (*chain, *current))
            retained = _fit_event_ids(prefix, retained, budget_tokens)
            archived = tuple(item for item in event_ids if item not in set(retained))
            summaries.extend(
                {
                    "record_id": str(item["event_id"]),
                    "kind": str(item.get("kind", "record")),
                    "representation": "fixed_handoff",
                    "content": item.get("content"),
                }
                for item in chain
                if str(item["event_id"]) in retained
            )
        else:
            if self.deletion_event_ids is None:
                raise ValueError("failure-chain deletion requires organizer gold event IDs")
            deleted = set(_close_event_protocol(prefix, self.deletion_event_ids))
            retained = tuple(
                event_id for event_id in event_ids if event_id not in deleted
            )
            retained = _fit_event_ids(
                prefix,
                retained,
                budget_tokens,
                newest_first=True,
                forbidden_event_ids=tuple(deleted),
            )
            archived = ()
        # Metadata and handoff representations consume the same context budget.
        if self.method_id != "M0_full_history":
            events = _event_map(prefix)
            if self.method_id == "M6_fixed_handoff":
                summaries = []
            if estimate_tokens(summaries) > budget_tokens:
                summaries = []
            retained = _fit_event_ids(
                prefix,
                retained,
                budget_tokens,
                newest_first=self.method_id in {"M1_recent_masking", "M2_flat_lexical_archive"},
                forbidden_event_ids=(
                    tuple(deleted) if self.method_id == "failure_chain_deletion" else ()
                ),
                representation=(
                    "fixed_handoff" if self.method_id == "M6_fixed_handoff" else "raw"
                ),
                extra_records=summaries,
            )
            if self.method_id == "M6_fixed_handoff":
                summaries = [
                    _record(events[item], representation="fixed_handoff") for item in retained
                ]
            if self.method_id not in {"M1_recent_masking", "failure_chain_deletion"}:
                archived = tuple(item for item in event_ids if item not in set(retained))
        state = MemoryState.create(
            prefix_id=prefix.prefix_id,
            method_id=self.method_id,
            budget_tokens=budget_tokens,
            retained_event_ids=retained,
            archived_event_ids=archived,
            summaries=summaries,
            ingestion_usage={
                "provider_input_tokens": 0,
                "provider_output_tokens": 0,
                "latency_seconds": 0.0,
                "cost_cny": 0.0,
                "future_query_observed": False,
                "hidden_gold_observed": self.method_id == "failure_chain_deletion",
                "public_causal_labels_used": False,
                "observed_failure_chain_event_ids": list(inferred_chain),
                "lexical_index": {
                    str(event["event_id"]): sorted(_event_terms(event))
                    for event in prefix.events
                    if self.method_id in {"M2_flat_lexical_archive", "M4_lifecycle_flat_reactivation"}
                },
                "implementation": "observed_trace_reference_v1",
            },
        )
        self._ingestion_seconds[state.state_hash] = time.perf_counter() - started
        return state

    def materialize(
        self,
        state: MemoryState,
        query: QueryRecord,
        budget_tokens: int,
    ) -> ContextBundle:
        started = time.perf_counter()
        state.verify_immutable()
        prefix = self._prefixes.get(state.prefix_id)
        if prefix is None or query.prefix_id != state.prefix_id:
            raise ValueError("materialization requires this adapter's query-hidden ingestion")
        if state.prefix_id != prefix.prefix_id or state.method_id != self.method_id:
            raise ValueError("memory state does not belong to this prefix and adapter")
        events = _event_map(prefix)
        selected = set(state.retained_event_ids)
        retrieved: set[str] = set()
        read_ids: set[str] = set()
        trigger = detect_reactivation_trigger(
            {
                "text": query.text,
                "historical_intent": query.track in {"audit_qa", "interactive_reacquisition"},
                "historical_reference": query.track in {"audit_qa", "interactive_reacquisition"},
            }
        )
        historical_query = trigger.active
        if self.method_id in {"M2_flat_lexical_archive", "M4_lifecycle_flat_reactivation"}:
            terms = _query_terms(query)
            lexical_index = state.ingestion_usage["lexical_index"]
            ranked = sorted(
                state.archived_event_ids,
                key=lambda event_id: (
                    -len(terms.intersection(lexical_index[event_id])),
                    int(events[event_id].get("step_id", 0)),
                    event_id,
                ),
            )
            for event_id in ranked:
                if not terms.intersection(lexical_index[event_id]):
                    continue
                candidate = _close_event_protocol(prefix, tuple(selected.union({event_id})))
                read_ids.update(set(candidate).difference(state.retained_event_ids))
                records = [_record(events[item]) for item in candidate]
                records.extend(dict(item) for item in state.summaries)
                if estimate_tokens(records) <= budget_tokens:
                    retrieved.update(set(candidate).difference(selected))
                    selected.update(candidate)
        elif self.method_id == "M5_lifecycle_causal_reactivation" and historical_query:
            causal = list(state.ingestion_usage.get("observed_failure_chain_event_ids", ()))
            for event_id in causal:
                candidate = _close_event_protocol(prefix, tuple(selected.union({event_id})))
                read_ids.update(set(candidate).difference(state.retained_event_ids))
                records = [_record(events[item]) for item in candidate]
                records.extend(dict(item) for item in state.summaries)
                if estimate_tokens(records) <= budget_tokens:
                    retrieved.update(set(candidate).difference(selected))
                    selected.update(candidate)
        ordered = sorted(selected, key=lambda item: (int(events[item]["step_id"]), item))
        if self.method_id == "M6_fixed_handoff":
            summary_by_id = {
                str(item["record_id"]): dict(item) for item in state.summaries
            }
            records = [summary_by_id.get(event_id, _record(events[event_id])) for event_id in ordered]
        else:
            records = [_record(events[event_id]) for event_id in ordered]
            records.extend(dict(item) for item in state.summaries)
        return ContextBundle.create(
            prefix_id=prefix.prefix_id,
            query_id=query.query_id,
            method_id=self.method_id,
            condition_id=("full" if self.method_id == "M0_full_history" else "candidate"),
            records=records,
            visible_event_ids=ordered,
            retrieved_event_ids=sorted(retrieved),
            budget_tokens=(
                max(budget_tokens, estimate_tokens(records))
                if self.method_id == "M0_full_history"
                else budget_tokens
            ),
            retrieval_usage={
                "archive_reads": len(read_ids),
                "read_event_ids": sorted(read_ids),
                "tool_calls": len(read_ids),
                "observation_tokens": sum(_event_tokens(events[item]) for item in read_ids),
                "latency_seconds": time.perf_counter() - started,
                "ingestion_latency_seconds": self._ingestion_seconds.get(state.state_hash, 0.0),
            },
        )


class AconCompressionAdapter:
    """Bridge the existing hash-verified official ACON runtime into this protocol."""

    method_id = "ACON_official"

    def __init__(self, runtime_factory: Callable[[], Any]) -> None:
        self.runtime_factory = runtime_factory
        self._prefixes: dict[str, PrefixRecord] = {}

    def ingest(self, prefix: PrefixRecord, budget_tokens: int) -> MemoryState:
        self._prefixes[prefix.prefix_id] = PrefixRecord.from_dict(prefix.to_dict())
        runtime = self.runtime_factory()
        messages = [dict(item) for item in prefix.messages]
        plan = runtime.prepare(messages, new_indices=tuple(range(len(messages))))
        if not bool(plan.runtime_main_result_eligible):
            raise RuntimeError("official ACON result is ineligible for the main benchmark")
        metadata = plan.metadata()
        if not metadata.get("accounting_complete"):
            raise RuntimeError("official ACON compressor usage is incomplete")
        included = set(int(item) for item in plan.included_indices)
        retained = tuple(
            str(event["event_id"])
            for event in prefix.events
            if int(event.get("source_message_ordinal", 0)) - 1 in included
        )
        summaries = []
        for index, content in sorted(plan.content_overrides.items()):
            record_ids = [
                str(event["event_id"])
                for event in prefix.events
                if int(event.get("source_message_ordinal", 0)) - 1 == index
            ] or [f"{prefix.prefix_id}:ACON:{index:03d}"]
            for record_id in record_ids:
                summaries.append(
                    {
                        "record_id": record_id,
                        "kind": "acon_summary",
                        "representation": "official_acon",
                        "content": content,
                    }
                )
        all_ids = tuple(str(item["event_id"]) for item in prefix.events)
        return MemoryState.create(
            prefix_id=prefix.prefix_id,
            method_id=self.method_id,
            budget_tokens=budget_tokens,
            retained_event_ids=retained,
            archived_event_ids=tuple(item for item in all_ids if item not in set(retained)),
            summaries=summaries,
            ingestion_usage={
                "provider_input_tokens": int(
                    metadata.get("compressor_provider_input_tokens", 0)
                ),
                "provider_output_tokens": int(
                    metadata.get("compressor_provider_output_tokens", 0)
                ),
                "latency_seconds": float(metadata.get("compressor_latency_seconds", 0.0)),
                "cost_cny": 0.0,
                "cost_usd": float(metadata.get("compressor_cost_usd", 0.0)),
                "accounting_complete": bool(metadata.get("accounting_complete")),
                "runtime_main_result_eligible": True,
                "future_query_observed": False,
                "provenance": dict(metadata.get("provenance") or {}),
            },
        )

    def materialize(
        self,
        state: MemoryState,
        query: QueryRecord,
        budget_tokens: int,
    ) -> ContextBundle:
        state.verify_immutable()
        prefix = self._prefixes.get(state.prefix_id)
        if prefix is None or query.prefix_id != state.prefix_id:
            raise ValueError("ACON materialization requires query-hidden ingestion")
        if state.method_id != self.method_id or state.prefix_id != prefix.prefix_id:
            raise ValueError("ACON memory state does not match the prefix")
        events = _event_map(prefix)
        summary_by_id = {str(item["record_id"]): dict(item) for item in state.summaries}
        records = [
            summary_by_id.get(event_id, _record(events[event_id]))
            for event_id in state.retained_event_ids
            if event_id in events
        ]
        represented = set(state.retained_event_ids)
        records.extend(
            dict(item)
            for item in state.summaries
            if str(item["record_id"]) not in represented
        )
        if estimate_tokens(records) > budget_tokens:
            raise RuntimeError("official ACON context exceeds the fixed benchmark budget")
        return ContextBundle.create(
            prefix_id=prefix.prefix_id,
            query_id=query.query_id,
            method_id=self.method_id,
            condition_id="candidate",
            records=records,
            visible_event_ids=state.retained_event_ids,
            budget_tokens=budget_tokens,
            retrieval_usage={
                "archive_reads": 0,
                "tool_calls": 0,
                "observation_tokens": 0,
            },
        )
