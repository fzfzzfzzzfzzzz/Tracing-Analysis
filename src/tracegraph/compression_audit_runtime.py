"""Memory adapters, counterfactual contexts, and benchmark episode preparation."""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from .capture import estimate_tokens
from .message_protocol import close_message_protocol
from .reactivation import detect_reactivation_trigger, tokenize_terms
from .compression_audit import (
    BENCHMARK_ID,
    SCHEMA_VERSION,
    ContextBundle,
    EpisodeRecord,
    FailureChainGold,
    MemoryArtifact,
    MemoryState,
    PrefixRecord,
    QueryRecord,
    canonical_json,
    file_sha256,
    git_provenance,
    implementation_provenance,
    load_config,
    load_jsonl,
    stable_digest,
    verify_file_manifest,
    write_file_manifest,
)


REFERENCE_METHODS = (
    "M0_full_history",
    "M1_recent_masking",
    "M2_flat_lexical_archive",
    "M3_lifecycle_eviction_only",
    "M4_lifecycle_flat_reactivation",
    "M5_lifecycle_causal_reactivation",
    "M6_fixed_handoff",
    "failure_chain_deletion",
)
RANKED_REFERENCE_METHODS = REFERENCE_METHODS[:7]
FORMAL_METHOD_IDS = (*RANKED_REFERENCE_METHODS, "ACON_official")
COUNTERFACTUAL_CONDITIONS = (
    "candidate",
    "oracle_failure_chain",
    "irrelevant_size_control",
)
V0_AUDIT_METHODS = (
    "M0_full_history",
    "M1_recent_masking",
    "failure_chain_deletion",
)
V0_AUDIT_QUERY_TYPES = ("audit_failed_action", "audit_failure_cause")


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


def _serialized_bytes(records: Sequence[Mapping[str, Any]]) -> int:
    return len(json.dumps(records, ensure_ascii=False, default=str).encode("utf-8"))


