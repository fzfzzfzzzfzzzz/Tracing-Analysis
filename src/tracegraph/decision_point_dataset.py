"""Deterministic, leakage-resistant decision-point datasets and E0 gates.

The helpers in this module deliberately consume frozen artifacts only.  They
do not call a model and they never infer a missing evaluator or snapshot
capability as present.  This makes the output suitable for preregistered
eligibility checks and task-held-out omission-risk experiments.
"""

from __future__ import annotations

import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .graph import TraceGraph
from .schema import EdgeType, Node, NodeType, ValidityState
from .trajectory_artifacts import sha256_json


DATASET_SCHEMA_VERSION = "gdsc_decision_points_v1"
ELIGIBILITY_SCHEMA_VERSION = "gdsc_benchmark_eligibility_v1"

OBJECT_CLASSES = (
    "goal_subgoal",
    "policy_constraint_confirmation",
    "entity_state_slot",
    "evidence_large_observation",
    "superseded_conflicting_expired_state",
    "failure_negative_guard",
)

REPRESENTATIONS = (
    "raw_message",
    "structured_state_delta",
    "verified_summary",
    "negative_guard",
    "archive_handle",
    "omit",
)

DEFAULT_ELIGIBILITY_THRESHOLDS: dict[str, float | int] = {
    "minimum_tasks_per_domain": 10,
    "minimum_median_agent_actions": 10.0,
    "minimum_dynamic_provider_input_ratio": 0.40,
    "minimum_oracle_headroom": 0.30,
    "minimum_lifecycle_classes": 3,
    "minimum_decision_points_per_lifecycle_class": 30,
    "minimum_full_success_rate": 0.20,
    "maximum_full_success_rate": 0.85,
}


