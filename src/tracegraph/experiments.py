"""Offline lifecycle, oracle, online-replay, baseline, and ablation experiments."""

from __future__ import annotations

import csv
import json
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .archive import ArchiveStore
from .context import ContextItem, ContextManager, ContextView, build_context_managers
from .graph import TraceGraph
from .lifecycle import LifecycleEngine
from .metrics import EvaluationMetrics, evaluate_view
from .schema import Edge, LifecycleState, Node, NodeType, utc_now


@dataclass(slots=True)
class ExperimentConfig:
    budget: int | None = 2048
    manager_names: list[str] = field(default_factory=list)
    online_replay: bool = True
    last_k: int = 8
    provenance: str = "unknown"


class OracleUpperBoundManager(ContextManager):
    """Post-hoc structural oracle that retains every hard-protected node."""

    name = "oracle_structural_upper_bound"

    def select(self, graph: TraceGraph, *, budget: int | None = None) -> ContextView:
        engine = LifecycleEngine()
        engine.apply(graph)
        items = [
            ContextItem.from_node(node, "oracle_hard_constraint")
            for node in graph.find_nodes()
            if not engine.safety_decision(graph, node.node_id).removable
        ]
        return self._view(
            graph,
            items,
            budget=budget,
            metadata={
                "post_hoc_oracle": True,
                "budget_ignored_for_hard_constraints": budget is not None,
            },
        )


def discover_graphs(path: str | Path) -> list[TraceGraph]:
    source = Path(path)
    paths = [source] if source.is_file() else sorted(source.glob("*.json"))
    graphs: list[TraceGraph] = []
    for candidate in paths:
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or "schema_version" not in payload:
            continue
        graph = TraceGraph.from_dict(payload)
        errors = graph.validate()
        if errors:
            raise ValueError(f"invalid graph {candidate}: {errors}")
        graphs.append(graph)
    if not graphs:
        raise ValueError(f"no TraceGraph JSON files found at {source}")
    return graphs


def prefix_graph(graph: TraceGraph, max_step: int) -> TraceGraph:
    prefix = TraceGraph(
        session_id=graph.session_id,
        metadata={**graph.metadata, "replay_max_step": max_step},
    )
    for node in graph.find_nodes():
        if node.step_id <= max_step:
            prefix.add_node(Node.from_dict(node.to_dict()))
    for edge in graph.edges.values():
        if edge.source in prefix.nodes and edge.target in prefix.nodes:
            prefix.add_edge(Edge.from_dict(edge.to_dict()))
    return prefix


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_manager: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_manager[row["manager"]].append(row)
    aggregates: dict[str, Any] = {}
    excluded = {"session_id", "manager", "synthetic", "source"}
    for manager, manager_rows in sorted(by_manager.items()):
        numeric_keys = sorted(
            {
                key
                for row in manager_rows
                for key, value in row.items()
                if key not in excluded and isinstance(value, (int, float)) and not isinstance(value, bool)
            }
        )
        metrics: dict[str, Any] = {"n": len(manager_rows)}
        for key in numeric_keys:
            values = [float(row[key]) for row in manager_rows if isinstance(row.get(key), (int, float))]
            metrics[f"mean_{key}"] = _mean(values)
            metrics[f"std_{key}"] = statistics.stdev(values) if len(values) > 1 else 0.0
        aggregates[manager] = metrics
    return aggregates


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


