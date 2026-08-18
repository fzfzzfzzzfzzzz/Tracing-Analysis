"""Conservative deterministic policy-rule representation and checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from .decision_state import stable_digest, stable_state_id


@dataclass(frozen=True, slots=True)
class PolicyRule:
    rule_id: str
    text: str
    scope: tuple[str, ...] = ("global",)
    trigger_tools: tuple[str, ...] = ()
    required_fields: tuple[str, ...] = ()
    requires_confirmation: bool = False
    denied_tools: tuple[str, ...] = ()
    source_event_ids: tuple[str, ...] = ()
    verifier: str = "deterministic_policy_rule_v1"
    parse_complete: bool = True

    def applies_to(self, tool_name: str | None) -> bool:
        return not self.trigger_tools or tool_name is None or tool_name in self.trigger_tools

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "text": self.text,
            "scope": list(self.scope),
            "trigger_tools": list(self.trigger_tools),
            "required_fields": list(self.required_fields),
            "requires_confirmation": self.requires_confirmation,
            "denied_tools": list(self.denied_tools),
            "source_event_ids": list(self.source_event_ids),
            "verifier": self.verifier,
            "parse_complete": self.parse_complete,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PolicyRule":
        values = dict(data)
        for key in (
            "scope",
            "trigger_tools",
            "required_fields",
            "denied_tools",
            "source_event_ids",
        ):
            values[key] = tuple(str(item) for item in values.get(key, ()))
        return cls(**values)


@dataclass(frozen=True, slots=True)
class PolicyCheck:
    allowed: bool
    violations: tuple[str, ...]
    required_confirmation: bool
    missing_fields: tuple[str, ...]
    checked_rule_ids: tuple[str, ...]


def compile_policy_rule(
    value: str | Mapping[str, Any],
    *,
    source_event_ids: Iterable[str] = (),
) -> PolicyRule:
    """Compile explicit fields; free text is retained whole and never guessed."""

    sources = tuple(sorted(set(str(item) for item in source_event_ids)))

    def normalized(field: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
        raw = value.get(field, default) if isinstance(value, Mapping) else default
        if isinstance(raw, str):
            raw = (raw,)
        return tuple(sorted(set(map(str, raw))))

    if isinstance(value, str):
        payload: dict[str, Any] = {
            "text": value,
            "scope": ("global",),
            "source_event_ids": sources,
            "parse_complete": False,
            "verifier": "full_policy_text_retention_v1",
        }
    else:
        payload = {
            "text": str(value.get("text") or value.get("rule") or ""),
            "scope": normalized("scope", ("global",)),
            "trigger_tools": normalized("trigger_tools"),
            "required_fields": normalized("required_fields"),
            "requires_confirmation": bool(value.get("requires_confirmation", False)),
            "denied_tools": normalized("denied_tools"),
            "source_event_ids": sources,
            "parse_complete": bool(value.get("parse_complete", True)),
            "verifier": str(value.get("verifier", "deterministic_policy_rule_v1")),
        }
    signature = dict(payload)
    payload["rule_id"] = stable_state_id("rule", signature)
    return PolicyRule(**payload)


def check_action(
    rules: Iterable[PolicyRule],
    *,
    tool_name: str,
    arguments: Mapping[str, Any],
    confirmed: bool = False,
) -> PolicyCheck:
    violations: list[str] = []
    missing: set[str] = set()
    confirmation = False
    checked: list[str] = []
    for rule in sorted(rules, key=lambda item: item.rule_id):
        if not rule.applies_to(tool_name):
            continue
        checked.append(rule.rule_id)
        if tool_name in rule.denied_tools:
            violations.append(f"{rule.rule_id}:tool_denied")
        missing.update(
            field
            for field in rule.required_fields
            if arguments.get(field) in (None, "")
        )
        if rule.requires_confirmation and not confirmed:
            confirmation = True
    if missing:
        violations.append("missing_required_policy_fields")
    if confirmation:
        violations.append("confirmation_required")
    return PolicyCheck(
        allowed=not violations,
        violations=tuple(violations),
        required_confirmation=confirmation,
        missing_fields=tuple(sorted(missing)),
        checked_rule_ids=tuple(checked),
    )


def policy_set_hash(rules: Iterable[PolicyRule]) -> str:
    return stable_digest([rule.to_dict() for rule in sorted(rules, key=lambda item: item.rule_id)])
