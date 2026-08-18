"""Auditable final provider request produced by the GDSC compiler."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from .provider_cost import PromptCost, provider_prompt_request, request_sha256


@dataclass(frozen=True, slots=True)
class PromptBundle:
    messages: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...]
    representation_manifest: tuple[dict[str, Any], ...]
    closure_provenance: tuple[dict[str, Any], ...]
    request_hash: str
    costs: PromptCost
    compiler_decision_log: tuple[dict[str, Any], ...]
    provider_protocol: dict[str, Any]
    provenance_manifest: dict[str, Any]
    matched_budget_eligible: bool = True
    budget_infeasible: bool = False
    hard_limit_exceeded: bool = False
    compiler_version: str = "gdsc_core_v1"

    def __post_init__(self) -> None:
        object.__setattr__(self, "messages", tuple(dict(item) for item in self.messages))
        object.__setattr__(self, "tools", tuple(dict(item) for item in self.tools))
        expected = request_sha256(self.request)
        if self.request_hash != expected:
            raise ValueError("PromptBundle request hash does not match final request")
        if self.hard_limit_exceeded and self.matched_budget_eligible:
            raise ValueError("hard-limit-exceeded bundle cannot be matched-budget eligible")
        if self.budget_infeasible and self.matched_budget_eligible:
            raise ValueError("budget-infeasible bundle cannot be matched-budget eligible")

    @property
    def request(self) -> dict[str, Any]:
        return provider_prompt_request(
            model=str(self.provider_protocol.get("model")),
            messages=self.messages,
            tools=self.tools,
        )

    @property
    def serialized_token_cost(self) -> int:
        return self.costs.serialized_request

    def with_provider_actual(
        self,
        input_tokens: int,
        *,
        cost_usd: float | None = None,
    ) -> "PromptBundle":
        return replace(self, costs=self.costs.with_actual(input_tokens, cost_usd))

    def to_dict(self) -> dict[str, Any]:
        return {
            "compiler_version": self.compiler_version,
            "messages": [dict(message) for message in self.messages],
            "tools": [dict(tool) for tool in self.tools],
            "request_hash": self.request_hash,
            "costs": self.costs.to_dict(),
            "matched_budget_eligible": self.matched_budget_eligible,
            "budget_infeasible": self.budget_infeasible,
            "hard_limit_exceeded": self.hard_limit_exceeded,
            "provider_protocol": dict(self.provider_protocol),
            "provenance_manifest": dict(self.provenance_manifest),
            "representation_manifest": [dict(item) for item in self.representation_manifest],
            "closure_provenance": [dict(item) for item in self.closure_provenance],
            "compiler_decision_log": [dict(item) for item in self.compiler_decision_log],
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "PromptBundle":
        values = dict(data)
        values["messages"] = tuple(dict(item) for item in values.get("messages", ()))
        values["tools"] = tuple(dict(item) for item in values.get("tools", ()))
        values["representation_manifest"] = tuple(
            dict(item) for item in values.get("representation_manifest", ())
        )
        values["closure_provenance"] = tuple(
            dict(item) for item in values.get("closure_provenance", ())
        )
        values["compiler_decision_log"] = tuple(
            dict(item) for item in values.get("compiler_decision_log", ())
        )
        values["costs"] = PromptCost.from_dict(values["costs"])
        return cls(**values)
