"""Deterministic Phase 6 reactivation triggers and flat archive retrieval."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from .decision_state import stable_digest
from .goal_lifecycle import (
    GoalLifecycleState,
    GoalLifecycleView,
)
from .graph import TraceGraph


class ReactivationTriggerType(str, Enum):
    HISTORICAL_QUERY = "historical_query"
    GOAL_RESUME = "goal_resume"
    FAILURE_RECURRENCE = "failure_recurrence"
    EXPLICIT_REFERENCE = "explicit_reference"


@dataclass(frozen=True, slots=True)
class ReactivationTrigger:
    trigger_types: tuple[ReactivationTriggerType, ...]
    query_text: str
    terms: tuple[str, ...]
    referenced_event_ids: tuple[str, ...] = ()
    referenced_entities: tuple[str, ...] = ()
    referenced_archive_handles: tuple[str, ...] = ()
    resume_goal_id: str | None = None
    error_signature: str | None = None
    schema_version: str = "phase6_reactivation_trigger_v1"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "trigger_types",
            tuple(sorted(set(self.trigger_types), key=lambda item: item.value)),
        )
        for field in (
            "terms",
            "referenced_event_ids",
            "referenced_entities",
            "referenced_archive_handles",
        ):
            object.__setattr__(self, field, tuple(sorted(set(getattr(self, field)))))

    @property
    def active(self) -> bool:
        return bool(self.trigger_types)

    @property
    def trigger_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "trigger_types": [item.value for item in self.trigger_types],
            "query_text": self.query_text,
            "terms": list(self.terms),
            "referenced_event_ids": list(self.referenced_event_ids),
            "referenced_entities": list(self.referenced_entities),
            "referenced_archive_handles": list(self.referenced_archive_handles),
            "resume_goal_id": self.resume_goal_id,
            "error_signature": self.error_signature,
        }
        if include_hash:
            value["trigger_hash"] = self.trigger_hash
        return value

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReactivationTrigger":
        value = cls(
            trigger_types=tuple(
                ReactivationTriggerType(str(item))
                for item in data.get("trigger_types", ())
            ),
            query_text=str(data.get("query_text") or ""),
            terms=tuple(map(str, data.get("terms", ()))),
            referenced_event_ids=tuple(map(str, data.get("referenced_event_ids", ()))),
            referenced_entities=tuple(map(str, data.get("referenced_entities", ()))),
            referenced_archive_handles=tuple(
                map(str, data.get("referenced_archive_handles", ()))
            ),
            resume_goal_id=(
                str(data["resume_goal_id"]) if data.get("resume_goal_id") else None
            ),
            error_signature=(
                str(data["error_signature"]) if data.get("error_signature") else None
            ),
            schema_version=str(
                data.get("schema_version", "phase6_reactivation_trigger_v1")
            ),
        )
        declared = data.get("trigger_hash")
        if declared is not None and declared != value.trigger_hash:
            raise ValueError("ReactivationTrigger hash mismatch")
        return value


@dataclass(frozen=True, slots=True)
class AnchorCandidate:
    event_id: str
    score: int
    matched_terms: tuple[str, ...]
    reasons: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.score < 0:
            raise ValueError("anchor score must be non-negative")
        object.__setattr__(self, "matched_terms", tuple(sorted(set(self.matched_terms))))
        object.__setattr__(self, "reasons", tuple(sorted(set(self.reasons))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "score": self.score,
            "matched_terms": list(self.matched_terms),
            "reasons": list(self.reasons),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "AnchorCandidate":
        return cls(
            event_id=str(data["event_id"]),
            score=int(data["score"]),
            matched_terms=tuple(map(str, data.get("matched_terms", ()))),
            reasons=tuple(map(str, data.get("reasons", ()))),
        )


@dataclass(frozen=True, slots=True)
class ReactivationResult:
    retrieval_mode: str
    trigger: ReactivationTrigger
    candidates: tuple[AnchorCandidate, ...]
    selected_anchor_ids: tuple[str, ...]
    injected_event_ids: tuple[str, ...]
    protocol_span_ids: tuple[str, ...]
    injected_tokens: int
    current_fact_ids: tuple[str, ...]
    historical_fact_ids: tuple[str, ...]
    omitted_event_ids: tuple[str, ...] = ()
    abstention_reason: str | None = None
    closure_records: tuple[dict[str, Any], ...] = ()
    schema_version: str = "phase6_reactivation_result_v1"

    def __post_init__(self) -> None:
        if self.injected_tokens < 0:
            raise ValueError("injected_tokens must be non-negative")
        object.__setattr__(
            self,
            "candidates",
            tuple(sorted(self.candidates, key=lambda item: (-item.score, item.event_id))),
        )
        for field in (
            "selected_anchor_ids",
            "injected_event_ids",
            "protocol_span_ids",
            "current_fact_ids",
            "historical_fact_ids",
            "omitted_event_ids",
        ):
            object.__setattr__(self, field, tuple(sorted(set(getattr(self, field)))))

    @property
    def result_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        value = {
            "schema_version": self.schema_version,
            "retrieval_mode": self.retrieval_mode,
            "trigger": self.trigger.to_dict(),
            "candidates": [item.to_dict() for item in self.candidates],
            "selected_anchor_ids": list(self.selected_anchor_ids),
            "injected_event_ids": list(self.injected_event_ids),
            "protocol_span_ids": list(self.protocol_span_ids),
            "injected_tokens": self.injected_tokens,
            "current_fact_ids": list(self.current_fact_ids),
            "historical_fact_ids": list(self.historical_fact_ids),
            "omitted_event_ids": list(self.omitted_event_ids),
            "abstention_reason": self.abstention_reason,
            "closure_records": [dict(item) for item in self.closure_records],
        }
        if include_hash:
            value["result_hash"] = self.result_hash
        return value

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ReactivationResult":
        value = cls(
            retrieval_mode=str(data["retrieval_mode"]),
            trigger=ReactivationTrigger.from_dict(data["trigger"]),
            candidates=tuple(
                AnchorCandidate.from_dict(item) for item in data.get("candidates", ())
            ),
            selected_anchor_ids=tuple(map(str, data.get("selected_anchor_ids", ()))),
            injected_event_ids=tuple(map(str, data.get("injected_event_ids", ()))),
            protocol_span_ids=tuple(map(str, data.get("protocol_span_ids", ()))),
            injected_tokens=int(data.get("injected_tokens", 0)),
            current_fact_ids=tuple(map(str, data.get("current_fact_ids", ()))),
            historical_fact_ids=tuple(map(str, data.get("historical_fact_ids", ()))),
            omitted_event_ids=tuple(map(str, data.get("omitted_event_ids", ()))),
            abstention_reason=(
                str(data["abstention_reason"])
                if data.get("abstention_reason")
                else None
            ),
            closure_records=tuple(dict(item) for item in data.get("closure_records", ())),
            schema_version=str(
                data.get("schema_version", "phase6_reactivation_result_v1")
            ),
        )
        declared = data.get("result_hash")
        if declared is not None and declared != value.result_hash:
            raise ValueError("ReactivationResult hash mismatch")
        return value


_TERM_RE = re.compile(r"[a-z0-9_./:\-]+", re.IGNORECASE)
_HISTORY_TERMS = {
    "before",
    "earlier",
    "historical",
    "history",
    "previous",
    "previously",
    "why",
    "audit",
    "receipt",
    "restore",
    "resume",
}


def tokenize_terms(value: Any) -> tuple[str, ...]:
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return tuple(sorted(set(item.lower() for item in _TERM_RE.findall(value))))


def detect_reactivation_trigger(request: Mapping[str, Any]) -> ReactivationTrigger:
    """Infer an explicit trigger from request-visible fields only."""

    text = str(request.get("text") or "")
    terms = set(tokenize_terms(text))
    trigger_types: set[ReactivationTriggerType] = set()
    resume_goal_id = str(request["resume_goal_id"]) if request.get("resume_goal_id") else None
    error_signature = (
        str(request["error_signature"]) if request.get("error_signature") else None
    )
    event_ids = tuple(map(str, request.get("referenced_event_ids", ())))
    entities = tuple(map(str, request.get("referenced_entities", ())))
    handles = tuple(map(str, request.get("archive_handles", ())))
    if terms.intersection(_HISTORY_TERMS) and bool(request.get("historical_intent")):
        trigger_types.add(ReactivationTriggerType.HISTORICAL_QUERY)
    if resume_goal_id:
        trigger_types.add(ReactivationTriggerType.GOAL_RESUME)
    if error_signature:
        trigger_types.add(ReactivationTriggerType.FAILURE_RECURRENCE)
    if event_ids or handles or bool(request.get("historical_reference")):
        trigger_types.add(ReactivationTriggerType.EXPLICIT_REFERENCE)
    # An entity by itself is not a history trigger; this is required for the
    # DISTRACTOR forks, which intentionally reuse old words and entities.
    return ReactivationTrigger(
        trigger_types=tuple(trigger_types),
        query_text=text,
        terms=tuple(terms),
        referenced_event_ids=event_ids,
        referenced_entities=entities,
        referenced_archive_handles=handles,
        resume_goal_id=resume_goal_id,
        error_signature=error_signature,
    )


def retrieve_anchor_candidates(
    event_graph: TraceGraph,
    lifecycle: GoalLifecycleView,
    trigger: ReactivationTrigger,
) -> tuple[AnchorCandidate, ...]:
    """Rank dormant/superseded events with deterministic lexical metadata."""

    if not trigger.active:
        return ()
    records = lifecycle.record_map()
    query_terms = set(trigger.terms).union(item.lower() for item in trigger.referenced_entities)
    result: list[AnchorCandidate] = []
    for event_id, record in records.items():
        if record.state not in {
            GoalLifecycleState.DORMANT,
            GoalLifecycleState.SUPERSEDED,
        }:
            continue
        node = event_graph.nodes[event_id]
        indexed = set(tokenize_terms(node.content))
        for key in (
            "retrieval_terms",
            "entities",
            "files",
            "operation",
            "error_signature",
        ):
            value = node.metadata.get(key, ())
            if isinstance(value, str):
                indexed.update(tokenize_terms(value))
            else:
                indexed.update(tokenize_terms(value))
        matched = query_terms.intersection(indexed)
        score = len(matched)
        reasons: list[str] = []
        if matched:
            reasons.append("lexical_or_entity_overlap")
        if event_id in trigger.referenced_event_ids:
            score += 100
            reasons.append("explicit_event_reference")
        if record.raw_ref and record.raw_ref in trigger.referenced_archive_handles:
            score += 100
            reasons.append("archive_handle_match")
        if trigger.resume_goal_id and trigger.resume_goal_id in record.goal_ids:
            score += 20
            reasons.append("goal_membership_match")
        node_error = str(node.metadata.get("error_signature") or "")
        if trigger.error_signature and trigger.error_signature == node_error:
            score += 30
            reasons.append("error_signature_match")
        if score:
            result.append(
                AnchorCandidate(
                    event_id=event_id,
                    score=score,
                    matched_terms=tuple(matched),
                    reasons=tuple(reasons),
                )
            )
    return tuple(sorted(result, key=lambda item: (-item.score, item.event_id)))


def _fact_labels(
    lifecycle: GoalLifecycleView,
    event_ids: Sequence[str],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    records = lifecycle.record_map()
    current = tuple(
        event_id
        for event_id in event_ids
        if records[event_id].state in {GoalLifecycleState.ACTIVE, GoalLifecycleState.PINNED}
    )
    historical = tuple(event_id for event_id in event_ids if event_id not in current)
    return current, historical


def flat_reactivation(
    event_graph: TraceGraph,
    lifecycle: GoalLifecycleView,
    trigger: ReactivationTrigger,
    candidates: Sequence[AnchorCandidate],
    *,
    token_budget: int = 1024,
) -> ReactivationResult:
    """Inject ranked anchor spans without causal edge expansion."""

    if token_budget <= 0:
        raise ValueError("token_budget must be positive")
    if not trigger.active:
        return ReactivationResult(
            retrieval_mode="flat",
            trigger=trigger,
            candidates=tuple(candidates),
            selected_anchor_ids=(),
            injected_event_ids=(),
            protocol_span_ids=(),
            injected_tokens=0,
            current_fact_ids=(),
            historical_fact_ids=(),
            abstention_reason="no_reactivation_trigger",
        )
    if not candidates:
        return ReactivationResult(
            retrieval_mode="flat",
            trigger=trigger,
            candidates=(),
            selected_anchor_ids=(),
            injected_event_ids=(),
            protocol_span_ids=(),
            injected_tokens=0,
            current_fact_ids=(),
            historical_fact_ids=(),
            abstention_reason="no_matching_anchor",
        )
    span_map = lifecycle.span_map()
    record_map = lifecycle.record_map()
    selected_spans: list[str] = []
    selected_anchors: list[str] = []
    injected: set[str] = set()
    spent = 0
    for candidate in candidates:
        span_id = record_map[candidate.event_id].protocol_span_id
        if span_id in selected_spans:
            selected_anchors.append(candidate.event_id)
            continue
        span = span_map[span_id]
        if spent + span.reactivation_token_count > token_budget:
            continue
        selected_spans.append(span_id)
        selected_anchors.append(candidate.event_id)
        injected.update(span.event_ids)
        spent += span.reactivation_token_count
        # Flat retrieval returns only the top-ranked matching span.  M4 and M5
        # therefore differ only by graph closure, not by their anchor ranker.
        break
    current, historical = _fact_labels(lifecycle, tuple(injected))
    return ReactivationResult(
        retrieval_mode="flat",
        trigger=trigger,
        candidates=tuple(candidates),
        selected_anchor_ids=tuple(selected_anchors),
        injected_event_ids=tuple(injected),
        protocol_span_ids=tuple(selected_spans),
        injected_tokens=spent,
        current_fact_ids=current,
        historical_fact_ids=historical,
        omitted_event_ids=tuple(
            item.event_id for item in candidates if item.event_id not in injected
        ),
        abstention_reason=None if injected else "matching_spans_exceed_budget",
    )
