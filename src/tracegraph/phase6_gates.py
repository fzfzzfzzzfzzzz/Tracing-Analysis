"""Fail-closed E1 eligibility and E2 method gates."""

from __future__ import annotations

import statistics
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from .goal_lifecycle import GoalLifecycleState
from .phase6_scenarios import ScenarioPrefix


E1_THRESHOLDS = {
    "prefix_count": 24,
    "fork_count": 72,
    "oracle_removable_token_share_median_min": 0.20,
    "active_pinned_token_share_each_min": 0.20,
    "full_history_success_rate": 1.0,
    "protocol_valid_rate": 1.0,
    "archive_integrity_rate": 1.0,
    "future_suffix_independence_rate": 1.0,
    "determinism_rate": 1.0,
    "provider_requests": 0,
}

E2_THRESHOLDS = {
    "pinned_evidence_recall_min": 1.0,
    "protocol_valid_rate": 1.0,
    "archive_integrity_rate": 1.0,
    "future_suffix_independence_rate": 1.0,
    "unsafe_eviction_node_rate_max": 0.0,
    "required_subgraph_recall_min": 0.95,
    "distractor_false_reactivation_rate_max": 0.10,
    "active_context_paired_median_delta_max_exclusive": 0.0,
    "reactivate_success_strictly_better_than_eviction_only": True,
    "provider_requests": 0,
}


def _criterion(name: str, observed: Any, required: Any, passed: bool) -> dict[str, Any]:
    return {
        "metric": name,
        "observed": observed,
        "required": required,
        "passed": bool(passed),
    }


def evaluate_e1_eligibility(
    prefixes: Sequence[ScenarioPrefix],
    full_history_rows: Sequence[Mapping[str, Any]],
    *,
    archive_failures: Sequence[str],
    deterministic_match: bool,
) -> dict[str, Any]:
    fork_count = sum(len(item.forks) for item in prefixes)
    removable_shares: list[float] = []
    active_shares: list[float] = []
    has_dormant = []
    has_protected = []
    has_reactivation = []
    nonempty_gold = []
    for prefix in prefixes:
        total = sum(node.token_count for node in prefix.graph.nodes.values())
        removable = sum(
            prefix.graph.nodes[event_id].token_count
            for event_id, state in prefix.lifecycle_gold_by_event.items()
            if state
            in {
                GoalLifecycleState.DORMANT,
                GoalLifecycleState.SUPERSEDED,
                GoalLifecycleState.EPHEMERAL,
            }
        )
        active = sum(
            prefix.graph.nodes[event_id].token_count
            for event_id, state in prefix.lifecycle_gold_by_event.items()
            if state in {GoalLifecycleState.ACTIVE, GoalLifecycleState.PINNED}
        )
        removable_shares.append(removable / total if total else 0.0)
        active_shares.append(active / total if total else 0.0)
        has_dormant.append(
            any(state == GoalLifecycleState.DORMANT for state in prefix.lifecycle_gold_by_event.values())
        )
        has_protected.append(
            any(
                state in {GoalLifecycleState.ACTIVE, GoalLifecycleState.PINNED}
                for state in prefix.lifecycle_gold_by_event.values()
            )
        )
        reactivate = [item for item in prefix.forks if item.fork_type == "REACTIVATE"]
        has_reactivation.append(
            len(reactivate) == 1 and bool(reactivate[0].required_subgraph_event_ids)
        )
        nonempty_gold.extend(
            bool(item.expected_answer_facts) and bool(item.expected_final_state)
            for item in prefix.forks
        )
    by_prefix_manager: dict[tuple[str, str], set[str]] = defaultdict(set)
    for row in full_history_rows:
        by_prefix_manager[(str(row["prefix_id"]), str(row["manager_id"]))].add(
            str(row["eviction_hash"])
        )
    future_independent = all(len(values) == 1 for values in by_prefix_manager.values())
    protocol_rate = (
        sum(bool(row["protocol_valid"]) for row in full_history_rows) / len(full_history_rows)
        if full_history_rows
        else 0.0
    )
    archive_rate = (
        sum(bool(row["archive_integrity"]) for row in full_history_rows)
        / len(full_history_rows)
        if full_history_rows
        else 0.0
    )
    full_success = (
        sum(bool(row["current_task_success"]) for row in full_history_rows)
        / len(full_history_rows)
        if full_history_rows
        else 0.0
    )
    removable_median = statistics.median(removable_shares) if removable_shares else 0.0
    active_min = min(active_shares, default=0.0)
    provider_requests = sum(int(row.get("provider_requests", 0)) for row in full_history_rows)
    criteria = [
        _criterion("prefix_count", len(prefixes), 24, len(prefixes) == 24),
        _criterion("fork_count", fork_count, 72, fork_count == 72),
        _criterion("all_forks_have_nonempty_gold", all(nonempty_gold), True, all(nonempty_gold)),
        _criterion("every_prefix_has_dormant", all(has_dormant), True, all(has_dormant)),
        _criterion("every_prefix_has_active_or_pinned", all(has_protected), True, all(has_protected)),
        _criterion("every_prefix_has_reactivation_gold", all(has_reactivation), True, all(has_reactivation)),
        _criterion(
            "oracle_removable_token_share_median",
            removable_median,
            ">=0.20",
            removable_median >= 0.20,
        ),
        _criterion(
            "active_pinned_token_share_each",
            active_min,
            ">=0.20",
            active_min >= 0.20,
        ),
        _criterion("full_history_success_rate", full_success, 1.0, full_success == 1.0),
        _criterion("protocol_valid_rate", protocol_rate, 1.0, protocol_rate == 1.0),
        _criterion("archive_integrity_rate", archive_rate, 1.0, archive_rate == 1.0),
        _criterion("archive_hash_failures", len(archive_failures), 0, not archive_failures),
        _criterion(
            "future_suffix_independence_rate",
            1.0 if future_independent else 0.0,
            1.0,
            future_independent,
        ),
        _criterion(
            "determinism_rate",
            1.0 if deterministic_match else 0.0,
            1.0,
            deterministic_match,
        ),
        _criterion(
            "always_keep_and_always_evict_nondegenerate",
            bool(all(has_protected) and all(has_reactivation)),
            True,
            bool(all(has_protected) and all(has_reactivation)),
        ),
        _criterion("provider_requests", provider_requests, 0, provider_requests == 0),
    ]
    return {
        "schema_version": "phase6_e1_gate_v1",
        "decision": "pass" if all(item["passed"] for item in criteria) else "fail",
        "criteria": criteria,
        "population": {"prefixes": len(prefixes), "forks": fork_count},
        "removable_token_shares": removable_shares,
        "active_pinned_token_shares": active_shares,
        "thresholds": E1_THRESHOLDS,
    }


