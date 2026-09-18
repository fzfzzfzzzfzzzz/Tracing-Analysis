"""Named policy registry for baselines, the graph policy, and its ablations."""

from __future__ import annotations

from collections.abc import Iterator

from .policy import ContextPolicy, GraphConstrainedPolicy


class PolicyRegistry:
    """Deterministic registry that rejects accidental policy replacement."""

    def __init__(self) -> None:
        self._policies: dict[str, ContextPolicy] = {}

    def register(self, policy: ContextPolicy, *, replace: bool = False) -> None:
        if policy.policy_id in self._policies and not replace:
            raise KeyError(f"policy already registered: {policy.policy_id}")
        self._policies[policy.policy_id] = policy

    def get(self, policy_id: str) -> ContextPolicy:
        try:
            return self._policies[policy_id]
        except KeyError as error:
            raise KeyError(f"unknown context policy: {policy_id}") from error

    def ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._policies))

    def __iter__(self) -> Iterator[ContextPolicy]:
        for policy_id in self.ids():
            yield self._policies[policy_id]

    @classmethod
    def defaults(cls) -> "PolicyRegistry":
        registry = cls()
        configurations = (
            ("full-history", {"selection_mode": "full"}),
            ("last-k", {"selection_mode": "last_k"}),
            ("token-pruning", {"selection_mode": "token"}),
            ("summary", {"selection_mode": "summary"}),
            ("llm-only", {"selection_mode": "llm-only"}),
            ("agentdiet", {"selection_mode": "token"}),
            ("acon", {"selection_mode": "summary"}),
            ("graph-v4", {}),
            ("graph-v4-no-edges", {"causal_closure": False}),
            (
                "graph-v4-no-lifecycle",
                {
                    "preserve_failures": False,
                    "preserve_constraints": False,
                },
            ),
            ("graph-v4-no-failure-retention", {"preserve_failures": False}),
            ("graph-v4-no-constraint-retention", {"preserve_constraints": False}),
        )
        for policy_id, options in configurations:
            registry.register(GraphConstrainedPolicy(policy_id, **options))
        return registry


DEFAULT_POLICIES = PolicyRegistry.defaults()