def _nearest_rank(values: Sequence[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = min(len(ordered), max(1, math.ceil(quantile * len(ordered))))
    return ordered[rank - 1]


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def _provider_input_tokens(metadata: Mapping[str, Any]) -> float | None:
    usage = metadata.get("provider_usage")
    if not isinstance(usage, Mapping):
        return None
    for key in ("prompt_tokens", "input_tokens", "input_token_count"):
        value = _as_number(usage.get(key))
        if value is not None:
            return value
    return None


def _is_verified(node: Node) -> bool:
    return bool(node.metadata.get("verified")) or (
        node.lifecycle_profile.validity == ValidityState.VALID
        and node.lifecycle_profile.confidence >= 0.8
    )


def _is_confirmation(node: Node) -> bool:
    text = str(node.content).lower()
    return bool(node.metadata.get("confirmation")) or any(
        marker in text
        for marker in ("confirm", "confirmation", "confirmed", "explicit user consent")
    )


def classify_candidate_object(node: Node) -> str:
    """Map a prefix object to exactly one preregistered construct class."""

    profile = node.lifecycle_profile
    if node.node_type == NodeType.ERROR or profile.validity == ValidityState.NEGATIVE_UNRESOLVED:
        return "failure_negative_guard"
    if (
        profile.validity == ValidityState.SUPERSEDED
        or node.lifecycle.value == "superseded"
        or bool(node.metadata.get("conflict") or node.metadata.get("expired"))
    ):
        return "superseded_conflicting_expired_state"
    if node.node_type == NodeType.CONSTRAINT or _is_confirmation(node):
        return "policy_constraint_confirmation"
    if node.node_type in {NodeType.GOAL, NodeType.SUBGOAL}:
        return "goal_subgoal"
    if node.node_type in {NodeType.OBSERVATION, NodeType.SUMMARY, NodeType.ARCHIVE_HANDLE}:
        if (
            node.token_count >= 128
            or node.node_type in {NodeType.SUMMARY, NodeType.ARCHIVE_HANDLE}
            or bool(node.metadata.get("evidence"))
        ):
            return "evidence_large_observation"
        return "entity_state_slot"
    if node.node_type in {NodeType.TOOL_CALL, NodeType.MCP_CALL}:
        return "entity_state_slot"
    return "entity_state_slot"


def candidate_representations(node: Node, object_class: str) -> tuple[str, ...]:
    """Return auditable representations without manufacturing verification."""

    result = ["raw_message"]
    if object_class in {
        "entity_state_slot",
        "evidence_large_observation",
        "superseded_conflicting_expired_state",
    }:
        result.append("structured_state_delta")
    if _is_verified(node):
        result.append("verified_summary")
    if object_class == "failure_negative_guard" and (
        bool(node.metadata.get("guard_source"))
        or bool(node.metadata.get("policy_predicate"))
        or _is_verified(node)
    ):
        result.append("negative_guard")
    if node.raw_ref:
        result.append("archive_handle")
    result.append("omit")
    return tuple(result)


def _source_ordinal(node: Node) -> int | None:
    value = node.metadata.get("source_message_ordinal")
    return int(value) if isinstance(value, int) and value > 0 else None


def _prefix_nodes(graph: TraceGraph, decision: Node) -> list[Node]:
    decision_ordinal = _source_ordinal(decision)
    result = []
    for node in graph.nodes.values():
        if node.node_id == decision.node_id:
            continue
        ordinal = _source_ordinal(node)
        if decision_ordinal is not None and ordinal is not None:
            if ordinal >= decision_ordinal:
                continue
        elif node.step_id >= decision.step_id:
            continue
        result.append(node)
    return sorted(result, key=lambda item: (item.step_id, _source_ordinal(item) or 0, item.node_id))


def _future_action(graph: TraceGraph, decision: Node) -> dict[str, Any]:
    calls = [
        graph.nodes[edge.target]
        for edge in graph.outgoing(decision.node_id, EdgeType.LEADS_TO)
        if edge.target in graph.nodes
    ]
    return {
        "decision_node_id": decision.node_id,
        "tool_names": sorted(
            str(call.metadata.get("tool_name") or "unknown_tool") for call in calls
        ),
        "side_effect": any(call.side_effect for call in calls),
        "tool_call_count": len(calls),
    }


def _stable_point_id(graph: TraceGraph, decision: Node) -> str:
    ordinal = _source_ordinal(decision)
    identity = {
        "session_id": graph.session_id,
        "decision_node_id": decision.node_id,
        "cutoff_step": decision.step_id,
        "source_message_ordinal": ordinal,
    }
    return f"dp_{sha256_json(identity)[:24]}"


@dataclass(frozen=True, slots=True)
class GraphRecord:
    graph: TraceGraph
    domain: str
    task_id: str
    trial: int | None = None
    source_path: str | None = None
    snapshot_replayable: bool = False
    native_evaluator_success: bool = False
    native_evaluator_side_effect: bool = False


def build_decision_point_dataset(records: Iterable[GraphRecord]) -> dict[str, Any]:
    """Build the E1 machine-construct package using prefix-only features.

    The actual next action is stored as an outcome label, never as an input
    feature.  Candidate objects are limited to events strictly before the
    decision ordinal/cutoff.
    """

    point_rows: list[dict[str, Any]] = []
    object_rows: list[dict[str, Any]] = []
    representation_rows: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    seen_sources: set[str] = set()
    for record in sorted(
        records,
        key=lambda item: (item.domain, item.task_id, item.trial or -1, item.graph.session_id),
    ):
        graph = record.graph
        if graph.session_id not in seen_sources:
            sources.append(
                {
                    "session_id": graph.session_id,
                    "domain": record.domain,
                    "task_id": record.task_id,
                    "trial": record.trial,
                    "source_path": record.source_path,
                    "event_graph_sha256": sha256_json(graph.to_dict()),
                }
            )
            seen_sources.add(graph.session_id)
        decisions = graph.find_nodes(node_types={NodeType.DECISION})
        for decision in decisions:
            prefix_nodes = _prefix_nodes(graph, decision)
            point_id = _stable_point_id(graph, decision)
            point_rows.append(
                {
                    "decision_point_id": point_id,
                    "session_id": graph.session_id,
                    "domain": record.domain,
                    "task_id": record.task_id,
                    "trial": record.trial,
                    "cutoff_step": decision.step_id,
                    "source_message_ordinal": _source_ordinal(decision),
                    "prefix_node_count": len(prefix_nodes),
                    "prefix_sha256": sha256_json([node.to_dict() for node in prefix_nodes]),
                    "outcome": _future_action(graph, decision),
                }
            )
            for node in prefix_nodes:
                object_class = classify_candidate_object(node)
                object_id = f"obj_{sha256_json({'point': point_id, 'node': node.node_id})[:24]}"
                obligations = sorted(item.value for item in node.lifecycle_profile.obligations)
                hard = bool(obligations) or node.node_type in {
                    NodeType.GOAL,
                    NodeType.SUBGOAL,
                    NodeType.CONSTRAINT,
                }
                object_rows.append(
                    {
                        "candidate_object_id": object_id,
                        "decision_point_id": point_id,
                        "domain": record.domain,
                        "task_id": record.task_id,
                        "trial": record.trial,
                        "source_node_ids": [node.node_id],
                        "object_class": object_class,
                        "node_type": node.node_type.value,
                        "lifecycle": node.lifecycle.value,
                        "validity": node.lifecycle_profile.validity.value,
                        "obligations": obligations,
                        "verified": _is_verified(node),
                        "hard": hard,
                        "raw_ref": node.raw_ref,
                        "estimated_raw_tokens": int(node.token_count),
                    }
                )
                for representation in candidate_representations(node, object_class):
                    representation_rows.append(
                        {
                            "row_id": f"repr_{sha256_json({'object': object_id, 'representation': representation})[:24]}",
                            "decision_point_id": point_id,
                            "candidate_object_id": object_id,
                            "domain": record.domain,
                            "task_id": record.task_id,
                            "trial": record.trial,
                            "representation": representation,
                            "source_ids": [node.node_id],
                            "hard": hard,
                            "verified": _is_verified(node),
                            "requires_human_label": True,
                        }
                    )

    payload: dict[str, Any] = {
        "schema_version": DATASET_SCHEMA_VERSION,
        "construction": "prefix_only_deterministic_v1",
        "counterfactual_outcomes_claimed": False,
        "sources": sources,
        "decision_points": point_rows,
        "candidate_objects": object_rows,
        "representation_rows": representation_rows,
    }
    payload["dataset_sha256"] = sha256_json(payload)
    return payload


def stable_task_split(
    rows: Sequence[Mapping[str, Any]],
    *,
    train_fraction: float = 0.6,
    validation_fraction: float = 0.2,
) -> dict[str, list[dict[str, Any]]]:
    """Split by domain/task hash so adjacent decision points never leak."""

    if not 0 < train_fraction < 1 or not 0 <= validation_fraction < 1:
        raise ValueError("split fractions are outside [0, 1]")
    if train_fraction + validation_fraction >= 1:
        raise ValueError("train_fraction + validation_fraction must be below 1")
    result: dict[str, list[dict[str, Any]]] = {"train": [], "validation": [], "test": []}
    task_to_split: dict[tuple[str, str], str] = {}
    for row in rows:
        key = (str(row.get("domain") or ""), str(row.get("task_id") or ""))
        if not all(key):
            raise ValueError("every risk row requires domain and task_id")
        if key not in task_to_split:
            unit = int(sha256_json(key)[:12], 16) / float(16**12)
            if unit < train_fraction:
                task_to_split[key] = "train"
            elif unit < train_fraction + validation_fraction:
                task_to_split[key] = "validation"
            else:
                task_to_split[key] = "test"
        result[task_to_split[key]].append(dict(row))
    return result


def _domain_task_from_path(path: Path, graph: TraceGraph) -> tuple[str, str, int | None]:
    metadata = graph.metadata
    domain = str(metadata.get("domain") or "")
    task_id = str(metadata.get("task_id") or "")
    trial_value = metadata.get("trial")
    trial = int(trial_value) if isinstance(trial_value, int) else None
    text = path.as_posix()
    match = re.search(r"(?:^|[_/\\-])(retail|airline)[_-]([^_/\\-]+)", text, re.I)
    if match:
        domain = domain or match.group(1).lower()
        if not task_id or re.fullmatch(r"[0-9a-f]{24,}", task_id):
            task_id = match.group(2)
    return domain or "unknown", task_id or graph.session_id, trial


def discover_graph_records(root: Path | str) -> list[GraphRecord]:
    """Load trace graphs while retaining path-derived domain/task provenance."""

    source = Path(root)
    paths = [source] if source.is_file() else sorted(source.rglob("trace.json"))
    if source.is_dir() and not paths:
        paths = sorted(path for path in source.rglob("*.json") if path.name != "generation.json")
    records: list[GraphRecord] = []
    for path in paths:
        try:
            graph = TraceGraph.load(path)
        except (KeyError, TypeError, ValueError):
            continue
        domain, task_id, trial = _domain_task_from_path(path, graph)
        metadata = graph.metadata
        records.append(
            GraphRecord(
                graph=graph,
                domain=domain,
                task_id=task_id,
                trial=trial,
                source_path=path.as_posix(),
                snapshot_replayable=bool(
                    metadata.get("snapshot_replayable")
                    or metadata.get("snapshot_restore_verified")
                ),
                native_evaluator_success=bool(
                    metadata.get("native_evaluator_success_available")
                    or metadata.get("native_evaluator_verified")
                ),
                native_evaluator_side_effect=bool(
                    metadata.get("native_evaluator_side_effect_available")
                    or metadata.get("collateral_evaluator_verified")
                ),
            )
        )
    return records


def _record_metrics(record: GraphRecord) -> dict[str, Any]:
    graph = record.graph
    decisions = graph.find_nodes(node_types={NodeType.DECISION})
    tool_calls = graph.find_nodes(node_types={NodeType.TOOL_CALL, NodeType.MCP_CALL})
    prompt_inputs = [
        value
        for value in (_provider_input_tokens(node.metadata) for node in decisions)
        if value is not None
    ]
    provider_input = sum(prompt_inputs) if prompt_inputs and len(prompt_inputs) == len(decisions) else None
    dynamic_ratio = _as_number(graph.metadata.get("dynamic_provider_input_ratio"))
    if dynamic_ratio is None and provider_input:
        dynamic_inputs = [
            _as_number((node.metadata.get("provider_usage") or {}).get("dynamic_input_tokens"))
            for node in decisions
        ]
        if dynamic_inputs and all(value is not None for value in dynamic_inputs):
            dynamic_ratio = sum(dynamic_inputs) / provider_input
    oracle_headroom = _as_number(
        graph.metadata.get(
            "provider_token_oracle_headroom",
            graph.metadata.get("oracle_provider_headroom"),
        )
    )
    phenomena: Counter[str] = Counter()
    for node in graph.nodes.values():
        if node.lifecycle_profile.validity == ValidityState.SUPERSEDED:
            phenomena["supersession"] += 1
        if node.metadata.get("conflict"):
            phenomena["conflict"] += 1
        if node.metadata.get("open_slot") or node.metadata.get("slot"):
            phenomena["open_slot"] += 1
        if node.node_type == NodeType.CONSTRAINT:
            phenomena["policy_scope"] += 1
        if node.side_effect:
            phenomena["side_effect"] += 1
        if node.node_type in {NodeType.OBSERVATION, NodeType.ERROR} and any(
            edge.edge_type in {EdgeType.SUPERSEDED_BY, EdgeType.RESOLVED_BY}
            for edge in graph.outgoing(node.node_id)
        ):
            phenomena["state_update"] += 1

    success_value = graph.metadata.get("task_success", graph.metadata.get("reward"))
    success_number = _as_number(success_value)
    return {
        "session_id": graph.session_id,
        "domain": record.domain,
        "task_id": record.task_id,
        "agent_actions": len(decisions),
        "tool_calls": len(tool_calls),
        "provider_input_tokens": provider_input,
        "dynamic_provider_input_ratio": dynamic_ratio,
        "oracle_headroom": oracle_headroom,
        "lifecycle_decision_points": dict(phenomena),
        "snapshot_replayable": record.snapshot_replayable,
        "native_evaluator_success": record.native_evaluator_success,
        "native_evaluator_side_effect": record.native_evaluator_side_effect,
        "success": None if success_number is None else bool(abs(success_number - 1.0) <= 1e-6),
        "source_path": record.source_path,
    }


def evaluate_benchmark_eligibility(
    records: Iterable[GraphRecord],
    *,
    thresholds: Mapping[str, float | int] | None = None,
) -> dict[str, Any]:
    """Evaluate E0 per domain and fail closed on every missing measurement."""

    limits = dict(DEFAULT_ELIGIBILITY_THRESHOLDS)
    if thresholds:
        unknown = set(thresholds) - set(limits)
        if unknown:
            raise ValueError(f"unknown eligibility thresholds: {sorted(unknown)}")
        limits.update(thresholds)
    rows = [_record_metrics(record) for record in records]
    by_domain: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_domain[row["domain"]].append(row)
    domains: dict[str, Any] = {}
    all_pass = bool(by_domain)
    for domain, domain_rows in sorted(by_domain.items()):
        tasks = sorted({str(row["task_id"]) for row in domain_rows})
        actions = [float(row["agent_actions"]) for row in domain_rows]
        dynamics = [row["dynamic_provider_input_ratio"] for row in domain_rows]
        headrooms = [row["oracle_headroom"] for row in domain_rows]
        success = [row["success"] for row in domain_rows]
        phenomenon_counts: Counter[str] = Counter()
        for row in domain_rows:
            phenomenon_counts.update(row["lifecycle_decision_points"])
        qualifying_classes = sorted(
            name
            for name, count in phenomenon_counts.items()
            if count >= int(limits["minimum_decision_points_per_lifecycle_class"])
        )
        complete_dynamic = bool(dynamics) and all(value is not None for value in dynamics)
        complete_headroom = bool(headrooms) and all(value is not None for value in headrooms)
        complete_success = bool(success) and all(value is not None for value in success)
        median_actions = statistics.median(actions) if actions else None
        median_dynamic = statistics.median(dynamics) if complete_dynamic else None
        median_headroom = statistics.median(headrooms) if complete_headroom else None
        success_rate = (
            statistics.fmean(float(value) for value in success)
            if complete_success
            else None
        )
        checks = {
            "task_count": len(tasks) >= int(limits["minimum_tasks_per_domain"]),
            "median_agent_actions": (
                median_actions is not None
                and median_actions >= float(limits["minimum_median_agent_actions"])
            ),
            "dynamic_provider_input": (
                median_dynamic is not None
                and median_dynamic >= float(limits["minimum_dynamic_provider_input_ratio"])
            ),
            "oracle_headroom": (
                median_headroom is not None
                and median_headroom >= float(limits["minimum_oracle_headroom"])
            ),
            "lifecycle_phenomena": len(qualifying_classes)
            >= int(limits["minimum_lifecycle_classes"]),
            "snapshot_replay": all(row["snapshot_replayable"] for row in domain_rows),
            "success_not_floor_or_ceiling": (
                success_rate is not None
                and float(limits["minimum_full_success_rate"])
                <= success_rate
                <= float(limits["maximum_full_success_rate"])
            ),
            "native_evaluator": all(
                row["native_evaluator_success"] and row["native_evaluator_side_effect"]
                for row in domain_rows
            ),
        }
        eligible = all(checks.values())
        all_pass = all_pass and eligible
        missing = []
        if not complete_dynamic:
            missing.append("dynamic_provider_input_ratio")
        if not complete_headroom:
            missing.append("provider_token_oracle_headroom")
        if not complete_success:
            missing.append("native_success_outcomes")
        domains[domain] = {
            "eligible": eligible,
            "checks": checks,
            "missing_measurements": missing,
            "sessions": len(domain_rows),
            "tasks": len(tasks),
            "task_ids": tasks,
            "median_agent_actions": median_actions,
            "p75_agent_actions": _nearest_rank(actions, 0.75),
            "median_dynamic_provider_input_ratio": median_dynamic,
            "median_oracle_headroom": median_headroom,
            "full_context_success_rate": success_rate,
            "lifecycle_decision_points": dict(sorted(phenomenon_counts.items())),
            "qualifying_lifecycle_classes": qualifying_classes,
        }
    result: dict[str, Any] = {
        "schema_version": ELIGIBILITY_SCHEMA_VERSION,
        "eligible": all_pass,
        "decision": "proceed_to_r3" if all_pass else "stop_before_r3",
        "fail_closed": True,
        "thresholds": limits,
        "domains": domains,
        "sessions": rows,
    }
    result["report_sha256"] = sha256_json(result)
    return result
