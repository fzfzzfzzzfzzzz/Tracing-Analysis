"""Machine-checkable provenance and equivalence checks for representations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .decision_state import StateAtom
from .representations import RepresentationCandidate, RepresentationType


@dataclass(frozen=True, slots=True)
class RepresentationVerification:
    ok: bool
    errors: tuple[str, ...] = ()


def verify_provenance(
    candidate: RepresentationCandidate,
    *,
    event_ids: set[str],
) -> RepresentationVerification:
    missing = sorted(set(candidate.source_ids).difference(event_ids))
    return RepresentationVerification(
        ok=not missing,
        errors=tuple(f"missing source event: {item}" for item in missing),
    )


def verify_structured_equivalence(
    candidate: RepresentationCandidate,
    atom: StateAtom,
) -> RepresentationVerification:
    if candidate.representation_type != RepresentationType.STRUCTURED_STATE_DELTA:
        return RepresentationVerification(False, ("not a structured-state representation",))
    payload = candidate.payload if isinstance(candidate.payload, Mapping) else {}
    expected = {
        "type": atom.atom_type.value,
        "key": atom.key,
        "value": atom.value,
        "status": atom.status,
        "verified": atom.verified,
    }
    mismatches = [key for key, value in expected.items() if payload.get(key) != value]
    return RepresentationVerification(
        ok=not mismatches,
        errors=tuple(f"structured field mismatch: {key}" for key in mismatches),
    )


def verify_summary_claims(
    candidate: RepresentationCandidate,
    atoms: Mapping[str, StateAtom],
) -> RepresentationVerification:
    if candidate.representation_type != RepresentationType.VERIFIED_SUMMARY:
        return RepresentationVerification(False, ("not a verified summary",))
    payload = candidate.payload if isinstance(candidate.payload, Mapping) else {}
    claims = payload.get("claims")
    if not isinstance(claims, list) or not claims:
        return RepresentationVerification(False, ("summary has no claims",))
    errors: list[str] = []
    for claim in claims:
        if not isinstance(claim, Mapping):
            errors.append("summary claim is not an object")
            continue
        atom = atoms.get(str(claim.get("atom_id")))
        if atom is None:
            errors.append(f"unknown summary atom: {claim.get('atom_id')}")
            continue
        if not atom.verified:
            errors.append(f"summary atom is unverified: {atom.atom_id}")
        if claim.get("key") != atom.key or claim.get("value") != atom.value:
            errors.append(f"summary claim mismatch: {atom.atom_id}")
        if atom.atom_id not in candidate.covered_atoms:
            errors.append(f"summary omitted covered atom declaration: {atom.atom_id}")
    return RepresentationVerification(not errors, tuple(errors))


def verify_archive_round_trip(
    candidate: RepresentationCandidate,
    loader: Callable[[str], Any],
) -> RepresentationVerification:
    if candidate.representation_type != RepresentationType.ARCHIVE_HANDLE:
        return RepresentationVerification(False, ("not an archive handle",))
    errors: list[str] = []
    for reference in candidate.raw_refs:
        try:
            loader(reference)
        except (OSError, KeyError, ValueError) as error:
            errors.append(f"archive verification failed for {reference}: {type(error).__name__}")
    return RepresentationVerification(not errors, tuple(errors))


def verify_omit_audit(candidate: RepresentationCandidate) -> RepresentationVerification:
    if candidate.representation_type != RepresentationType.OMIT:
        return RepresentationVerification(False, ("not an omission",))
    errors = []
    if not candidate.omission_reason:
        errors.append("omission reason missing")
    if candidate.omission_risk is None or not 0.0 <= candidate.omission_risk <= 1.0:
        errors.append("omission risk invalid")
    return RepresentationVerification(not errors, tuple(errors))


def verify_candidate(
    candidate: RepresentationCandidate,
    *,
    atom: StateAtom,
    atoms: Mapping[str, StateAtom],
    event_ids: set[str],
) -> RepresentationVerification:
    errors = list(verify_provenance(candidate, event_ids=event_ids).errors)
    if candidate.representation_type == RepresentationType.STRUCTURED_STATE_DELTA:
        errors.extend(verify_structured_equivalence(candidate, atom).errors)
    elif candidate.representation_type == RepresentationType.VERIFIED_SUMMARY:
        errors.extend(verify_summary_claims(candidate, atoms).errors)
    elif candidate.representation_type == RepresentationType.OMIT:
        errors.extend(verify_omit_audit(candidate).errors)
    return RepresentationVerification(not errors, tuple(errors))
