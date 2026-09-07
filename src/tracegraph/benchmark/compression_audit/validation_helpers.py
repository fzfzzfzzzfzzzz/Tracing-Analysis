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





def _tool_pair_errors(prefix: PrefixRecord) -> list[str]:
    calls: dict[str, Mapping[str, Any]] = {}
    results: dict[str, list[Mapping[str, Any]]] = {}
    for event in prefix.events:
        call_id = event.get("call_id")
        if not call_id:
            continue
        if event.get("kind") == "tool_call":
            if str(call_id) in calls:
                return [f"duplicate tool call ID in {prefix.prefix_id}: {call_id}"]
            calls[str(call_id)] = event
        elif event.get("kind") in {"observation", "error", "tool_result"}:
            results.setdefault(str(call_id), []).append(event)
    errors = []
    for call_id in calls:
        if len(results.get(call_id, ())) != 1:
            errors.append(f"tool call {call_id} does not have exactly one result")
    for call_id in results:
        if call_id not in calls:
            errors.append(f"tool result {call_id} has no call")
    return errors


def _query_leaks_gold(query: QueryRecord, gold: FailureChainGold) -> list[str]:
    text = " ".join(query.text.lower().split())
    leaks = []
    protected = (
        ("error_signature", gold.error_signature),
        ("failed_action", gold.failed_action),
        ("replacement_action", gold.replacement_action),
    )
    for field_name, value in protected:
        normalized = " ".join(value.lower().split())
        if normalized and len(normalized) >= 5 and normalized in text:
            leaks.append(f"{query.query_id} leaks {field_name}")
    return leaks


def _cohen_kappa(left: Sequence[str], right: Sequence[str]) -> float | None:
    if len(left) != len(right) or not left:
        return None
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / len(left)
    labels = sorted(set(left).union(right))
    expected = sum(
        (left.count(label) / len(left)) * (right.count(label) / len(right))
        for label in labels
    )
    if math.isclose(expected, 1.0):
        return None  # Chance-corrected agreement is undefined for a constant category.
    return (observed - expected) / (1.0 - expected)


def _event_f1(left: Sequence[str], right: Sequence[str]) -> float:
    a, b = set(left), set(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    precision = len(a.intersection(b)) / len(a)
    recall = len(a.intersection(b)) / len(b)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


# Imported after definitions so mutually-referential helpers initialize safely.
from .models import (
    FailureChainGold as FailureChainGold,
    PrefixRecord as PrefixRecord,
    QueryRecord as QueryRecord,
)
