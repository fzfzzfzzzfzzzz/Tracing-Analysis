"""Definitions moved from ``tracegraph.liveness``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from ..decision_query import DecisionQuery
from ..decision_state import DecisionStateGraph, StateAtom, StateAtomType, StateEdgeType, stable_digest
from ..graph import TraceGraph
from ..policy_rules import PolicyRule
from ..schema import EdgeType, Node, NodeType, SemanticOutcome
from ..state_reducer import reduce_event_graph





@dataclass(frozen=True, slots=True)
class EventLifecycleRecord:
    event_id: str
    status: str
    terminal: bool
    reason: str
    confidence: float
    source_atom_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("lifecycle confidence must be between zero and one")
        object.__setattr__(self, "source_atom_ids", tuple(sorted(set(self.source_atom_ids))))

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "status": self.status,
            "terminal": self.terminal,
            "reason": self.reason,
            "confidence": self.confidence,
            "source_atom_ids": list(self.source_atom_ids),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventLifecycleRecord":
        return cls(
            event_id=str(data["event_id"]),
            status=str(data["status"]),
            terminal=bool(data["terminal"]),
            reason=str(data["reason"]),
            confidence=float(data["confidence"]),
            source_atom_ids=tuple(map(str, data.get("source_atom_ids", ()))),
        )


@dataclass(frozen=True, slots=True)
class DecisionLifecycleGraph:
    """Prefix-only decision state plus deterministic event lifecycle records."""

    decision_state: DecisionStateGraph
    event_graph_hash: str
    event_records: tuple[EventLifecycleRecord, ...]
    lifecycle_version: str = "decision_lifecycle_graph_v1"

    def __post_init__(self) -> None:
        records = tuple(sorted(self.event_records, key=lambda item: item.event_id))
        ids = [item.event_id for item in records]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate event lifecycle record")
        object.__setattr__(self, "event_records", records)

    @property
    def cutoff_step(self) -> int:
        return self.decision_state.cutoff_step

    @property
    def lifecycle_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def record_map(self) -> dict[str, EventLifecycleRecord]:
        return {item.event_id: item for item in self.event_records}

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.lifecycle_version,
            "event_graph_hash": self.event_graph_hash,
            "decision_state": self.decision_state.to_dict(),
            "event_records": [item.to_dict() for item in self.event_records],
        }
        if include_hash:
            result["lifecycle_hash"] = self.lifecycle_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionLifecycleGraph":
        value = cls(
            decision_state=DecisionStateGraph.from_dict(
                dict(data["decision_state"])
            ),
            event_graph_hash=str(data["event_graph_hash"]),
            event_records=tuple(
                EventLifecycleRecord.from_dict(item)
                for item in data.get("event_records", ())
            ),
            lifecycle_version=str(
                data.get("schema_version", "decision_lifecycle_graph_v1")
            ),
        )
        declared = data.get("lifecycle_hash")
        if declared is not None and declared != value.lifecycle_hash:
            raise ValueError("DecisionLifecycleGraph hash mismatch")
        return value


@dataclass(frozen=True, slots=True)
class LivenessRoot:
    root_id: str
    atom_id: str | None
    source_event_ids: tuple[str, ...]
    reason: str
    hard: bool

    @classmethod
    def create(
        cls,
        *,
        atom_id: str | None,
        source_event_ids: Sequence[str],
        reason: str,
        hard: bool,
    ) -> "LivenessRoot":
        sources = tuple(sorted(set(map(str, source_event_ids))))
        identity = {
            "atom_id": atom_id,
            "source_event_ids": sources,
            "reason": reason,
            "hard": hard,
        }
        return cls(
            root_id=f"root_{stable_digest(identity)[:24]}",
            atom_id=atom_id,
            source_event_ids=sources,
            reason=reason,
            hard=hard,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_id": self.root_id,
            "atom_id": self.atom_id,
            "source_event_ids": list(self.source_event_ids),
            "reason": self.reason,
            "hard": self.hard,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LivenessRoot":
        value = cls.create(
            atom_id=str(data["atom_id"]) if data.get("atom_id") else None,
            source_event_ids=tuple(
                map(str, data.get("source_event_ids", ()))
            ),
            reason=str(data["reason"]),
            hard=bool(data["hard"]),
        )
        declared = data.get("root_id")
        if declared is not None and declared != value.root_id:
            raise ValueError("LivenessRoot id mismatch")
        return value


@dataclass(frozen=True, slots=True)
class LivenessRoots:
    lifecycle_hash: str
    query_hash: str
    roots: tuple[LivenessRoot, ...]
    uncertainty_reasons: tuple[str, ...] = ()
    roots_version: str = "liveness_roots_v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "roots", tuple(sorted(self.roots, key=lambda item: item.root_id)))
        object.__setattr__(
            self,
            "uncertainty_reasons",
            tuple(sorted(set(map(str, self.uncertainty_reasons)))),
        )

    @property
    def root_atom_ids(self) -> tuple[str, ...]:
        return tuple(sorted({item.atom_id for item in self.roots if item.atom_id}))

    @property
    def root_event_ids(self) -> tuple[str, ...]:
        return tuple(
            sorted(
                {
                    event_id
                    for item in self.roots
                    for event_id in item.source_event_ids
                }
            )
        )

    @property
    def roots_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.roots_version,
            "lifecycle_hash": self.lifecycle_hash,
            "query_hash": self.query_hash,
            "roots": [item.to_dict() for item in self.roots],
            "root_atom_ids": list(self.root_atom_ids),
            "root_event_ids": list(self.root_event_ids),
            "uncertainty_reasons": list(self.uncertainty_reasons),
        }
        if include_hash:
            result["roots_hash"] = self.roots_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LivenessRoots":
        value = cls(
            lifecycle_hash=str(data["lifecycle_hash"]),
            query_hash=str(data["query_hash"]),
            roots=tuple(
                LivenessRoot.from_dict(item)
                for item in data.get("roots", ())
            ),
            uncertainty_reasons=tuple(
                map(str, data.get("uncertainty_reasons", ()))
            ),
            roots_version=str(data.get("schema_version", "liveness_roots_v1")),
        )
        declared = data.get("roots_hash")
        if declared is not None and declared != value.roots_hash:
            raise ValueError("LivenessRoots hash mismatch")
        return value


@dataclass(frozen=True, slots=True)
class EventSpan:
    span_id: str
    span_type: str
    node_ids: tuple[str, ...]
    message_ordinals: tuple[int, ...]
    call_ids: tuple[str, ...]
    raw_refs: tuple[str, ...]

    @classmethod
    def create(
        cls,
        *,
        span_type: str,
        node_ids: Sequence[str],
        message_ordinals: Sequence[int],
        call_ids: Sequence[str] = (),
        raw_refs: Sequence[str] = (),
    ) -> "EventSpan":
        nodes = tuple(sorted(set(map(str, node_ids))))
        ordinals = tuple(sorted(set(map(int, message_ordinals))))
        calls = tuple(sorted(set(map(str, call_ids))))
        refs = tuple(sorted(set(map(str, raw_refs))))
        identity = {
            "span_type": span_type,
            "node_ids": nodes,
            "message_ordinals": ordinals,
            "call_ids": calls,
            "raw_refs": refs,
        }
        return cls(
            span_id=f"span_{stable_digest(identity)[:24]}",
            span_type=span_type,
            node_ids=nodes,
            message_ordinals=ordinals,
            call_ids=calls,
            raw_refs=refs,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "span_type": self.span_type,
            "node_ids": list(self.node_ids),
            "message_ordinals": list(self.message_ordinals),
            "call_ids": list(self.call_ids),
            "raw_refs": list(self.raw_refs),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "EventSpan":
        value = cls.create(
            span_type=str(data["span_type"]),
            node_ids=tuple(map(str, data.get("node_ids", ()))),
            message_ordinals=tuple(
                map(int, data.get("message_ordinals", ()))
            ),
            call_ids=tuple(map(str, data.get("call_ids", ()))),
            raw_refs=tuple(map(str, data.get("raw_refs", ()))),
        )
        declared = data.get("span_id")
        if declared is not None and declared != value.span_id:
            raise ValueError("EventSpan id mismatch")
        return value


@dataclass(frozen=True, slots=True)
class LiveSubgraph:
    lifecycle_hash: str
    roots_hash: str
    cutoff_step: int
    spans: tuple[EventSpan, ...]
    live_atom_ids: tuple[str, ...]
    live_node_ids: tuple[str, ...]
    evicted_node_ids: tuple[str, ...]
    live_span_ids: tuple[str, ...]
    evicted_span_ids: tuple[str, ...]
    root_provenance: tuple[dict[str, Any], ...]
    closure_provenance: tuple[dict[str, Any], ...]
    lifecycle_reasons: tuple[dict[str, Any], ...]
    uncertainty_records: tuple[dict[str, Any], ...] = ()
    analyzer_version: str = "live_subgraph_v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "spans", tuple(sorted(self.spans, key=lambda item: item.span_id)))
        for field in (
            "live_atom_ids",
            "live_node_ids",
            "evicted_node_ids",
            "live_span_ids",
            "evicted_span_ids",
        ):
            object.__setattr__(self, field, tuple(sorted(set(getattr(self, field)))))
        if set(self.live_node_ids).intersection(self.evicted_node_ids):
            raise ValueError("live and evicted node sets overlap")
        if set(self.live_span_ids).intersection(self.evicted_span_ids):
            raise ValueError("live and evicted span sets overlap")

    @property
    def live_subgraph_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def span_map(self) -> dict[str, EventSpan]:
        return {span.span_id: span for span in self.spans}

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.analyzer_version,
            "lifecycle_hash": self.lifecycle_hash,
            "roots_hash": self.roots_hash,
            "cutoff_step": self.cutoff_step,
            "spans": [item.to_dict() for item in self.spans],
            "live_atom_ids": list(self.live_atom_ids),
            "live_node_ids": list(self.live_node_ids),
            "evicted_node_ids": list(self.evicted_node_ids),
            "live_span_ids": list(self.live_span_ids),
            "evicted_span_ids": list(self.evicted_span_ids),
            "root_provenance": [dict(item) for item in self.root_provenance],
            "closure_provenance": [dict(item) for item in self.closure_provenance],
            "lifecycle_reasons": [dict(item) for item in self.lifecycle_reasons],
            "uncertainty_records": [dict(item) for item in self.uncertainty_records],
        }
        if include_hash:
            result["live_subgraph_hash"] = self.live_subgraph_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LiveSubgraph":
        value = cls(
            lifecycle_hash=str(data["lifecycle_hash"]),
            roots_hash=str(data["roots_hash"]),
            cutoff_step=int(data["cutoff_step"]),
            spans=tuple(
                EventSpan.from_dict(item) for item in data.get("spans", ())
            ),
            live_atom_ids=tuple(map(str, data.get("live_atom_ids", ()))),
            live_node_ids=tuple(map(str, data.get("live_node_ids", ()))),
            evicted_node_ids=tuple(
                map(str, data.get("evicted_node_ids", ()))
            ),
            live_span_ids=tuple(map(str, data.get("live_span_ids", ()))),
            evicted_span_ids=tuple(
                map(str, data.get("evicted_span_ids", ()))
            ),
            root_provenance=tuple(
                dict(item) for item in data.get("root_provenance", ())
            ),
            closure_provenance=tuple(
                dict(item) for item in data.get("closure_provenance", ())
            ),
            lifecycle_reasons=tuple(
                dict(item) for item in data.get("lifecycle_reasons", ())
            ),
            uncertainty_records=tuple(
                dict(item) for item in data.get("uncertainty_records", ())
            ),
            analyzer_version=str(
                data.get("schema_version", "live_subgraph_v1")
            ),
        )
        declared = data.get("live_subgraph_hash")
        if declared is not None and declared != value.live_subgraph_hash:
            raise ValueError("LiveSubgraph hash mismatch")
        return value
