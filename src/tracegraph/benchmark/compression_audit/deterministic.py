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
    RANKED_REFERENCE_METHODS as RANKED_REFERENCE_METHODS,
)
from .development_protocol import DEVELOPMENT_PROTOCOL, development_metadata



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
    protocol: str = DEVELOPMENT_PROTOCOL,
) -> dict[str, Any]:
    if protocol not in {"v0.1-diagnostic", DEVELOPMENT_PROTOCOL}:
        raise ValueError(f"unsupported deterministic protocol: {protocol}")
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
            row = episode.to_dict()
            if protocol == DEVELOPMENT_PROTOCOL:
                from .development_scoring import (
                    canonical_answer, make_rubric, provider_response,
                    rule_judge_fixture, wire_answer,
                )
                from .development_protocol import parse_development_submission

                rubric = make_rubric(query, gold)
                enough = any(set(ids) <= set(bundle.visible_event_ids)
                             for ids in rubric["alternative_evidence_sets"])
                wire = canonical_answer(rubric) if enough else {
                    "a": "Insufficient visible history.", "e": [],
                    "t": rubric["expected_scope"], "s": False}
                row.update(protocol=protocol,
                    answer=wire_answer(parse_development_submission(provider_response(wire))),
                    rubric=rubric, judge=rule_judge_fixture(wire, rubric),
                    judge_calibrated=True, judge_source="offline_rule_fixture",
                    evidence_event_ids=wire["e"])
            rows.append(row)
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
        "protocol": protocol,
        "development_only": True,
    }
    (output_root / "run_config.json").write_text(
        json.dumps(run_config, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifacts = write_file_manifest(output_root)
    manifest = {
        **development_metadata(run_id=stable_digest(run_config)[:16]),
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


# Imported after definitions so mutually-referential helpers initialize safely.
from .adapters import (
    ReferenceMemoryAdapter as ReferenceMemoryAdapter,
)

from .artifacts import (
    load_dataset as load_dataset,
    memory_artifact as memory_artifact,
)
