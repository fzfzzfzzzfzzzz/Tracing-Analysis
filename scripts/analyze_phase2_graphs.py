"""Summarize schema-v2 lifecycle signals in reimported experiment graphs."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from tracegraph.context import GraphLifecycleManager, NoFailureRetentionManager
from tracegraph.graph import TraceGraph
from tracegraph.schema import (
    EdgeType,
    NodeType,
    RelevanceState,
    SemanticOutcome,
    ValidityState,
)


NEGATIVE_OUTCOMES = {
    SemanticOutcome.NEGATIVE.value,
    SemanticOutcome.POLICY_DENIED.value,
    SemanticOutcome.TEST_FAILED.value,
}
FORWARD_RELATIONS = {
    EdgeType.PROVIDES_INPUT,
    EdgeType.RETRIED_BY,
    EdgeType.RESOLVED_BY,
    EdgeType.SUPERSEDED_BY,
    EdgeType.SUMMARIZED_BY,
}


def _session_lookup(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    report = json.loads(path.read_text(encoding="utf-8"))
    return {
        str(row.get("simulation_id")): {
            "manager": str(row.get("manager") or "unknown"),
            "domain": str(row.get("domain") or "unknown"),
            "task_id": str(row.get("task_id") or "unknown"),
            "trial": int(row.get("trial") or 0),
            "task_success": bool(row.get("task_success")),
        }
        for row in report.get("sessions", [])
        if isinstance(row, dict) and row.get("simulation_id")
    }


def _counter_template() -> Counter[str]:
    return Counter(
        {
            "graphs": 0,
            "valid_graphs": 0,
            "graph_validation_errors": 0,
            "nodes": 0,
            "edges": 0,
            "error_nodes": 0,
            "negative_observation_nodes": 0,
            "unresolved_negative_nodes": 0,
            "resolved_negative_nodes": 0,
            "retry_edges": 0,
            "exact_retry_edges": 0,
            "structural_retry_edges": 0,
            "argument_completion_retry_edges": 0,
            "resolve_edges": 0,
            "supersede_edges": 0,
            "canonical_forward_edges": 0,
            "temporally_reversed_forward_edges": 0,
            "consumed_nodes_with_obligations": 0,
            "sessions_with_negative_observations": 0,
            "sessions_with_retries": 0,
            "sessions_with_resolutions": 0,
            "sessions_where_failure_retention_changes_selection": 0,
            "full_ours_selected_negative_nodes": 0,
            "no_failure_selected_negative_nodes": 0,
        }
    )


def _update(counter: Counter[str], graph: TraceGraph, *, budget: int) -> None:
    errors = graph.validate()
    counter["graphs"] += 1
    counter["valid_graphs"] += not errors
    counter["graph_validation_errors"] += len(errors)
    counter["nodes"] += len(graph.nodes)
    counter["edges"] += len(graph.edges)

    negative_observations = {
        node.node_id
        for node in graph.nodes.values()
        if node.node_type == NodeType.OBSERVATION
        and node.metadata.get("semantic_outcome") in NEGATIVE_OUTCOMES
    }
    negative_nodes = negative_observations | {
        node.node_id
        for node in graph.nodes.values()
        if node.node_type == NodeType.ERROR
    }
    unresolved = {
        node.node_id
        for node in graph.nodes.values()
        if node.lifecycle_profile.validity == ValidityState.NEGATIVE_UNRESOLVED
    }
    resolved = {
        node.node_id
        for node in graph.nodes.values()
        if node.lifecycle_profile.validity == ValidityState.NEGATIVE_RESOLVED
    }
    retries = [
        edge
        for edge in graph.edges.values()
        if edge.edge_type in {EdgeType.RETRIED_BY, EdgeType.RETRIES}
    ]
    resolutions = [
        edge
        for edge in graph.edges.values()
        if edge.edge_type in {EdgeType.RESOLVED_BY, EdgeType.RESOLVES}
    ]
    supersessions = [
        edge
        for edge in graph.edges.values()
        if edge.edge_type in {EdgeType.SUPERSEDED_BY, EdgeType.SUPERSEDES}
    ]
    forward = [
        edge for edge in graph.edges.values() if edge.edge_type in FORWARD_RELATIONS
    ]

    counter["error_nodes"] += sum(
        node.node_type == NodeType.ERROR for node in graph.nodes.values()
    )
    counter["negative_observation_nodes"] += len(negative_observations)
    counter["unresolved_negative_nodes"] += len(unresolved)
    counter["resolved_negative_nodes"] += len(resolved)
    counter["retry_edges"] += len(retries)
    counter["exact_retry_edges"] += sum(
        edge.metadata.get("match_type") == "exact_signature" for edge in retries
    )
    counter["structural_retry_edges"] += sum(
        edge.metadata.get("match_type") == "structural_operation" for edge in retries
    )
    counter["argument_completion_retry_edges"] += sum(
        edge.metadata.get("match_type") == "argument_completion" for edge in retries
    )
    counter["resolve_edges"] += len(resolutions)
    counter["supersede_edges"] += len(supersessions)
    counter["canonical_forward_edges"] += len(forward)
    counter["temporally_reversed_forward_edges"] += sum(
        graph.nodes[edge.source].step_id > graph.nodes[edge.target].step_id
        for edge in forward
    )
    counter["consumed_nodes_with_obligations"] += sum(
        node.lifecycle_profile.relevance == RelevanceState.CONSUMED
        and bool(node.lifecycle_profile.obligations)
        for node in graph.nodes.values()
    )
    counter["sessions_with_negative_observations"] += bool(negative_observations)
    counter["sessions_with_retries"] += bool(retries)
    counter["sessions_with_resolutions"] += bool(resolutions)

    full_graph = TraceGraph.from_dict(graph.to_dict())
    ablated_graph = TraceGraph.from_dict(graph.to_dict())
    full_view = GraphLifecycleManager().select(full_graph, budget=budget)
    ablated_view = NoFailureRetentionManager().select(ablated_graph, budget=budget)
    full_selected = {item.node_id for item in full_view.items} & negative_nodes
    ablated_selected = {item.node_id for item in ablated_view.items} & negative_nodes
    counter["full_ours_selected_negative_nodes"] += len(full_selected)
    counter["no_failure_selected_negative_nodes"] += len(ablated_selected)
    counter["sessions_where_failure_retention_changes_selection"] += (
        full_selected != ablated_selected
    )


def analyze(
    input_dir: Path,
    *,
    matrix_report: Path | None = None,
    budget: int = 4096,
) -> dict[str, Any]:
    paths = sorted(input_dir.glob("*.json"))
    if not paths:
        raise ValueError(f"no graph JSON files found in {input_dir}")
    sessions = _session_lookup(matrix_report)
    overall = _counter_template()
    by_manager: defaultdict[str, Counter[str]] = defaultdict(_counter_template)
    semantic_outcomes: Counter[str] = Counter()
    validity_states: Counter[str] = Counter()
    obligations: Counter[str] = Counter()
    signal_sessions: list[dict[str, Any]] = []

    for path in paths:
        graph = TraceGraph.load(path)
        simulation_id = str(graph.metadata.get("simulation_id") or "")
        session = sessions.get(simulation_id, {})
        manager = str(session.get("manager", "unknown"))
        _update(overall, graph, budget=budget)
        _update(by_manager[manager], graph, budget=budget)
        semantic_outcomes.update(
            [
                str(node.metadata["semantic_outcome"])
                for node in graph.nodes.values()
                if node.metadata.get("semantic_outcome") is not None
            ]
        )
        validity_states.update(
            node.lifecycle_profile.validity.value for node in graph.nodes.values()
        )
        obligations.update(
            obligation.value
            for node in graph.nodes.values()
            for obligation in node.lifecycle_profile.obligations
        )
        retry_edges = [
            edge
            for edge in graph.edges.values()
            if edge.edge_type in {EdgeType.RETRIED_BY, EdgeType.RETRIES}
        ]
        resolve_edges = [
            edge
            for edge in graph.edges.values()
            if edge.edge_type in {EdgeType.RESOLVED_BY, EdgeType.RESOLVES}
        ]
        if retry_edges or resolve_edges:
            signal_sessions.append(
                {
                    "simulation_id": simulation_id,
                    **session,
                    "error_nodes": sum(
                        node.node_type == NodeType.ERROR
                        for node in graph.nodes.values()
                    ),
                    "retry_edges": len(retry_edges),
                    "exact_retry_edges": sum(
                        edge.metadata.get("match_type") == "exact_signature"
                        for edge in retry_edges
                    ),
                    "structural_retry_edges": sum(
                        edge.metadata.get("match_type") == "structural_operation"
                        for edge in retry_edges
                    ),
                    "argument_completion_retry_edges": sum(
                        edge.metadata.get("match_type") == "argument_completion"
                        for edge in retry_edges
                    ),
                    "resolve_edges": len(resolve_edges),
                }
            )

    return {
        "schema_version": "1.0",
        "graph_schema_version": TraceGraph.schema_version,
        "input_dir": str(input_dir),
        "matrix_report": str(matrix_report) if matrix_report else None,
        "context_budget": budget,
        "overall": dict(overall),
        "by_manager": {
            manager: dict(counter) for manager, counter in sorted(by_manager.items())
        },
        "semantic_outcome_counts": dict(sorted(semantic_outcomes.items())),
        "profile_validity_counts": dict(sorted(validity_states.items())),
        "profile_obligation_counts": dict(sorted(obligations.items())),
        "signal_sessions": sorted(
            signal_sessions,
            key=lambda row: (
                -int(row["resolve_edges"]),
                -int(row["retry_edges"]),
                str(row.get("domain", "")),
                str(row.get("task_id", "")),
            ),
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--matrix-report", type=Path)
    parser.add_argument("--budget", type=int, default=4096)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    report = analyze(
        args.input,
        matrix_report=args.matrix_report,
        budget=args.budget,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report["overall"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
