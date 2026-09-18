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
    COUNTERFACTUAL_CONDITIONS as COUNTERFACTUAL_CONDITIONS,
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
    prefixes = [PrefixRecord.from_dict(item) for item in prefix_rows]
    queries = [QueryRecord.from_dict(item) for item in query_rows]
    gold = [FailureChainGold.from_dict(item) for item in gold_rows]
    prefix_map = {prefix.prefix_id: prefix for prefix in prefixes}
    for item in gold:
        if item.failure_episode is not None and item.prefix_id in prefix_map:
            item.failure_episode.validate_against_prefix(prefix_map[item.prefix_id])
    return prefixes, queries, gold


# Imported after definitions so mutually-referential helpers initialize safely.
from .adapters import (
    ReferenceMemoryAdapter as ReferenceMemoryAdapter,
    _close_event_protocol as _close_event_protocol,
    _event_map as _event_map,
    _fit_event_ids as _fit_event_ids,
    _record as _record,
)
