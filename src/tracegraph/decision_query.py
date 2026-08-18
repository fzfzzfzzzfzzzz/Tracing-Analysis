"""Deterministic construction of the next-action query used by GDSC."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .decision_state import DecisionStateGraph, StateAtomType, stable_digest
from .policy_rules import PolicyRule


def _schema_body(schema: Mapping[str, Any]) -> Mapping[str, Any]:
    function = schema.get("function")
    return function if isinstance(function, Mapping) else schema


@dataclass(frozen=True, slots=True)
class DecisionQuery:
    goal_id: str | None
    subgoal_id: str | None
    candidate_action_family: tuple[str, ...]
    candidate_tools: tuple[str, ...]
    required_slots: tuple[str, ...]
    known_entities: tuple[str, ...]
    pending_confirmation: tuple[str, ...]
    side_effect_level: str
    applicable_policy_scope: tuple[str, ...]
    expected_environment_transition: tuple[str, ...]
    uncertainty_reasons: tuple[str, ...] = ()
    referenced_atom_ids: tuple[str, ...] = ()
    referenced_event_ids: tuple[str, ...] = ()
    query_version: str = "decision_query_v1"

    @property
    def query_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "query_version": self.query_version,
            "goal_id": self.goal_id,
            "subgoal_id": self.subgoal_id,
            "candidate_action_family": list(self.candidate_action_family),
            "candidate_tools": list(self.candidate_tools),
            "required_slots": list(self.required_slots),
            "known_entities": list(self.known_entities),
            "pending_confirmation": list(self.pending_confirmation),
            "side_effect_level": self.side_effect_level,
            "applicable_policy_scope": list(self.applicable_policy_scope),
            "expected_environment_transition": list(self.expected_environment_transition),
            "uncertainty_reasons": list(self.uncertainty_reasons),
        }
        # Phase 5 query-reactivation hints are optional extensions. Omitting
        # empty values preserves every historical decision_query_v1 hash.
        if self.referenced_atom_ids:
            result["referenced_atom_ids"] = list(self.referenced_atom_ids)
        if self.referenced_event_ids:
            result["referenced_event_ids"] = list(self.referenced_event_ids)
        if include_hash:
            result["query_hash"] = self.query_hash
        return result

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DecisionQuery":
        values = dict(data)
        values.pop("query_hash", None)
        for key in (
            "candidate_action_family",
            "candidate_tools",
            "required_slots",
            "known_entities",
            "pending_confirmation",
            "applicable_policy_scope",
            "expected_environment_transition",
            "uncertainty_reasons",
            "referenced_atom_ids",
            "referenced_event_ids",
        ):
            values[key] = tuple(map(str, values.get(key, ())))
        query = cls(**values)
        declared = data.get("query_hash")
        if declared is not None and declared != query.query_hash:
            raise ValueError("DecisionQuery hash mismatch")
        return query


def build_decision_query(
    state: DecisionStateGraph,
    *,
    tool_schemas: Sequence[Mapping[str, Any]] = (),
    policy_rules: Sequence[PolicyRule] = (),
) -> DecisionQuery:
    goals = state.find_atoms(StateAtomType.ACTIVE_GOAL, status="current")
    subgoals = state.find_atoms(StateAtomType.OPEN_SUBGOAL, status="current")
    pending = state.find_atoms(StateAtomType.PENDING_OPERATION, status="current")
    confirmations = state.find_atoms(StateAtomType.CONFIRMATION_REQUIREMENT, status="current")
    unknown = state.find_atoms(StateAtomType.UNKNOWN_SLOT, status="current")
    entities = state.find_atoms(StateAtomType.ENTITY, StateAtomType.SLOT_VALUE, status="current")

    schema_names: set[str] = set()
    schema_required: dict[str, set[str]] = {}
    for schema in tool_schemas:
        body = _schema_body(schema)
        name = str(body.get("name") or schema.get("name") or "")
        if not name:
            continue
        schema_names.add(name)
        parameters = body.get("parameters")
        required = parameters.get("required", ()) if isinstance(parameters, Mapping) else ()
        schema_required[name] = set(map(str, required))

    pending_tools = {
        str(atom.value.get("tool_name"))
        for atom in pending
        if isinstance(atom.value, Mapping) and atom.value.get("tool_name")
    }
    uncertainty: list[str] = []
    if not pending_tools:
        uncertainty.append("no_pending_operation")
    # Conservative expansion: schemas are never discarded merely because the
    # reducer is unsure which pending operation is next.
    candidates = pending_tools | schema_names
    if not candidates:
        uncertainty.append("no_tool_schema")

    required = {
        str(atom.value.get("slot"))
        for atom in unknown
        if isinstance(atom.value, Mapping) and atom.value.get("slot")
    }
    for name in candidates:
        required.update(schema_required.get(name, set()))

    policy_scope: set[str] = set()
    for rule in policy_rules:
        if not rule.trigger_tools or candidates.intersection(rule.trigger_tools):
            policy_scope.update(rule.scope)
    for atom in state.find_atoms(
        StateAtomType.GLOBAL_POLICY_RULE,
        StateAtomType.APPLICABLE_POLICY_RULE,
        status="current",
    ):
        value = atom.value if isinstance(atom.value, Mapping) else {}
        policy_scope.update(map(str, value.get("scope", ("global",))))

    transitions = []
    side_effect = "none"
    for atom in pending:
        value = atom.value if isinstance(atom.value, Mapping) else {}
        tool_name = str(value.get("tool_name") or "")
        if tool_name:
            transitions.append(f"complete:{tool_name}")
        if value.get("side_effect"):
            side_effect = "irreversible"

    return DecisionQuery(
        goal_id=goals[-1].atom_id if goals else None,
        subgoal_id=subgoals[-1].atom_id if subgoals else None,
        candidate_action_family=tuple(sorted(candidates)),
        candidate_tools=tuple(sorted(candidates)),
        required_slots=tuple(sorted(required)),
        known_entities=tuple(sorted(atom.atom_id for atom in entities)),
        pending_confirmation=tuple(sorted(atom.atom_id for atom in confirmations)),
        side_effect_level=side_effect,
        applicable_policy_scope=tuple(sorted(policy_scope or {"global"})),
        expected_environment_transition=tuple(sorted(transitions)),
        uncertainty_reasons=tuple(uncertainty),
    )
