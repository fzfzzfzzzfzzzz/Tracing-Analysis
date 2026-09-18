"""Definitions moved from ``tracegraph.compression_audit``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from ...capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens

from .constants import (
    BENCHMARK_ID as BENCHMARK_ID,
    QUERY_TYPES as QUERY_TYPES,
    RECOVERABILITY_LEVELS as RECOVERABILITY_LEVELS,
    SPLITS as SPLITS,
    VALIDATION_SCHEMA_VERSION as VALIDATION_SCHEMA_VERSION,
)



def validate_benchmark(dataset_root: Path) -> dict[str, Any]:
    public = dataset_root / "public"
    private = dataset_root / "private"
    errors: list[str] = []
    warnings: list[str] = []
    try:
        prefixes = [PrefixRecord.from_dict(row) for row in load_jsonl(public / "prefixes.jsonl")]
        queries = [QueryRecord.from_dict(row) for row in load_jsonl(public / "queries.jsonl")]
        gold = [FailureChainGold.from_dict(row) for row in load_jsonl(private / "all_gold.jsonl")]
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
        return {
            "schema_version": VALIDATION_SCHEMA_VERSION,
            "benchmark_id": BENCHMARK_ID,
            "diagnostic_ready": False,
            "v1_ready": False,
            "errors": [str(error)],
            "warnings": [],
        }
    try:
        claimed_artifacts = load_jsonl(dataset_root / "file_manifest.jsonl")
        claimed_by_path = {str(item.get("path")): item for item in claimed_artifacts}
        actual_artifacts = artifact_manifest(dataset_root)
        actual_by_path = {str(item["path"]): item for item in actual_artifacts}
        if len(claimed_by_path) != len(claimed_artifacts):
            errors.append("file manifest contains duplicate paths")
        if set(claimed_by_path) != set(actual_by_path):
            errors.append("file manifest paths do not match benchmark artifacts")
        else:
            for path, actual in actual_by_path.items():
                claimed = claimed_by_path[path]
                if claimed.get("sha256") != actual["sha256"] or int(
                    claimed.get("bytes", -1)
                ) != int(actual["bytes"]):
                    errors.append(f"file manifest mismatch: {path}")
    except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
        errors.append(f"file manifest is invalid: {error}")
    prefix_by_id = {item.prefix_id: item for item in prefixes}
    gold_by_id = {item.prefix_id: item for item in gold}
    if len(prefix_by_id) != len(prefixes):
        errors.append("duplicate prefix IDs")
    if len({item.query_id for item in queries}) != len(queries):
        errors.append("duplicate query IDs")
    if len(prefixes) != 240:
        errors.append(f"expected 240 controlled prefixes, found {len(prefixes)}")
    if len(queries) != 1440:
        errors.append(f"expected 1440 controlled queries, found {len(queries)}")
    if len(gold) != 240:
        errors.append(f"expected 240 controlled gold rows, found {len(gold)}")

    queries_by_prefix: dict[str, list[QueryRecord]] = {}
    for query in queries:
        queries_by_prefix.setdefault(query.prefix_id, []).append(query)
        if query.prefix_id not in prefix_by_id:
            errors.append(f"query references missing prefix: {query.query_id}")
            continue
        if query.prefix_id in gold_by_id:
            errors.extend(_query_leaks_gold(query, gold_by_id[query.prefix_id]))
        if query.track == "audit_qa" and query.allowed_tools:
            errors.append(f"Audit-QA query exposes tools: {query.query_id}")
    for prefix in prefixes:
        if any("causal_role" in event for event in prefix.events):
            errors.append(f"hidden causal labels leaked into public prefix: {prefix.prefix_id}")
        kinds = {item.query_type for item in queries_by_prefix.get(prefix.prefix_id, ())}
        if kinds != set(QUERY_TYPES):
            errors.append(f"prefix does not have exactly six query types: {prefix.prefix_id}")
        errors.extend(_tool_pair_errors(prefix))
        item_gold = gold_by_id.get(prefix.prefix_id)
        if item_gold is None:
            errors.append(f"missing gold: {prefix.prefix_id}")
            continue
        event_ids = {str(item["event_id"]) for item in prefix.events}
        if not set(item_gold.ordered_event_ids).issubset(event_ids):
            errors.append(f"gold references missing event: {prefix.prefix_id}")
        steps = {str(item["event_id"]): int(item["step_id"]) for item in prefix.events}
        order = [steps[item] for item in item_gold.ordered_event_ids]
        if order != sorted(order):
            errors.append(f"failure chain is not chronological: {prefix.prefix_id}")
        if prefix.recoverability == "R0" and prefix.environment_snapshot.get(
            "history_reconstructable"
        ):
            errors.append(f"R0 history is incorrectly reconstructable: {prefix.prefix_id}")
        if prefix.recoverability == "R3" and not prefix.environment_snapshot.get(
            "unsafe_to_repeat_failed_action"
        ):
            errors.append(f"R3 prefix does not block unsafe replay: {prefix.prefix_id}")

    families_by_split = {
        split: {item.failure_family for item in prefixes if item.split == split}
        for split in SPLITS
    }
    for left_index, left in enumerate(SPLITS):
        for right in SPLITS[left_index + 1 :]:
            overlap = families_by_split[left].intersection(families_by_split[right])
            if overlap:
                errors.append(f"failure families leak across {left}/{right}: {sorted(overlap)}")
    split_counts = {split: sum(item.split == split for item in prefixes) for split in SPLITS}
    if split_counts != {"dev": 48, "validation": 48, "test": 144}:
        errors.append(f"unexpected split counts: {split_counts}")

    public_gold = load_jsonl(public / "dev_validation_gold.jsonl")
    if any(
        str(item.get("prefix_id")) not in prefix_by_id
        or prefix_by_id[str(item.get("prefix_id"))].split == "test"
        for item in public_gold
    ):
        errors.append("test gold leaked into public gold")
    try:
        legacy_prefixes = [
            PrefixRecord.from_dict(row)
            for row in load_jsonl(dataset_root / "legacy_diagnostic" / "audit_prefixes.jsonl")
        ]
        legacy_gold = {
            row.prefix_id: row for row in (
                FailureChainGold.from_dict(item)
                for item in load_jsonl(dataset_root / "legacy_diagnostic" / "audit_gold.jsonl")
            )
        }
        if len(legacy_prefixes) != 24:
            errors.append("legacy diagnostic must preserve 24 source prefixes")
        for prefix in legacy_prefixes:
            errors.extend(_tool_pair_errors(prefix))
            positions = {str(event["event_id"]): int(event["step_id"]) for event in prefix.events}
            chain = legacy_gold[prefix.prefix_id]
            chronological = [positions[item] for item in chain.ordered_event_ids]
            if chronological != sorted(chronological):
                errors.append(f"legacy gold is not chronological: {prefix.prefix_id}")
    except (ValueError, KeyError, FileNotFoundError) as error:
        errors.append(f"legacy diagnostic validation failed: {error}")
    snapshot_path = dataset_root / "config.snapshot.json"
    snapshot = (
        json.loads(snapshot_path.read_text(encoding="utf-8"))
        if snapshot_path.is_file()
        else {}
    )
    real_report = validate_real_annotations(
        dataset_root / "annotations" / "real",
        expected_revisions=_expected_real_revisions(snapshot) if snapshot else None,
    )
    if real_report["ready"]:
        try:
            real_prefixes = [
                PrefixRecord.from_dict(row)
                for row in load_jsonl(public / "real_prefixes.jsonl")
            ]
            real_queries = [
                QueryRecord.from_dict(row)
                for row in load_jsonl(public / "real_queries.jsonl")
            ]
            real_gold = [
                FailureChainGold.from_dict(row)
                for row in load_jsonl(private / "real_all_gold.jsonl")
            ]
            if len(real_prefixes) != 100 or len(real_gold) != 100:
                errors.append("formal real import must contain exactly 100 prefixes and gold rows")
            if len(real_queries) != 520:
                errors.append("formal real import must contain exactly 520 queries")
            real_ids = {item.prefix_id for item in real_prefixes}
            if len(real_ids) != len(real_prefixes):
                errors.append("formal real import contains duplicate prefix IDs")
            if any(item.prefix_id not in real_ids for item in real_queries):
                errors.append("formal real query references an unknown prefix")
            if any(item.prefix_id not in real_ids for item in real_gold):
                errors.append("formal real gold references an unknown prefix")
        except (ValueError, FileNotFoundError, json.JSONDecodeError) as error:
            errors.append(f"formal real import is invalid: {error}")
    if not real_report["ready"]:
        warnings.append("formal v1 is blocked until 100 real chains pass double-human annotation")

    diagnostic_ready = not errors
    return {
        "schema_version": VALIDATION_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "diagnostic_ready": diagnostic_ready,
        "v1_ready": diagnostic_ready and bool(real_report["ready"]),
        "controlled": {
            "prefix_count": len(prefixes),
            "query_count": len(queries),
            "gold_count": len(gold),
            "split_counts": split_counts,
            "failure_family_counts": {
                family: sum(item.failure_family == family for item in prefixes)
                for family in sorted({item.failure_family for item in prefixes})
            },
            "recoverability_counts": {
                level: sum(item.recoverability == level for item in prefixes)
                for level in RECOVERABILITY_LEVELS
            },
        },
        "real": real_report,
        "errors": errors,
        "warnings": warnings,
    }


# Imported after definitions so mutually-referential helpers initialize safely.
from .build import (
    _expected_real_revisions as _expected_real_revisions,
    artifact_manifest as artifact_manifest,
)

from .io import (
    load_jsonl as load_jsonl,
)

from .models import (
    FailureChainGold as FailureChainGold,
    PrefixRecord as PrefixRecord,
    QueryRecord as QueryRecord,
)

from .real_validation import (
    validate_real_annotations as validate_real_annotations,
)

from .validation_helpers import (
    _query_leaks_gold as _query_leaks_gold,
    _tool_pair_errors as _tool_pair_errors,
)
