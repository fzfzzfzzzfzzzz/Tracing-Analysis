"""Experimental public-goal scoping and explicit summary fallback, never default 0.4."""

from dataclasses import replace

from ..compression_audit.models import PrefixRecord

NAME = "tracegraph_goal_scoped_fallback"


def scoped_prefix(prefix: PrefixRecord):
    """Reject explicit relation links across two different *observed* goal IDs.

    Missing ownership stays unknown; no gold labels or adjacency-based resolutions.
    Tool-call/result identities are untouched.
    """
    owners = {e["event_id"]: e.get("goal_id") for e in prefix.events}
    dropped, unknown, events = [], [], []
    for event in prefix.events:
        value = dict(event)
        relations = []
        for relation in event.get("relations", ()):
            left, right = owners.get(relation["source"]), owners[event["event_id"]]
            if left is not None and right is not None and left != right:
                dropped.append({"source": relation["source"], "target": event["event_id"],
                                "reason": "explicit_goal_conflict"})
            else:
                relations.append(relation)
                if left is None or right is None:
                    unknown.append([relation["source"], event["event_id"]])
        value["relations"] = relations
        events.append(value)
    return replace(prefix, events=tuple(events)), {"rejected_cross_goal_relations": dropped,
        "unknown_goal_relations": unknown, "algorithm_status": "experimental_unvalidated",
        "selection_unit": "formal_policy_pair_and_dependency_spans"}