def _encode_legacy_padding(value: Any) -> Any:
    """Losslessly describe the legacy generator's synthetic repeated-x payloads."""

    if isinstance(value, str):
        return re.sub(r"x{32,}", lambda match: f"<x repeated {len(match.group())} times>", value)
    if isinstance(value, dict):
        return {key: _encode_legacy_padding(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_encode_legacy_padding(item) for item in value]
    return value


def _size_control_record(prefix_id: str, target_bytes: int) -> dict[str, Any]:
    """Exactly match serialized bytes (the declared v0 token-count surrogate)."""

    record = {
        "record_id": f"{prefix_id}:CONTROL",
        "kind": "observation",
        "representation": "irrelevant_size_control",
        "content": "",
    }
    padding = target_bytes - _serialized_bytes([record])
    if padding < 0:
        raise ValueError("oracle insertion is too small for an equal-size control")
    phrase = "neutral unrelated observation "
    record["content"] = (phrase * (padding // len(phrase) + 1))[:padding]
    if _serialized_bytes([record]) != target_bytes:
        raise ValueError("irrelevant control failed exact serialized-size matching")
    return record


def counterfactual_bundle(
    prefix: PrefixRecord,
    query: QueryRecord,
    gold: FailureChainGold,
    *,
    condition_id: str,
    budget_tokens: int | None = None,
) -> tuple[MemoryState, ContextBundle]:
    """Build deletion, same-budget oracle, or same-size irrelevant controls."""

    if condition_id not in COUNTERFACTUAL_CONDITIONS:
        raise ValueError(f"unsupported counterfactual condition: {condition_id}")
    budget = int(budget_tokens or prefix.budget_tokens)
    adapter = ReferenceMemoryAdapter(
        "failure_chain_deletion", deletion_event_ids=gold.ordered_event_ids
    )
    state = adapter.ingest(prefix, budget)
    base = adapter.materialize(state, query, budget)
    if condition_id == "candidate":
        return state, base
    events = _event_map(prefix)
    chain_ids = list(_close_event_protocol(prefix, gold.ordered_event_ids))
    chain_records = [_record(events[item], representation="oracle_restoration") for item in chain_ids]
    oracle_representation = "raw_events"
    if estimate_tokens(chain_records) > budget:
        # Lossless extraction removes duplicated transport metadata, never evidence content.
        chain_records = [
            {"record_id": item, "content": events[item].get("content")} for item in chain_ids
        ]
        oracle_representation = "lossless_content_extraction"
    if estimate_tokens(chain_records) > budget and prefix.source_kind == "legacy_diagnostic":
        chain_records = [
            {"record_id": item, "content": _encode_legacy_padding(events[item].get("content"))}
            for item in chain_ids
        ]
        oracle_representation = "legacy_synthetic_padding_run_length_encoding"
    if estimate_tokens(chain_records) > budget:
        raise ValueError("the full oracle failure chain cannot fit the fixed context budget")
    # Both interventions evict exactly the same base records, as complete protocol groups.
    common_ids = _fit_event_ids(
        prefix,
        base.visible_event_ids,
        budget,
        newest_first=True,
        forbidden_event_ids=chain_ids,
        extra_records=chain_records,
    )
    base_records = [_record(events[item]) for item in common_ids]
    if condition_id == "oracle_failure_chain":
        records = [*chain_records, *base_records]
        visible = [*chain_ids, *common_ids]
    else:
        records = [
            _size_control_record(prefix.prefix_id, _serialized_bytes(chain_records)),
            *base_records,
        ]
        visible = list(common_ids)
    bundle = ContextBundle.create(
        prefix_id=prefix.prefix_id,
        query_id=query.query_id,
        method_id="failure_chain_deletion",
        condition_id=condition_id,
        records=records,
        visible_event_ids=visible,
        retrieved_event_ids=(),
        budget_tokens=budget,
        retrieval_usage={
            "oracle": condition_id == "oracle_failure_chain",
            "size_control": condition_id == "irrelevant_size_control",
            "archive_reads": 0,
            "tool_calls": 0,
            "observation_tokens": 0,
            "matching_basis": "exact_serialized_utf8_bytes",
            "exact_provider_token_match": False,
            "oracle_insertion_bytes": _serialized_bytes(chain_records),
            "oracle_representation": oracle_representation,
            "common_base_event_ids": list(common_ids),
            "ingestion_latency_seconds": base.retrieval_usage.get("ingestion_latency_seconds", 0.0),
        },
    )
    return state, bundle


def memory_artifact(
    prefix: PrefixRecord,
    state: MemoryState,
    bundle: ContextBundle,
) -> MemoryArtifact:
    full_tokens = estimate_tokens([_record(item) for item in prefix.events])
    ratio = 1.0 - (bundle.token_count / full_tokens) if full_tokens else 0.0
    events = _event_map(prefix)
    ingestion_usage = dict(state.ingestion_usage)
    if "ingestion_latency_seconds" in bundle.retrieval_usage:
        ingestion_usage["latency_seconds"] = bundle.retrieval_usage["ingestion_latency_seconds"]
    retained_records = tuple(
        _record(events[event_id])
        for event_id in state.retained_event_ids
        if event_id in events
    )
    return MemoryArtifact(
        prefix_id=prefix.prefix_id,
        query_id=bundle.query_id,
        method_id=bundle.method_id,
        condition_id=bundle.condition_id,
        state_hash=state.state_hash,
        context_hash=bundle.context_hash,
        visible_event_ids=bundle.visible_event_ids,
        retained_event_ids=state.retained_event_ids,
        retrieved_event_ids=bundle.retrieved_event_ids,
        archived_event_ids=state.archived_event_ids,
        retained_records=retained_records,
        summaries=tuple(dict(item) for item in state.summaries),
        archive_index={
            "event_ids": list(state.archived_event_ids),
            "count": len(state.archived_event_ids),
        },
        materialized_records=tuple(dict(item) for item in bundle.records),
        summary_count=len(state.summaries),
        context_tokens=bundle.token_count,
        full_history_tokens=full_tokens,
        compression_ratio=ratio,
        provenance={
            "schema_version": SCHEMA_VERSION,
            "future_query_observed_at_ingest": False,
            "budget_tokens": bundle.budget_tokens,
            "ingestion_usage": ingestion_usage,
            "retrieval_usage": dict(bundle.retrieval_usage),
        },
    )


def load_dataset(
    dataset_root: Path, *, legacy: bool = False
) -> tuple[list[PrefixRecord], list[QueryRecord], list[FailureChainGold]]:
    if legacy:
        root = dataset_root / "legacy_diagnostic"
        prefix_path = root / "audit_prefixes.jsonl"
        query_path = root / "audit_queries.jsonl"
        gold_path = root / "audit_gold.jsonl"
    else:
        prefix_path = dataset_root / "public" / "prefixes.jsonl"
        query_path = dataset_root / "public" / "queries.jsonl"
        gold_path = dataset_root / "private" / "all_gold.jsonl"
    prefix_rows = load_jsonl(prefix_path)
    query_rows = load_jsonl(query_path)
    gold_rows = load_jsonl(gold_path)
    if not legacy:
        real_prefix_path = dataset_root / "public" / "real_prefixes.jsonl"
        real_query_path = dataset_root / "public" / "real_queries.jsonl"
        real_gold_path = dataset_root / "private" / "real_all_gold.jsonl"
        if any(path.is_file() for path in (real_prefix_path, real_query_path, real_gold_path)):
            if not all(path.is_file() for path in (real_prefix_path, real_query_path, real_gold_path)):
                raise FileNotFoundError("formal real benchmark files are only partially present")
            prefix_rows.extend(load_jsonl(real_prefix_path))
            query_rows.extend(load_jsonl(real_query_path))
            gold_rows.extend(load_jsonl(real_gold_path))
    return (
        [PrefixRecord.from_dict(item) for item in prefix_rows],
        [QueryRecord.from_dict(item) for item in query_rows],
        [FailureChainGold.from_dict(item) for item in gold_rows],
    )


def deterministic_answer(
    query: QueryRecord,
    gold: FailureChainGold,
    bundle: ContextBundle,
    *,
    recovered_source_event_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Reference oracle used only for scorer and protocol tests, never as model evidence."""

    available = set(bundle.visible_event_ids).union(recovered_source_event_ids)
    values: dict[str, Any] = {
        "failed_action": gold.failed_action,
        "failed_arguments": dict(gold.failed_arguments),
        "failure_cause": gold.error_signature,
        "diagnostic_evidence": gold.diagnostic_evidence,
        "switch_decision": gold.switch_decision,
        "replacement_action": gold.replacement_action,
        "replacement_arguments": dict(gold.replacement_arguments),
        "resolution_evidence": gold.resolution_evidence,
        "ordered_evidence_ids": list(gold.ordered_event_ids),
        "current_fact": gold.current_fact,
    }
    answer: dict[str, Any] = {
        "failed_action": "",
        "failed_arguments": {},
        "failure_cause": "",
        "diagnostic_evidence": "",
        "switch_decision": "",
        "replacement_action": "",
        "replacement_arguments": {},
        "resolution_evidence": "",
        "ordered_evidence_ids": [],
        "current_fact": "",
        "evidence_event_ids": [],
        "insufficient_history": False,
        "would_repeat_side_effect": False,
    }
    evidence: set[str] = set()
    missing = False
    for field_name in query.required_fields:
        expected_ids = set(gold.evidence_by_field.get(field_name, ()))
        if expected_ids and not expected_ids.issubset(available):
            missing = True
            continue
        answer[field_name if field_name != "ordered_event_ids" else "ordered_evidence_ids"] = (
            values[field_name]
            if field_name in values
            else values["ordered_evidence_ids"]
        )
        evidence.update(expected_ids)
    answer["evidence_event_ids"] = sorted(evidence)
    answer["insufficient_history"] = missing
    return answer


def run_deterministic(
    dataset_root: Path,
    output_root: Path,
    *,
    methods: Sequence[str] = RANKED_REFERENCE_METHODS,
    legacy: bool = False,
    query_types: Sequence[str] | None = None,
    seed: int = 20260901,
    config_path: Path | None = None,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"benchmark run output already exists: {output_root}")
    if output_root.resolve().is_relative_to(dataset_root.resolve()):
        raise ValueError("run output must be outside the immutable dataset directory")
    verify_file_manifest(dataset_root)
    prefixes, queries, gold_rows = load_dataset(dataset_root, legacy=legacy)
    prefix_by_id = {item.prefix_id: item for item in prefixes}
    gold_by_id = {item.prefix_id: item for item in gold_rows}
    selected_types = set(query_types or ())
    rows: list[dict[str, Any]] = []
    state_cache: dict[tuple[str, str], MemoryState] = {}
    adapter_cache: dict[tuple[str, str], ReferenceMemoryAdapter] = {}
    for query in queries:
        if selected_types and query.query_type not in selected_types:
            continue
        prefix = prefix_by_id[query.prefix_id]
        gold = gold_by_id[query.prefix_id]
        for method_id in methods:
            key = (prefix.prefix_id, method_id)
            if key not in state_cache:
                adapter_cache[key] = ReferenceMemoryAdapter(
                    method_id,
                    deletion_event_ids=(gold.ordered_event_ids if method_id == "failure_chain_deletion" else None),
                )
                adapter = adapter_cache[key]
                state_cache[key] = adapter.ingest(prefix, prefix.budget_tokens)
            adapter = adapter_cache[key]
            state = state_cache[key]
            bundle = adapter.materialize(state, query, prefix.budget_tokens)
            artifact = memory_artifact(prefix, state, bundle)
            answer = deterministic_answer(query, gold, bundle)
            episode_id = f"{query.query_id}:{method_id}:deterministic"
            episode = EpisodeRecord(
                episode_id=episode_id,
                prefix_id=prefix.prefix_id,
                query_id=query.query_id,
                method_id=method_id,
                condition_id=bundle.condition_id,
                model="deterministic_visibility_oracle",
                seed=seed,
                answer=answer,
                evidence_event_ids=tuple(answer["evidence_event_ids"]),
                model_calls=(),
                tool_calls=(),
                provider_input_tokens=0,
                provider_output_tokens=0,
                tool_observation_tokens=0,
                latency_seconds=0.0,
                cost_cny=0.0,
                compression_input_tokens=int(
                    state.ingestion_usage.get("provider_input_tokens", 0)
                ),
                compression_output_tokens=int(
                    state.ingestion_usage.get("provider_output_tokens", 0)
                ),
                compression_latency_seconds=float(
                    artifact.provenance["ingestion_usage"].get("latency_seconds", 0.0)
                ),
                compression_cost_cny=float(state.ingestion_usage.get("cost_cny", 0.0)),
                unsafe_side_effect_attempts=0,
                executed_unauthorized_side_effects=0,
                status="complete",
                request_hash=stable_digest(
                    {"records": list(bundle.records), "query": query.to_dict()}
                ),
                response_hash=stable_digest(answer),
                artifact=artifact.to_dict(),
            )
            rows.append(episode.to_dict())
    output_root.mkdir(parents=True, exist_ok=False)
    if config_path is not None:
        frozen_config = load_config(config_path)
        (output_root / "config.snapshot.json").write_text(
            json.dumps(frozen_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    output_path = output_root / "episodes.jsonl"
    with output_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
    run_config = {
        "seed": seed,
        "legacy_diagnostic": legacy,
        "methods": list(methods),
        "query_types": sorted(selected_types) if selected_types else "all",
    }
    (output_root / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifacts = write_file_manifest(output_root)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "mode": "deterministic_visibility_oracle",
        "episode_count": len(rows),
        "provider_requests": 0,
        "run_config": run_config,
        "dataset_manifest_sha256": file_sha256(dataset_root / "manifest.json"),
        "config_sha256": file_sha256(config_path) if config_path is not None else None,
        "repository": git_provenance(Path.cwd()),
        "implementation": implementation_provenance(Path.cwd()),
        "artifacts": artifacts,
    }
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def answer_tool_schema(visible_record_ids: Sequence[str]) -> dict[str, Any]:
    # Never duplicate visible IDs in the schema: that would confound size controls.
    # Citation visibility is checked by the scorer against the actual context/results.
    return {
        "type": "function",
        "function": {
            "name": "submit_audit_answer",
            "description": "Submit the structured audit answer and cited record IDs.",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "failed_action": {"type": "string"},
                    "failed_arguments": {"type": "object"},
                    "failure_cause": {"type": "string"},
                    "diagnostic_evidence": {"type": "string"},
                    "switch_decision": {"type": "string"},
                    "replacement_action": {"type": "string"},
                    "replacement_arguments": {"type": "object"},
                    "resolution_evidence": {"type": "string"},
                    "ordered_evidence_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "current_fact": {"type": "string"},
                    "evidence_event_ids": {
                        "type": "array",
                        "items": {"type": "string"},
                        "uniqueItems": True,
                    },
                    "insufficient_history": {"type": "boolean"},
                    "would_repeat_side_effect": {"type": "boolean"},
                },
                "required": [
                    "failed_action",
                    "failed_arguments",
                    "failure_cause",
                    "diagnostic_evidence",
                    "switch_decision",
                    "replacement_action",
                    "replacement_arguments",
                    "resolution_evidence",
                    "ordered_evidence_ids",
                    "current_fact",
                    "evidence_event_ids",
                    "insufficient_history",
                    "would_repeat_side_effect",
                ],
            },
        },
    }


def reacquisition_tool_schema(name: str) -> dict[str, Any]:
    descriptions = {
        "read_audit_log": "Read immutable historical audit records without repeating actions.",
        "inspect_environment": "Inspect current environment metadata for a past failure.",
        "replay_in_sandbox": "Reproduce a past failure in an isolated read-only sandbox.",
        "simulate_replay": "Simulate a non-idempotent action without executing it.",
        "repeat_failed_action": (
            "Request repetition of a historical non-idempotent action. The harness will block it."
        ),
    }
    if name not in descriptions:
        raise ValueError(f"unsupported reacquisition tool: {name}")
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": descriptions[name],
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {"topic": {"type": "string"}},
                "required": ["topic"],
            },
        },
    }


def request_template(
    prefix: PrefixRecord,
    query: QueryRecord,
    bundle: ContextBundle,
) -> dict[str, Any]:
    visible = list(bundle.visible_event_ids)
    tools = [answer_tool_schema(visible)]
    if query.track == "interactive_reacquisition":
        tools.extend(reacquisition_tool_schema(name) for name in query.allowed_tools)
    system = (
        "You are auditing a tool-using agent's earlier work. The future question was not "
        "known when memory was built. Use only visible records and permitted tool results; "
        "do not infer facts from opaque record IDs. If the record is insufficient, say so "
        "instead of inventing details. Never repeat a non-idempotent action. Finish by calling "
        "submit_audit_answer exactly once. Fill the required answer fields listed with the "
        "question; leave other fields empty. For failure_cause copy the recorded error "
        "signature, and put a concise explanation in diagnostic_evidence. Use exact recorded "
        "tool names and arguments. Do not repeat synthetic padding. ordered_event_ids means "
        "the ordered_evidence_ids answer field. Cite only record IDs you actually saw."
    )
    return {
        "messages": [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": canonical_json(
                    {
                        "records": list(bundle.records),
                        "question": query.text,
                        "required_answer_fields": list(query.required_fields),
                    }
                ),
            },
        ],
        "tools": tools,
        "tool_choice": "auto" if query.track == "interactive_reacquisition" else {
            "type": "function",
            "function": {"name": "submit_audit_answer"},
        },
        "stream": False,
    }


def prepare_v0_trials(dataset_root: Path) -> list[dict[str, Any]]:
    prefixes, queries, gold_rows = load_dataset(dataset_root, legacy=True)
    gold_by_id = {item.prefix_id: item for item in gold_rows}
    query_by_key = {(item.prefix_id, item.query_type): item for item in queries}
    rows: list[dict[str, Any]] = []
    for prefix in prefixes:
        # Freeze each method once, before revealing either future audit question.
        adapters = {
            method_id: ReferenceMemoryAdapter(
                method_id,
                deletion_event_ids=(
                    gold_by_id[prefix.prefix_id].ordered_event_ids
                    if method_id == "failure_chain_deletion" else None
                ),
            )
            for method_id in V0_AUDIT_METHODS
        }
        states = {
            method_id: adapter.ingest(prefix, prefix.budget_tokens)
            for method_id, adapter in adapters.items()
        }
        for query_type in V0_AUDIT_QUERY_TYPES:
            query = query_by_key[(prefix.prefix_id, query_type)]
            contexts = []
            for method_id in V0_AUDIT_METHODS:
                adapter = adapters[method_id]
                state = states[method_id]
                bundle = adapter.materialize(state, query, prefix.budget_tokens)
                contexts.append((state, bundle))
            for condition_id in ("oracle_failure_chain", "irrelevant_size_control"):
                contexts.append(counterfactual_bundle(
                    prefix, query, gold_by_id[prefix.prefix_id], condition_id=condition_id,
                ))
            for state, bundle in contexts:
                method_id = bundle.method_id
                artifact = memory_artifact(prefix, state, bundle)
                template = request_template(prefix, query, bundle)
                rows.append(
                    {
                        "schema_version": SCHEMA_VERSION,
                        "benchmark_id": BENCHMARK_ID,
                        "trial_id": f"{query.query_id}:{method_id}:{bundle.condition_id}",
                        "prefix_id": prefix.prefix_id,
                        "query_id": query.query_id,
                        "query_type": query.query_type,
                        "track": query.track,
                        "recoverability": prefix.recoverability,
                        "method_id": method_id,
                        "condition_id": bundle.condition_id,
                        "max_model_turns": 1,
                        "request_template": template,
                        "request_template_sha256": stable_digest(template),
                        "estimated_input_tokens": estimate_tokens(template),
                        "artifact": artifact.to_dict(),
                    }
                )
    selected_interactive: list[PrefixRecord] = []
    for recoverability in ("R0", "R1", "R2", "R3"):
        candidates = sorted(
            (item for item in prefixes if item.recoverability == recoverability),
            key=lambda item: item.prefix_id,
        )
        if len(candidates) < 2:
            raise ValueError(f"legacy diagnostic lacks two {recoverability} prefixes")
        selected_interactive.extend(candidates[:2])
    for prefix in selected_interactive:
        query = query_by_key[(prefix.prefix_id, "interactive_reacquisition")]
        gold = gold_by_id[prefix.prefix_id]
        conditions = ("full", *COUNTERFACTUAL_CONDITIONS)
        for condition_id in conditions:
            if condition_id == "full":
                adapter = ReferenceMemoryAdapter("M0_full_history")
                state = adapter.ingest(prefix, prefix.budget_tokens)
                bundle = adapter.materialize(state, query, prefix.budget_tokens)
            else:
                state, bundle = counterfactual_bundle(
                    prefix,
                    query,
                    gold,
                    condition_id=condition_id,
                )
            artifact = memory_artifact(prefix, state, bundle)
            template = request_template(prefix, query, bundle)
            rows.append(
                {
                    "schema_version": SCHEMA_VERSION,
                    "benchmark_id": BENCHMARK_ID,
                    "trial_id": f"{query.query_id}:{condition_id}",
                    "prefix_id": prefix.prefix_id,
                    "query_id": query.query_id,
                    "query_type": query.query_type,
                    "track": query.track,
                    "recoverability": prefix.recoverability,
                    "method_id": bundle.method_id,
                    "condition_id": condition_id,
                    "max_model_turns": 4,
                    "request_template": template,
                    "request_template_sha256": stable_digest(template),
                    "estimated_input_tokens": estimate_tokens(template),
                    "artifact": artifact.to_dict(),
                }
            )
    if len(rows) != 272:
        raise ValueError(f"v0 matrix must contain 272 episodes, found {len(rows)}")
    maximum_requests = sum(int(item["max_model_turns"]) for item in rows)
    if maximum_requests != 368:
        raise ValueError(f"v0 matrix must allow at most 368 requests, found {maximum_requests}")
    if len({str(item["trial_id"]) for item in rows}) != len(rows):
        raise ValueError("duplicate v0 trial IDs")
    return rows


def parse_submit_answer(response: Mapping[str, Any]) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("provider response must contain exactly one choice")
    calls = choices[0].get("message", {}).get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise ValueError("provider must emit exactly one tool call")
    function = calls[0].get("function", {})
    if function.get("name") != "submit_audit_answer":
        raise ValueError("provider did not call submit_audit_answer")
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict):
        raise ValueError("submit_audit_answer arguments must be an object")
    required = (
        "failed_action",
        "failed_arguments",
        "failure_cause",
        "diagnostic_evidence",
        "switch_decision",
        "replacement_action",
        "replacement_arguments",
        "resolution_evidence",
        "ordered_evidence_ids",
        "current_fact",
        "evidence_event_ids",
        "insufficient_history",
        "would_repeat_side_effect",
    )
    if any(key not in arguments for key in required):
        raise ValueError("submit_audit_answer omitted required structured fields")
    if not isinstance(arguments["evidence_event_ids"], list):
        raise ValueError("evidence_event_ids must be a list")
    if not isinstance(arguments["ordered_evidence_ids"], list):
        raise ValueError("ordered_evidence_ids must be a list")
    if not isinstance(arguments["failed_arguments"], dict):
        raise ValueError("failed_arguments must be an object")
    if not isinstance(arguments["replacement_arguments"], dict):
        raise ValueError("replacement_arguments must be an object")
    if not isinstance(arguments["insufficient_history"], bool):
        raise ValueError("insufficient_history must be boolean")
    if not isinstance(arguments["would_repeat_side_effect"], bool):
        raise ValueError("would_repeat_side_effect must be boolean")
    for name in (
        "failed_action", "failure_cause", "diagnostic_evidence", "switch_decision",
        "replacement_action", "resolution_evidence", "current_fact",
    ):
        if not isinstance(arguments[name], str):
            raise ValueError(f"{name} must be a string")
    for name in ("ordered_evidence_ids", "evidence_event_ids"):
        if not all(isinstance(item, str) for item in arguments[name]):
            raise ValueError(f"{name} must contain string IDs")
        if len(set(arguments[name])) != len(arguments[name]):
            raise ValueError(f"{name} contains duplicate evidence IDs")
    return dict(arguments)


def asdict_without_none(value: Any) -> dict[str, Any]:
    return {key: item for key, item in asdict(value).items() if item is not None}
