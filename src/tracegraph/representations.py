"""Representation lattice for decision-state atoms."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Iterable

from .capture import estimate_tokens
from .decision_state import StateAtom, StateAtomType, stable_state_id
from .negative_guards import NegativeGuard, guard_from_failure_atom


class RepresentationType(str, Enum):
    RAW_MESSAGE = "raw_message"
    STRUCTURED_STATE_DELTA = "structured_state_delta"
    VERIFIED_SUMMARY = "verified_summary"
    NEGATIVE_GUARD = "negative_guard"
    ARCHIVE_HANDLE = "archive_handle"
    OMIT = "omit"


@dataclass(frozen=True, slots=True)
class RepresentationCandidate:
    representation_id: str
    object_id: str
    representation_type: RepresentationType
    payload: Any
    source_ids: tuple[str, ...]
    covered_atoms: tuple[str, ...]
    lost_fields: tuple[str, ...]
    verifier: str
    raw_refs: tuple[str, ...]
    estimated_cost: int
    serialized_marginal_cost: int | None = None
    omission_reason: str | None = None
    omission_risk: float | None = None

    def __post_init__(self) -> None:
        if not self.source_ids:
            raise ValueError("representation requires source provenance")
        if self.estimated_cost < 0:
            raise ValueError("estimated representation cost must be non-negative")
        if self.representation_type == RepresentationType.OMIT:
            if not self.omission_reason or self.omission_risk is None:
                raise ValueError("Omit requires an audited reason and risk")
            if self.covered_atoms:
                raise ValueError("Omit cannot claim atom coverage")
        elif not self.covered_atoms:
            raise ValueError("non-Omit representation must cover at least one atom")

    @classmethod
    def create(
        cls,
        object_id: str,
        representation_type: RepresentationType,
        payload: Any,
        source_ids: Iterable[str],
        covered_atoms: Iterable[str],
        lost_fields: Iterable[str],
        verifier: str,
        raw_refs: Iterable[str] = (),
        **kwargs: Any,
    ) -> "RepresentationCandidate":
        sources = tuple(sorted(set(source_ids)))
        covered = tuple(sorted(set(covered_atoms)))
        lost = tuple(sorted(set(lost_fields)))
        refs = tuple(sorted(set(raw_refs)))
        signature = {
            "object_id": object_id,
            "representation_type": representation_type.value,
            "payload": payload,
            "source_ids": sources,
            "covered_atoms": covered,
            "lost_fields": lost,
            "raw_refs": refs,
        }
        return cls(
            representation_id=stable_state_id("repr", signature),
            object_id=object_id,
            representation_type=representation_type,
            payload=payload,
            source_ids=sources,
            covered_atoms=covered,
            lost_fields=lost,
            verifier=verifier,
            raw_refs=refs,
            estimated_cost=estimate_tokens(payload),
            **kwargs,
        )

    def with_marginal_cost(self, cost: int) -> "RepresentationCandidate":
        return replace(self, serialized_marginal_cost=int(cost))

    def to_dict(self) -> dict[str, Any]:
        return {
            "representation_id": self.representation_id,
            "object_id": self.object_id,
            "representation_type": self.representation_type.value,
            "payload": self.payload,
            "source_ids": list(self.source_ids),
            "covered_atoms": list(self.covered_atoms),
            "lost_fields": list(self.lost_fields),
            "verifier": self.verifier,
            "raw_refs": list(self.raw_refs),
            "estimated_cost": self.estimated_cost,
            "serialized_marginal_cost": self.serialized_marginal_cost,
            "omission_reason": self.omission_reason,
            "omission_risk": self.omission_risk,
        }


def _structured_payload(atom: StateAtom) -> dict[str, Any]:
    return {
        "type": atom.atom_type.value,
        "key": atom.key,
        "value": atom.value,
        "status": atom.status,
        "verified": atom.verified,
    }


def _summary_payload(atom: StateAtom) -> dict[str, Any]:
    # Summary v1 is a deterministic rendering of a single verified claim, not
    # free-form generated prose.
    return {
        "summary_schema": "verified_summary_v1",
        "claims": [
            {
                "atom_id": atom.atom_id,
                "key": atom.key,
                "value": atom.value,
                "status": atom.status,
            }
        ],
    }


def generate_representations(
    atom: StateAtom,
    *,
    allow_omit: bool,
    omission_risk: float,
    omission_reason: str,
    negative_guard: NegativeGuard | None = None,
) -> tuple[RepresentationCandidate, ...]:
    candidates: list[RepresentationCandidate] = []
    raw_payload = {
        "representation": "raw_event_projection",
        "atom": atom.to_dict(),
    }
    candidates.append(
        RepresentationCandidate.create(
            atom.atom_id,
            RepresentationType.RAW_MESSAGE,
            raw_payload,
            atom.source_event_ids,
            (atom.atom_id,),
            (),
            "raw_event_identity_v1",
            atom.raw_refs,
        )
    )
    candidates.append(
        RepresentationCandidate.create(
            atom.atom_id,
            RepresentationType.STRUCTURED_STATE_DELTA,
            _structured_payload(atom),
            atom.source_event_ids,
            (atom.atom_id,),
            (),
            "structured_state_equivalence_v1",
            atom.raw_refs,
        )
    )
    if atom.verified:
        candidates.append(
            RepresentationCandidate.create(
                atom.atom_id,
                RepresentationType.VERIFIED_SUMMARY,
                _summary_payload(atom),
                atom.source_event_ids,
                (atom.atom_id,),
                (),
                "verified_claim_coverage_v1",
                atom.raw_refs,
            )
        )
    guard = negative_guard or guard_from_failure_atom(atom)
    if guard is not None:
        candidates.append(
            RepresentationCandidate.create(
                atom.atom_id,
                RepresentationType.NEGATIVE_GUARD,
                guard.to_dict(),
                atom.source_event_ids,
                (atom.atom_id,),
                ("raw_failure_payload",),
                guard.verifier,
                atom.raw_refs,
            )
        )
    critical_types = {
        StateAtomType.CRITICAL_EVIDENCE,
        StateAtomType.SIDE_EFFECT_RECEIPT,
        StateAtomType.CONFIRMATION_REQUIREMENT,
    }
    if atom.raw_refs and not atom.hard and atom.atom_type not in critical_types:
        candidates.append(
            RepresentationCandidate.create(
                atom.atom_id,
                RepresentationType.ARCHIVE_HANDLE,
                {
                    "representation": "archive_handle_v1",
                    "references": list(atom.raw_refs),
                    "source_ids": list(atom.source_event_ids),
                },
                atom.source_event_ids,
                (atom.atom_id,),
                ("active_payload",),
                "content_addressed_archive_v1",
                atom.raw_refs,
            )
        )
    if allow_omit:
        candidates.append(
            RepresentationCandidate.create(
                atom.atom_id,
                RepresentationType.OMIT,
                {"reason": omission_reason, "risk": omission_risk},
                atom.source_event_ids,
                (),
                ("all_active_fields",),
                "audited_omission_v1",
                atom.raw_refs,
                omission_reason=omission_reason,
                omission_risk=omission_risk,
            )
        )
    return tuple(sorted(candidates, key=lambda item: (item.representation_type.value, item.representation_id)))
