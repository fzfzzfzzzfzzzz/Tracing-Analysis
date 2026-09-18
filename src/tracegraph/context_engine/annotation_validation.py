"""Definitions moved from ``tracegraph.lifecycle_annotation``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from ..capture import estimate_tokens
from ..graph import TraceGraph
from ..liveness import EventSpan
from ..phase5_offline import policy_text
from ..schema import EdgeType, Node, NodeType
from ..trajectory_artifacts import sha256_json

from .annotation_constants import (
    CURRENT_TARGET_NEEDS as CURRENT_TARGET_NEEDS,
    OBLIGATIONS as OBLIGATIONS,
    TERMINAL_REASONS as TERMINAL_REASONS,
    TERMINAL_SAFE_REASONS as TERMINAL_SAFE_REASONS,
)



def derive_relation_first_disposition(
    *,
    terminal_reason: str,
    current_target_need: str,
    obligations: Sequence[str],
    reactivation_risk: bool,
) -> str:
    """Fail-closed deterministic mapping from lifecycle evidence to disposition."""

    protected = set(obligations) & set(OBLIGATIONS)
    if protected or terminal_reason == "audit_required":
        return "live_critical"
    if reactivation_risk or terminal_reason == "unknown":
        return "uncertain"
    if terminal_reason in TERMINAL_SAFE_REASONS:
        if current_target_need == "not_needed":
            return "safe_to_evict"
        if current_target_need == "required":
            return "live_critical"
        if current_target_need == "useful":
            return "live_noncritical"
        return "uncertain"
    if terminal_reason == "active":
        if current_target_need == "required":
            return "live_critical"
        if current_target_need == "useful":
            return "live_noncritical"
    return "uncertain"


def validate_relation_first_labels(
    value: Any,
    *,
    expected_span_ids: set[str],
    allowed_event_ids: set[str],
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Mapping) or set(value) != {"labels"}:
        raise ValueError("machine response must contain only labels")
    raw_labels = value["labels"]
    if not isinstance(raw_labels, list):
        raise ValueError("machine response labels must be an array")
    required = {
        "span_id",
        "terminal_reason",
        "current_target_need",
        "relation_target_ids",
        "obligations",
        "evidence_event_ids",
        "reactivation_risk",
    }
    parsed: list[dict[str, Any]] = []
    for raw in raw_labels:
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise ValueError("machine relation label has missing or additional fields")
        span_id = str(raw["span_id"])
        reason = str(raw["terminal_reason"])
        need = str(raw["current_target_need"])
        targets = tuple(sorted(set(map(str, raw["relation_target_ids"]))))
        obligations = tuple(sorted(set(map(str, raw["obligations"]))))
        evidence = tuple(sorted(set(map(str, raw["evidence_event_ids"]))))
        if span_id not in expected_span_ids:
            raise ValueError(f"unknown machine-label span ID: {span_id}")
        if reason not in TERMINAL_REASONS:
            raise ValueError(
                f"invalid terminal_reason enum for {span_id}: {reason!r}; "
                f"allowed={list(TERMINAL_REASONS)!r}"
            )
        if need not in CURRENT_TARGET_NEEDS:
            raise ValueError(
                f"invalid current_target_need enum for {span_id}: {need!r}; "
                f"allowed={list(CURRENT_TARGET_NEEDS)!r}"
            )
        if not set(targets).issubset(expected_span_ids):
            raise ValueError("machine relation label contains an unknown relation target")
        if not set(obligations).issubset(OBLIGATIONS):
            raise ValueError("machine relation label contains an unknown obligation")
        if not set(evidence).issubset(allowed_event_ids):
            raise ValueError("machine relation label contains an unknown evidence event")
        if not isinstance(raw["reactivation_risk"], bool):
            raise ValueError("reactivation_risk must be boolean")
        parsed.append(
            {
                "span_id": span_id,
                "disposition": derive_relation_first_disposition(
                    terminal_reason=reason,
                    current_target_need=need,
                    obligations=obligations,
                    reactivation_risk=raw["reactivation_risk"],
                ),
                "terminal_reason": reason,
                "current_target_need": need,
                "relation_target_ids": list(targets),
                "obligations": list(obligations),
                "evidence_event_ids": list(evidence),
                "reactivation_risk": raw["reactivation_risk"],
                "disposition_provenance": "deterministic_relation_first_v1",
            }
        )
    observed = [item["span_id"] for item in parsed]
    if len(observed) != len(set(observed)) or set(observed) != expected_span_ids:
        raise ValueError("machine response has missing or duplicate span labels")
    return tuple(sorted(parsed, key=lambda item: item["span_id"]))


def derive_relation_boolean_disposition(
    *,
    terminal_reason: str,
    required_for_current_target: bool,
    requirement_uncertain: bool,
    obligations: Sequence[str],
    reactivation_risk: bool,
) -> str:
    """Fail closed while deriving liveness from relations and boolean evidence."""

    if set(obligations) & set(OBLIGATIONS) or terminal_reason == "audit_required":
        return "live_critical"
    if required_for_current_target:
        return "live_critical"
    if requirement_uncertain or reactivation_risk or terminal_reason == "unknown":
        return "uncertain"
    if terminal_reason in TERMINAL_SAFE_REASONS:
        return "safe_to_evict"
    if terminal_reason == "active":
        return "live_noncritical"
    return "uncertain"


def validate_relation_boolean_labels(
    value: Any,
    *,
    expected_span_ids: set[str],
    allowed_event_ids: set[str],
) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, Mapping) or set(value) != {"labels"}:
        raise ValueError("machine response must contain only labels")
    raw_labels = value["labels"]
    if not isinstance(raw_labels, list):
        raise ValueError("machine response labels must be an array")
    required = {
        "span_id",
        "terminal_reason",
        "required_for_current_target",
        "requirement_uncertain",
        "relation_target_ids",
        "obligations",
        "evidence_event_ids",
        "reactivation_risk",
    }
    parsed: list[dict[str, Any]] = []
    for raw in raw_labels:
        if not isinstance(raw, Mapping) or set(raw) != required:
            raise ValueError("machine boolean relation label has missing or additional fields")
        span_id = str(raw["span_id"])
        reason = str(raw["terminal_reason"])
        targets = tuple(sorted(set(map(str, raw["relation_target_ids"]))))
        obligations = tuple(sorted(set(map(str, raw["obligations"]))))
        evidence = tuple(sorted(set(map(str, raw["evidence_event_ids"]))))
        needed = raw["required_for_current_target"]
        need_uncertain = raw["requirement_uncertain"]
        risk = raw["reactivation_risk"]
        if span_id not in expected_span_ids:
            raise ValueError(f"unknown machine-label span ID: {span_id}")
        if reason not in TERMINAL_REASONS:
            raise ValueError(
                f"invalid terminal_reason enum for {span_id}: {reason!r}; "
                f"allowed={list(TERMINAL_REASONS)!r}"
            )
        for name, item in (
            ("required_for_current_target", needed),
            ("requirement_uncertain", need_uncertain),
            ("reactivation_risk", risk),
        ):
            if not isinstance(item, bool):
                raise ValueError(f"{name} must be boolean for {span_id}")
        if not set(targets).issubset(expected_span_ids):
            raise ValueError("machine boolean label contains an unknown relation target")
        if not set(obligations).issubset(OBLIGATIONS):
            raise ValueError("machine boolean label contains an unknown obligation")
        if not set(evidence).issubset(allowed_event_ids):
            raise ValueError("machine boolean label contains an unknown evidence event")
        parsed.append(
            {
                "span_id": span_id,
                "disposition": derive_relation_boolean_disposition(
                    terminal_reason=reason,
                    required_for_current_target=needed,
                    requirement_uncertain=need_uncertain,
                    obligations=obligations,
                    reactivation_risk=risk,
                ),
                "terminal_reason": reason,
                "required_for_current_target": needed,
                "requirement_uncertain": need_uncertain,
                "relation_target_ids": list(targets),
                "obligations": list(obligations),
                "evidence_event_ids": list(evidence),
                "reactivation_risk": risk,
                "disposition_provenance": (
                    "deterministic_relation_first_boolean_v2"
                ),
            }
        )
    observed = [item["span_id"] for item in parsed]
    if len(observed) != len(set(observed)) or set(observed) != expected_span_ids:
        raise ValueError("machine response has missing or duplicate span labels")
    return tuple(sorted(parsed, key=lambda item: item["span_id"]))


def remap_labels_to_original(
    labels: Sequence[Mapping[str, Any]], mapping: Mapping[str, Any]
) -> tuple[dict[str, Any], ...]:
    spans = {item["opaque_span_id"]: item["span_id"] for item in mapping["spans"]}
    events = {item["opaque_event_id"]: item["event_id"] for item in mapping["events"]}
    return tuple(
        sorted(
            (
                {
                    **dict(label),
                    "span_id": spans[str(label["span_id"])],
                    "relation_target_ids": sorted(
                        spans[str(item)] for item in label["relation_target_ids"]
                    ),
                    "evidence_event_ids": sorted(
                        events[str(item)] for item in label["evidence_event_ids"]
                    ),
                }
                for label in labels
            ),
            key=lambda item: item["span_id"],
        )
    )


def consensus_labels(
    pass_a: Sequence[Mapping[str, Any]], pass_b: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], ...]:
    a_map = {str(item["span_id"]): item for item in pass_a}
    b_map = {str(item["span_id"]): item for item in pass_b}
    if set(a_map) != set(b_map):
        raise ValueError("annotation passes do not cover the same spans")
    rows: list[dict[str, Any]] = []
    for span_id in sorted(a_map):
        a = a_map[span_id]
        b = b_map[span_id]
        fields = ["disposition", "terminal_reason", "relation_target_ids", "obligations"]
        relation_first = "current_target_need" in a or "current_target_need" in b
        relation_boolean = (
            "required_for_current_target" in a or "required_for_current_target" in b
        )
        if relation_first:
            fields.append("current_target_need")
        if relation_boolean:
            fields.extend(
                ["required_for_current_target", "requirement_uncertain"]
            )
        agreed = all(
            field in a and field in b and a[field] == b[field] for field in fields
        )
        if agreed:
            result = {
                "disposition": a["disposition"],
                "terminal_reason": a["terminal_reason"],
                "relation_target_ids": list(a["relation_target_ids"]),
                "obligations": list(a["obligations"]),
                "evidence_event_ids": sorted(
                    set(a["evidence_event_ids"]) | set(b["evidence_event_ids"])
                ),
                "reactivation_risk": bool(
                    a["reactivation_risk"] or b["reactivation_risk"]
                ),
            }
            if relation_first:
                result.update(
                    {
                        "current_target_need": a["current_target_need"],
                        "disposition_provenance": (
                            "deterministic_relation_first_v1"
                        ),
                    }
                )
            if relation_boolean:
                result.update(
                    {
                        "required_for_current_target": a[
                            "required_for_current_target"
                        ],
                        "requirement_uncertain": a["requirement_uncertain"],
                        "disposition_provenance": (
                            "deterministic_relation_first_boolean_v2"
                        ),
                    }
                )
        else:
            result = {
                "disposition": "uncertain",
                "terminal_reason": "unknown",
                "relation_target_ids": [],
                "obligations": sorted(
                    set(a["obligations"]) | set(b["obligations"])
                ),
                "evidence_event_ids": sorted(
                    set(a["evidence_event_ids"]) | set(b["evidence_event_ids"])
                ),
                "reactivation_risk": True,
            }
            if relation_first:
                result.update(
                    {
                        "current_target_need": "uncertain",
                        "disposition_provenance": (
                            "deterministic_relation_first_v1"
                        ),
                    }
                )
            if relation_boolean:
                result.update(
                    {
                        "required_for_current_target": False,
                        "requirement_uncertain": True,
                        "disposition_provenance": (
                            "deterministic_relation_first_boolean_v2"
                        ),
                    }
                )
        rows.append({"span_id": span_id, "machine_consensus": agreed, **result})
    return tuple(rows)


def cohen_kappa_binary(a_values: Sequence[bool], b_values: Sequence[bool]) -> float:
    if len(a_values) != len(b_values) or not a_values:
        raise ValueError("binary kappa requires two non-empty equal-length samples")
    observed = sum(a == b for a, b in zip(a_values, b_values, strict=True)) / len(a_values)
    a_counts = Counter(a_values)
    b_counts = Counter(b_values)
    expected = sum(
        (a_counts[value] / len(a_values)) * (b_counts[value] / len(b_values))
        for value in (False, True)
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)
