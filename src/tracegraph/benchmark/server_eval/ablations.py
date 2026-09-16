"""Explicit mechanism ablations; safety rules and the baseline remain unchanged."""

from dataclasses import replace

from ...context_engine.policy import GraphConstrainedPolicy

ABLATIONS = {
    "tracegraph_no_causal_closure": {"causal_closure": False},
    "tracegraph_no_failure_priority": {"preserve_failures": False},
    "tracegraph_no_lifecycle_priority": {},
    "tracegraph_no_retrieval": {},
}


class LifecyclePriorityAblation(GraphConstrainedPolicy):
    def _base_selection(self, spans, budget):
        return super()._base_selection(tuple(replace(s, live=s.hard) for s in spans), budget)


class RetrievalAblation(GraphConstrainedPolicy):
    def materialize(self, snapshot, query, provider_protocol):
        # Empty matching features prevent restoration, retaining all hard protection.
        modified = replace(snapshot, snapshot_hash="", spans=tuple(replace(s, lexical_terms=(), entity_terms=(),
            action_terms=(), error_terms=()) for s in snapshot.spans))
        return super().materialize(modified, "", provider_protocol)


def ablated_policy(method: str, counter):
    cls = {"tracegraph_no_lifecycle_priority": LifecyclePriorityAblation,
           "tracegraph_no_retrieval": RetrievalAblation}.get(method, GraphConstrainedPolicy)
    return cls(policy_id=method, token_counter=counter, **ABLATIONS[method])