def evaluate_e2_gate(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    m5 = [row for row in rows if row["manager_id"] == "M5_lifecycle_causal_reactivation"]
    m0_by_fork = {
        str(row["fork_id"]): row
        for row in rows
        if row["manager_id"] == "M0_full_history"
    }
    m3_reactivate = [
        row
        for row in rows
        if row["manager_id"] == "M3_lifecycle_eviction_only"
        and row["fork_type"] == "REACTIVATE"
    ]
    m5_reactivate = [row for row in m5 if row["fork_type"] == "REACTIVATE"]
    m5_distractor = [row for row in m5 if row["fork_type"] == "DISTRACTOR"]
    by_prefix_hashes: dict[str, set[str]] = defaultdict(set)
    shared_lifecycle_hashes: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        if row["manager_id"] in {
            "M3_lifecycle_eviction_only",
            "M4_lifecycle_flat_reactivation",
            "M5_lifecycle_causal_reactivation",
        }:
            by_prefix_hashes[str(row["prefix_id"])].add(str(row["eviction_hash"]))
            shared_lifecycle_hashes[str(row["prefix_id"])].add(str(row["lifecycle_hash"]))
    future_independent = all(len(values) == 1 for values in by_prefix_hashes.values())
    lifecycle_independent = all(len(values) == 1 for values in shared_lifecycle_hashes.values())
    pinned_min = min((float(row["pinned_evidence_recall"]) for row in m5), default=0.0)
    protocol_rate = sum(bool(row["protocol_valid"]) for row in m5) / len(m5) if m5 else 0.0
    archive_rate = sum(bool(row["archive_integrity"]) for row in m5) / len(m5) if m5 else 0.0
    unsafe_max = max((float(row["unsafe_eviction_node_rate"]) for row in m5), default=1.0)
    recall = (
        statistics.fmean(float(row["required_subgraph_recall"]) for row in m5_reactivate)
        if m5_reactivate
        else 0.0
    )
    distractor_rate = (
        sum(bool(row["distractor_false_reactivation"]) for row in m5_distractor)
        / len(m5_distractor)
        if m5_distractor
        else 1.0
    )
    token_deltas = [
        float(row["active_context_tokens"])
        - float(m0_by_fork[str(row["fork_id"])]["active_context_tokens"])
        for row in m5
        if str(row["fork_id"]) in m0_by_fork
    ]
    median_delta = statistics.median(token_deltas) if token_deltas else 0.0
    m5_success = (
        sum(bool(row["current_task_success"]) for row in m5_reactivate) / len(m5_reactivate)
        if m5_reactivate
        else 0.0
    )
    m3_success = (
        sum(bool(row["current_task_success"]) for row in m3_reactivate) / len(m3_reactivate)
        if m3_reactivate
        else 0.0
    )
    provider_requests = sum(int(row.get("provider_requests", 0)) for row in rows)
    criteria = [
        _criterion("pinned_evidence_recall_min", pinned_min, 1.0, pinned_min == 1.0),
        _criterion("protocol_valid_rate", protocol_rate, 1.0, protocol_rate == 1.0),
        _criterion("archive_integrity_rate", archive_rate, 1.0, archive_rate == 1.0),
        _criterion(
            "future_suffix_independence_rate",
            1.0 if future_independent and lifecycle_independent else 0.0,
            1.0,
            future_independent and lifecycle_independent,
        ),
        _criterion("unsafe_eviction_node_rate_max", unsafe_max, 0.0, unsafe_max == 0.0),
        _criterion("required_subgraph_recall", recall, ">=0.95", recall >= 0.95),
        _criterion(
            "distractor_false_reactivation_rate",
            distractor_rate,
            "<=0.10",
            distractor_rate <= 0.10,
        ),
        _criterion(
            "active_context_paired_median_delta",
            median_delta,
            "<0",
            median_delta < 0,
        ),
        _criterion(
            "reactivate_success_rate_m5_vs_m3",
            {"m5": m5_success, "m3": m3_success},
            "m5>m3",
            m5_success > m3_success,
        ),
        _criterion("provider_requests", provider_requests, 0, provider_requests == 0),
    ]
    return {
        "schema_version": "phase6_e2_gate_v1",
        "decision": "pass" if all(item["passed"] for item in criteria) else "fail",
        "criteria": criteria,
        "thresholds": E2_THRESHOLDS,
        "m5_episode_count": len(m5),
    }
