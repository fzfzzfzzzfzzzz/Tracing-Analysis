"""Deterministic, decision-facing state graph for GDSC.

The existing :class:`TraceGraph` remains the immutable audit/event layer.  This
module deliberately defines a separate vocabulary so adding decision state
does not change the historical ``NodeType`` or its serialized artifacts.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable


def canonical_json(value: Any) -> str:
    """Serialize JSON data identically across runs and dictionary orderings."""

    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def stable_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def stable_state_id(prefix: str, value: Any) -> str:
    return f"{prefix}_{stable_digest(value)[:24]}"


class StateAtomType(str, Enum):
    ACTIVE_GOAL = "active_goal"
    OPEN_SUBGOAL = "open_subgoal"
    ENTITY = "entity"
    SLOT_VALUE = "slot_value"
    UNKNOWN_SLOT = "unknown_slot"
    KNOWN_FACT = "known_fact"
    CONFLICTING_FACT = "conflicting_fact"
    SUPERSEDED_FACT = "superseded_fact"
    PENDING_OPERATION = "pending_operation"
    COMPLETED_OPERATION = "completed_operation"
    PRECONDITION = "precondition"
    CONFIRMATION_REQUIREMENT = "confirmation_requirement"
    APPLICABLE_POLICY_RULE = "applicable_policy_rule"
    GLOBAL_POLICY_RULE = "global_policy_rule"
    CRITICAL_EVIDENCE = "critical_evidence"
    EVIDENCE_SET = "evidence_set"
    STATE_DELTA = "state_delta"
    NEGATIVE_GUARD = "negative_guard"
    CANDIDATE_ALTERNATIVE = "candidate_alternative"
    ARCHIVE_HANDLE = "archive_handle"
    SIDE_EFFECT_RECEIPT = "side_effect_receipt"


class StateEdgeType(str, Enum):
    REQUIRED_FOR = "required_for"
    FILLS = "fills"
    SUPPORTS = "supports"
    BLOCKS = "blocks"
    SATISFIES = "satisfies"
    VIOLATES = "violates"
    SUPERSEDES = "supersedes"
    CONFLICTS_WITH = "conflicts_with"
    DERIVED_FROM = "derived_from"
    RESOLVES = "resolves"
    ALTERNATIVE_FOR = "alternative_for"


@dataclass(frozen=True, slots=True)
class StateAtom:
    atom_id: str
    atom_type: StateAtomType
    key: str
    value: Any
    source_event_ids: tuple[str, ...]
    verified: bool = True
    hard: bool = False
    status: str = "current"
    confidence: float = 1.0
    verifier: str = "deterministic_event_reducer_v1"
    raw_refs: tuple[str, ...] = ()
    metadata: tuple[tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not self.source_event_ids:
            raise ValueError("every state atom requires EventGraph provenance")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("state atom confidence must be between 0 and 1")
        object.__setattr__(self, "source_event_ids", tuple(sorted(set(self.source_event_ids))))
        object.__setattr__(self, "raw_refs", tuple(sorted(set(self.raw_refs))))
        object.__setattr__(self, "metadata", tuple(sorted(self.metadata, key=lambda item: item[0])))

    @classmethod
    def create(
        cls,
        atom_type: StateAtomType,
        key: str,
        value: Any,
        source_event_ids: Iterable[str],
        **kwargs: Any,
    ) -> "StateAtom":
        sources = tuple(sorted(set(source_event_ids)))
        signature = {
            "atom_type": atom_type.value,
            "key": key,
            "value": value,
            "source_event_ids": sources,
            "status": kwargs.get("status", "current"),
        }
        return cls(
            atom_id=stable_state_id("atom", signature),
            atom_type=atom_type,
            key=key,
            value=value,
            source_event_ids=sources,
            **kwargs,
        )

    def metadata_dict(self) -> dict[str, Any]:
        return dict(self.metadata)

    def to_dict(self) -> dict[str, Any]:
        return {
            "atom_id": self.atom_id,
            "atom_type": self.atom_type.value,
            "key": self.key,
            "value": self.value,
            "source_event_ids": list(self.source_event_ids),
            "verified": self.verified,
            "hard": self.hard,
            "status": self.status,
            "confidence": self.confidence,
            "verifier": self.verifier,
            "raw_refs": list(self.raw_refs),
            "metadata": self.metadata_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StateAtom":
        values = dict(data)
        values["atom_type"] = StateAtomType(values["atom_type"])
        values["source_event_ids"] = tuple(values.get("source_event_ids", ()))
        values["raw_refs"] = tuple(values.get("raw_refs", ()))
        metadata = values.get("metadata", {})
        values["metadata"] = tuple(sorted(dict(metadata).items()))
        return cls(**values)


@dataclass(frozen=True, slots=True)
class StateEdge:
    edge_id: str
    source: str
    target: str
    edge_type: StateEdgeType
    source_event_ids: tuple[str, ...]
    confidence: float = 1.0

    def __post_init__(self) -> None:
        if not self.source_event_ids:
            raise ValueError("every state edge requires EventGraph provenance")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("state edge confidence must be between 0 and 1")
        object.__setattr__(self, "source_event_ids", tuple(sorted(set(self.source_event_ids))))

    @classmethod
    def create(
        cls,
        source: str,
        target: str,
        edge_type: StateEdgeType,
        source_event_ids: Iterable[str],
        *,
        confidence: float = 1.0,
    ) -> "StateEdge":
        sources = tuple(sorted(set(source_event_ids)))
        signature = {
            "source": source,
            "target": target,
            "edge_type": edge_type.value,
            "source_event_ids": sources,
        }
        return cls(
            edge_id=stable_state_id("state_edge", signature),
            source=source,
            target=target,
            edge_type=edge_type,
            source_event_ids=sources,
            confidence=confidence,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": self.edge_id,
            "source": self.source,
            "target": self.target,
            "edge_type": self.edge_type.value,
            "source_event_ids": list(self.source_event_ids),
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StateEdge":
        values = dict(data)
        values["edge_type"] = StateEdgeType(values["edge_type"])
        values["source_event_ids"] = tuple(values.get("source_event_ids", ()))
        return cls(**values)


@dataclass(frozen=True, slots=True)
class DecisionStateGraph:
    """Canonical snapshot produced only from events at or before ``cutoff_step``."""

    event_graph_session_id: str
    cutoff_step: int
    atoms: tuple[StateAtom, ...]
    edges: tuple[StateEdge, ...] = ()
    reducer_version: str = "decision_state_reducer_v1"

    def __post_init__(self) -> None:
        atoms = tuple(sorted(self.atoms, key=lambda atom: atom.atom_id))
        edges = tuple(sorted(self.edges, key=lambda edge: edge.edge_id))
        atom_ids = [atom.atom_id for atom in atoms]
        if len(atom_ids) != len(set(atom_ids)):
            raise ValueError("duplicate state atom id")
        known = set(atom_ids)
        dangling = [edge.edge_id for edge in edges if edge.source not in known or edge.target not in known]
        if dangling:
            raise ValueError(f"state edges have missing endpoints: {dangling}")
        object.__setattr__(self, "atoms", atoms)
        object.__setattr__(self, "edges", edges)

    @property
    def state_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def atom_map(self) -> dict[str, StateAtom]:
        return {atom.atom_id: atom for atom in self.atoms}

    def find_atoms(
        self,
        *atom_types: StateAtomType,
        status: str | None = None,
    ) -> tuple[StateAtom, ...]:
        allowed = set(atom_types)
        return tuple(
            atom
            for atom in self.atoms
            if (not allowed or atom.atom_type in allowed)
            and (status is None or atom.status == status)
        )

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": "decision_state_graph_v1",
            "event_graph_session_id": self.event_graph_session_id,
            "cutoff_step": self.cutoff_step,
            "reducer_version": self.reducer_version,
            "atoms": [atom.to_dict() for atom in self.atoms],
            "edges": [edge.to_dict() for edge in self.edges],
        }
        if include_hash:
            result["state_hash"] = self.state_hash
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DecisionStateGraph":
        graph = cls(
            event_graph_session_id=str(data["event_graph_session_id"]),
            cutoff_step=int(data["cutoff_step"]),
            reducer_version=str(data.get("reducer_version", "decision_state_reducer_v1")),
            atoms=tuple(StateAtom.from_dict(item) for item in data.get("atoms", ())),
            edges=tuple(StateEdge.from_dict(item) for item in data.get("edges", ())),
        )
        declared = data.get("state_hash")
        if declared is not None and declared != graph.state_hash:
            raise ValueError("DecisionStateGraph hash mismatch")
        return graph
