"""Action-conditioned, verifier-backed negative guards."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .decision_state import StateAtom, stable_state_id


_BANNED_GENERIC_ALTERNATIVES = {
    "change arguments",
    "change arguments or use alternative path",
    "retry with different arguments",
    "use alternative path",
    "try again",
}
_ALLOWED_VERIFIER_PREFIXES = ("schema:", "policy:", "environment:", "successful_path:")


@dataclass(frozen=True, slots=True)
class NegativeGuard:
    guard_id: str
    action_family: str
    entity_scope: tuple[str, ...]
    violated_predicate: str
    observed_failure: Any
    failed_argument_delta: dict[str, Any]
    admissible_alternatives: tuple[str, ...]
    required_new_evidence: tuple[str, ...]
    expiry_condition: str
    source_event_ids: tuple[str, ...]
    verifier: str

    def __post_init__(self) -> None:
        if not self.source_event_ids:
            raise ValueError("NegativeGuard requires source-event provenance")
        if not self.violated_predicate:
            raise ValueError("NegativeGuard requires a violated predicate")
        if not self.verifier.startswith(_ALLOWED_VERIFIER_PREFIXES):
            raise ValueError("NegativeGuard verifier must be schema, policy, environment, or success based")
        normalized = {item.strip().lower() for item in self.admissible_alternatives}
        if normalized.intersection(_BANNED_GENERIC_ALTERNATIVES):
            raise ValueError("generic unverified correction fallback is forbidden")
        if not self.admissible_alternatives and not self.required_new_evidence:
            raise ValueError("NegativeGuard requires a verified alternative or new evidence")

    def applies_to(self, candidate_tools: Iterable[str]) -> bool:
        candidates = set(candidate_tools)
        return self.action_family in candidates or not candidates

    def to_dict(self) -> dict[str, Any]:
        return {
            "guard_id": self.guard_id,
            "action_family": self.action_family,
            "entity_scope": list(self.entity_scope),
            "violated_predicate": self.violated_predicate,
            "observed_failure": self.observed_failure,
            "failed_argument_delta": dict(self.failed_argument_delta),
            "admissible_alternatives": list(self.admissible_alternatives),
            "required_new_evidence": list(self.required_new_evidence),
            "expiry_condition": self.expiry_condition,
            "source_event_ids": list(self.source_event_ids),
            "verifier": self.verifier,
        }


def build_negative_guard(
    *,
    action_family: str,
    entity_scope: Iterable[str],
    violated_predicate: str,
    observed_failure: Any,
    failed_argument_delta: Mapping[str, Any] | None,
    admissible_alternatives: Iterable[str] = (),
    required_new_evidence: Iterable[str] = (),
    expiry_condition: str,
    source_event_ids: Iterable[str],
    verifier: str,
) -> NegativeGuard:
    values = {
        "action_family": str(action_family),
        "entity_scope": tuple(sorted(set(map(str, entity_scope)))),
        "violated_predicate": str(violated_predicate),
        "observed_failure": observed_failure,
        "failed_argument_delta": dict(failed_argument_delta or {}),
        "admissible_alternatives": tuple(sorted(set(map(str, admissible_alternatives)))),
        "required_new_evidence": tuple(sorted(set(map(str, required_new_evidence)))),
        "expiry_condition": str(expiry_condition),
        "source_event_ids": tuple(sorted(set(map(str, source_event_ids)))),
        "verifier": str(verifier),
    }
    return NegativeGuard(guard_id=stable_state_id("guard", values), **values)


def guard_from_failure_atom(atom: StateAtom) -> NegativeGuard | None:
    """Build a guard only when the reducer exposed explicit verifier inputs."""

    value = atom.value if isinstance(atom.value, Mapping) else {}
    guard = value.get("negative_guard")
    if not isinstance(guard, Mapping):
        return None
    tool_name = str(guard.get("action_family") or value.get("tool_name") or "")
    verifier = str(guard.get("verifier") or "")
    if not tool_name or not verifier:
        return None
    return build_negative_guard(
        action_family=tool_name,
        entity_scope=guard.get("entity_scope", ()),
        violated_predicate=str(guard.get("violated_predicate") or ""),
        observed_failure=value.get("result"),
        failed_argument_delta=guard.get("failed_argument_delta", {}),
        admissible_alternatives=guard.get("admissible_alternatives", ()),
        required_new_evidence=guard.get("required_new_evidence", ()),
        expiry_condition=str(guard.get("expiry_condition") or "operation_resolved"),
        source_event_ids=atom.source_event_ids,
        verifier=verifier,
    )