class ExperimentRunner:
    def __init__(
        self,
        config: ExperimentConfig,
        *,
        archive: ArchiveStore | None = None,
    ) -> None:
        self.config = config
        self.archive = archive
        self.managers = build_context_managers(last_k=config.last_k)
        if config.manager_names:
            missing = sorted(set(config.manager_names) - set(self.managers))
            if missing:
                raise ValueError(f"unknown context managers: {missing}")
            self.managers = {
                name: self.managers[name]
                for name in config.manager_names
            }

    @staticmethod
    def _clone(graph: TraceGraph) -> TraceGraph:
        return TraceGraph.from_dict(graph.to_dict())

    def _row(
        self,
        graph: TraceGraph,
        manager: ContextManager,
    ) -> tuple[dict[str, Any], ContextView]:
        working = self._clone(graph)
        started = time.perf_counter()
        view = manager.select(working, budget=self.config.budget)
        elapsed_ms = (time.perf_counter() - started) * 1000
        evaluated_manager = working.metadata.get("evaluated_context_manager")
        condition_was_executed = manager.name == evaluated_manager or (
            evaluated_manager is None and manager.name == "full_trajectory"
        )
        metrics = evaluate_view(
            working,
            view,
            archive=self.archive,
            task_success=(
                working.metadata.get("task_success") if condition_was_executed else None
            ),
            policy_violation=(
                working.metadata.get("policy_violation") if condition_was_executed else None
            ),
            manager_overhead_ms=elapsed_ms,
        )
        row = {
            "session_id": working.session_id,
            "manager": manager.name,
            "source": working.metadata.get("source"),
            "synthetic": bool(working.metadata.get("synthetic", False)),
            **metrics.to_dict(),
        }
        return row, view

    def run(self, graphs: list[TraceGraph], output_dir: str | Path) -> dict[str, Any]:
        if not graphs:
            raise ValueError("at least one graph is required")
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        rows: list[dict[str, Any]] = []
        views: list[dict[str, Any]] = []
        for graph in graphs:
            for manager in self.managers.values():
                row, view = self._row(graph, manager)
                rows.append(row)
                views.append(
                    {
                        "session_id": graph.session_id,
                        "manager": manager.name,
                        "view": view.to_dict(),
                    }
                )

        state_counts: Counter[str] = Counter()
        transition_counts: Counter[str] = Counter()
        for graph in graphs:
            state_counts.update(node.lifecycle.value for node in graph.nodes.values())
            transitions = graph.metadata.get("lifecycle_transitions", {})
            if isinstance(transitions, dict):
                for value in transitions.values():
                    if isinstance(value, list) and len(value) == 2:
                        transition_counts[f"{value[0]}->{value[1]}"] += 1
        lifecycle_analysis = {
            "experiment": "offline_lifecycle_analysis",
            "state_counts": dict(sorted(state_counts.items())),
            "transition_counts": dict(sorted(transition_counts.items())),
            "sessions": len(graphs),
        }

        oracle_rows: list[dict[str, Any]] = []
        oracle_manager = OracleUpperBoundManager()
        for graph in graphs:
            row, _ = self._row(graph, oracle_manager)
            oracle_rows.append(row)

        replay_rows: list[dict[str, Any]] = []
        if self.config.online_replay:
            for graph in graphs:
                steps = sorted({node.step_id for node in graph.nodes.values()})
                for step in steps:
                    prefix = prefix_graph(graph, step)
                    for manager in self.managers.values():
                        row, _ = self._row(prefix, manager)
                        row["step_id"] = step
                        row["replay"] = True
                        replay_rows.append(row)

        aggregate = _aggregate(rows)
        oracle_aggregate = _aggregate(oracle_rows)
        manifest = {
            "schema_version": "1.0",
            "generated_at": utc_now(),
            "provenance": self.config.provenance,
            "graph_count": len(graphs),
            "manager_names": list(self.managers),
            "budget": self.config.budget,
            "online_replay": self.config.online_replay,
            "contains_synthetic_data": any(
                bool(graph.metadata.get("synthetic", False)) for graph in graphs
            ),
            "interpretation_warning": (
                "Synthetic outputs validate the pipeline only and are not benchmark evidence."
                if any(bool(graph.metadata.get("synthetic", False)) for graph in graphs)
                else "Offline views do not provide counterfactual task success; only actually executed conditions carry reward/policy outcomes."
            ),
            "files": [
                "per_session.jsonl",
                "per_session.csv",
                "aggregate.json",
                "context_views.jsonl",
                "lifecycle_analysis.json",
                "oracle_upper_bound.json",
                *( ["online_replay.jsonl"] if self.config.online_replay else [] ),
            ],
        }
        _write_json(output / "manifest.json", manifest)
        _write_jsonl(output / "per_session.jsonl", rows)
        _write_csv(output / "per_session.csv", rows)
        _write_json(output / "aggregate.json", aggregate)
        _write_jsonl(output / "context_views.jsonl", views)
        _write_json(output / "lifecycle_analysis.json", lifecycle_analysis)
        _write_json(
            output / "oracle_upper_bound.json",
            {"per_session": oracle_rows, "aggregate": oracle_aggregate},
        )
        if self.config.online_replay:
            _write_jsonl(output / "online_replay.jsonl", replay_rows)
        return manifest
