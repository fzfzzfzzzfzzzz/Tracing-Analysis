"""Run the preregistered E1/E4 GDSC mechanism audit without provider calls."""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from tracegraph.compiler import CompilerConfig, compile as compile_decision_state
from tracegraph.decision_query import build_decision_query
from tracegraph.graph import TraceGraph
from tracegraph.policy_rules import compile_policy_rule
from tracegraph.provider_cost import ProviderProtocol, serialized_request_cost
from tracegraph.representation_verifiers import verify_structured_equivalence
from tracegraph.representations import RepresentationType, generate_representations
from tracegraph.schema import Node, NodeType
from tracegraph.state_reducer import reduce_event_graph
from tracegraph.trajectory_artifacts import sha256_json


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


ABLATIONS = (
    "no_graph",
    "no_lifecycle",
    "keep_drop_only",
    "node_cost_only",
    "no_policy_checker",
    "no_negative_guard",
)


def _ordinal(node: Node) -> int | None:
    value = node.metadata.get("source_message_ordinal")
    return int(value) if isinstance(value, int) and value > 0 else None


def _prefix_graph(graph: TraceGraph, decision: Node) -> TraceGraph:
    """Physically truncate a graph at the strict pre-decision prefix."""

    decision_ordinal = _ordinal(decision)
    selected: list[Node] = []
    for node in graph.nodes.values():
        if node.node_id == decision.node_id:
            continue
        ordinal = _ordinal(node)
        if decision_ordinal is not None and ordinal is not None:
            if ordinal >= decision_ordinal:
                continue
        elif node.step_id >= decision.step_id:
            continue
        selected.append(node)
    prefix = TraceGraph(
        session_id=graph.session_id,
        metadata={
            "source_session_id": graph.session_id,
            "prefix_decision_node_id": decision.node_id,
            "prefix_only": True,
        },
    )
    for node in sorted(selected, key=lambda item: (item.step_id, item.node_id)):
        prefix.add_node(node)
    for edge in sorted(graph.edges.values(), key=lambda item: item.edge_id):
        if edge.source in prefix.nodes and edge.target in prefix.nodes:
            prefix.add_edge(edge, validate_signature=False)
    return prefix


def _text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )


def _prefix_messages(prefix: TraceGraph) -> tuple[dict[str, Any], ...]:
    grouped: defaultdict[int, list[Node]] = defaultdict(list)
    for node in prefix.nodes.values():
        ordinal = _ordinal(node)
        if ordinal is not None:
            grouped[ordinal].append(node)
    messages: list[dict[str, Any]] = []
    for ordinal, nodes in sorted(grouped.items()):
        ordered = sorted(nodes, key=lambda item: (item.node_type.value, item.node_id))
        results = [
            node for node in ordered if node.node_type in {NodeType.OBSERVATION, NodeType.ERROR}
        ]
        if results:
            for node in results:
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": str(node.metadata.get("call_id") or node.node_id),
                        "content": _text(node.content),
                    }
                )
            continue
        user_nodes = [
            node
            for node in ordered
            if node.metadata.get("source") == "user_message"
            or node.node_type in {NodeType.GOAL, NodeType.SUBGOAL}
        ]
        calls = [
            node for node in ordered if node.node_type in {NodeType.TOOL_CALL, NodeType.MCP_CALL}
        ]
        decisions = [node for node in ordered if node.node_type == NodeType.DECISION]
        if user_nodes and not calls and not decisions:
            messages.append({"role": "user", "content": _text(user_nodes[-1].content)})
            continue
        message: dict[str, Any] = {
            "role": "assistant",
            "content": _text(decisions[-1].content) if decisions else "",
        }
        if calls:
            message["tool_calls"] = [
                {
                    "id": str(call.metadata.get("call_id") or call.node_id),
                    "type": "function",
                    "function": {
                        "name": str(
                            call.metadata.get("tool_name")
                            or (call.content.get("tool_name") if isinstance(call.content, Mapping) else "")
                        ),
                        "arguments": _text(
                            call.content.get("arguments", {})
                            if isinstance(call.content, Mapping)
                            else {}
                        ),
                    },
                }
                for call in calls
            ]
        messages.append(message)
    return tuple(messages)


def _domain_tool_schemas(domain: str) -> tuple[dict[str, Any], ...]:
    if domain == "retail":
        from tau2.domains.retail.environment import get_environment
    elif domain == "airline":
        from tau2.domains.airline.environment import get_environment
    else:
        raise ValueError(f"unsupported tau3 domain: {domain}")
    environment = get_environment()
    return tuple(
        tool.openai_schema
        for tool in sorted(environment.get_tools(), key=lambda item: item.name)
    )


def _policy_text(prefix: TraceGraph) -> tuple[str, ...]:
    return tuple(
        str(node.content)
        for node in prefix.find_nodes(node_types={NodeType.CONSTRAINT})
    )


def _coverage(state, bundle) -> bool:
    required = {atom.atom_id for atom in state.atoms if atom.hard}
    covered = {
        atom_id
        for item in bundle.representation_manifest
        if item.get("representation_type") != RepresentationType.OMIT.value
        for atom_id in item.get("covered_atoms", ())
    }
    return required.issubset(covered)


def _median(rows: Sequence[Mapping[str, Any]], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return statistics.median(values) if values else None


def _rate(rows: Sequence[Mapping[str, Any]], key: str) -> float:
    return statistics.fmean(float(bool(row.get(key))) for row in rows) if rows else 0.0


def evaluate(
    dataset: Mapping[str, Any],
    *,
    budgets: Sequence[int],
    hard_context_limit: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    sources = {
        str(item["session_id"]): item
        for item in dataset.get("sources", ())
        if isinstance(item, Mapping)
    }
    records: dict[str, TraceGraph] = {
        session_id: TraceGraph.load(str(source["source_path"]))
        for session_id, source in sources.items()
    }
    schemas_by_domain = {
        domain: _domain_tool_schemas(domain)
        for domain in sorted({str(item["domain"]) for item in sources.values()})
    }
    prepared: list[dict[str, Any]] = []
    structured_total = 0
    structured_equivalent = 0
    sufficiency: list[bool] = []
    for point in dataset.get("decision_points", ()):
        graph = records[str(point["session_id"])]
        decision = graph.nodes[str(point["outcome"]["decision_node_id"])]
        prefix = _prefix_graph(graph, decision)
        messages = _prefix_messages(prefix)
        domain = str(point["domain"])
        schemas = schemas_by_domain[domain]
        rules = tuple(
            compile_policy_rule(node.content, source_event_ids=(node.node_id,))
            for node in prefix.find_nodes(node_types={NodeType.CONSTRAINT})
        )
        state = reduce_event_graph(prefix, tool_schemas=schemas, policy_rules=rules)
        query = build_decision_query(state, tool_schemas=schemas, policy_rules=rules)
        protocol = ProviderProtocol(
            model="zai/glm-4.7-flash",
            base_messages=messages,
            tools=schemas,
            hard_context_limit=hard_context_limit,
        )
        raw_protocol = ProviderProtocol(
            model="zai/glm-4.7-flash",
            system_rules=_policy_text(prefix),
            base_messages=messages,
            tools=schemas,
            hard_context_limit=hard_context_limit,
        )
        raw_messages = tuple(
            [{"role": "system", "content": "\n\n".join(raw_protocol.system_rules)}]
            + list(messages)
        )
        raw_tokens = serialized_request_cost(raw_protocol, raw_messages)
        atom_map = state.atom_map()
        for atom in state.atoms:
            for candidate in generate_representations(
                atom,
                allow_omit=False,
                omission_risk=1.0,
                omission_reason="offline_equivalence_audit",
            ):
                if candidate.representation_type == RepresentationType.STRUCTURED_STATE_DELTA:
                    structured_total += 1
                    structured_equivalent += int(
                        verify_structured_equivalence(candidate, atom_map[atom.atom_id]).ok
                    )
        expected_tools = set(map(str, point["outcome"].get("tool_names", ())))
        sufficient = expected_tools.issubset(set(query.candidate_tools))
        sufficiency.append(sufficient)
        prepared.append(
            {
                "point": point,
                "prefix": prefix,
                "state": state,
                "query": query,
                "protocol": protocol,
                "raw_tokens": raw_tokens,
                "sufficient": sufficient,
            }
        )

    budget_rows: list[dict[str, Any]] = []
    for item in prepared:
        for budget in budgets:
            bundle = compile_decision_state(
                item["prefix"], item["state"], item["query"], item["protocol"], budget
            )
            raw_tokens = int(item["raw_tokens"])
            budget_rows.append(
                {
                    "decision_point_id": item["point"]["decision_point_id"],
                    "domain": item["point"]["domain"],
                    "task_id": item["point"]["task_id"],
                    "budget": budget,
                    "raw_serialized_tokens": raw_tokens,
                    "compiled_serialized_tokens": bundle.serialized_token_cost,
                    "serialized_reduction": (
                        (raw_tokens - bundle.serialized_token_cost) / raw_tokens
                        if raw_tokens
                        else None
                    ),
                    "hard_coverage": _coverage(item["state"], bundle),
                    "conservative_fallback": bundle.budget_infeasible,
                    "hard_limit_exceeded": bundle.hard_limit_exceeded,
                    "matched_budget_eligible": bundle.matched_budget_eligible,
                    "request_hash": bundle.request_hash,
                }
            )

    budget_summary: list[dict[str, Any]] = []
    for budget in budgets:
        rows = [row for row in budget_rows if row["budget"] == budget]
        budget_summary.append(
            {
                "budget": budget,
                "decision_points": len(rows),
                "hard_coverage_rate": _rate(rows, "hard_coverage"),
                "conservative_fallback_rate": _rate(rows, "conservative_fallback"),
                "hard_limit_exceeded_rate": _rate(rows, "hard_limit_exceeded"),
                "median_raw_serialized_tokens": _median(rows, "raw_serialized_tokens"),
                "median_compiled_serialized_tokens": _median(rows, "compiled_serialized_tokens"),
                "median_serialized_reduction": _median(rows, "serialized_reduction"),
            }
        )
    primary_candidates = [
        row
        for row in budget_summary
        if row["hard_coverage_rate"] == 1.0
        and row["conservative_fallback_rate"] <= 0.05
        and row["hard_limit_exceeded_rate"] == 0.0
    ]
    primary_budget = min((int(row["budget"]) for row in primary_candidates), default=None)

    ablation_rows: list[dict[str, Any]] = []
    if primary_budget is not None:
        for item in prepared:
            for ablation in ABLATIONS:
                bundle = compile_decision_state(
                    item["prefix"],
                    item["state"],
                    item["query"],
                    item["protocol"],
                    primary_budget,
                    config=CompilerConfig(ablations=frozenset({ablation})),
                )
                ablation_rows.append(
                    {
                        "decision_point_id": item["point"]["decision_point_id"],
                        "ablation": ablation,
                        "serialized_tokens": bundle.serialized_token_cost,
                        "hard_coverage": _coverage(item["state"], bundle),
                        "conservative_fallback": bundle.budget_infeasible,
                    }
                )
    ablation_summary = []
    for ablation in ABLATIONS:
        rows = [row for row in ablation_rows if row["ablation"] == ablation]
        ablation_summary.append(
            {
                "ablation": ablation,
                "decision_points": len(rows),
                "median_serialized_tokens": _median(rows, "serialized_tokens"),
                "hard_coverage_rate": _rate(rows, "hard_coverage"),
                "conservative_fallback_rate": _rate(rows, "conservative_fallback"),
            }
        )

    structured_rate = structured_equivalent / structured_total if structured_total else 0.0
    sufficiency_rate = statistics.fmean(map(float, sufficiency)) if sufficiency else 0.0
    primary_summary = next(
        (row for row in budget_summary if row["budget"] == primary_budget), None
    )
    checks = {
        "structured_equivalence_100_percent": structured_rate == 1.0,
        "provisional_decision_sufficiency_at_least_95_percent": sufficiency_rate >= 0.95,
        "primary_budget_identified": primary_budget is not None,
        "median_serialized_reduction_at_least_30_percent": bool(primary_summary)
        and float(primary_summary["median_serialized_reduction"] or 0.0) >= 0.30,
    }
    report: dict[str, Any] = {
        "schema_version": "gdsc_offline_mechanism_v1",
        "execution": "offline_zero_api",
        "dataset_sha256": dataset.get("dataset_sha256"),
        "decision_point_count": len(prepared),
        "tool_schema_provenance": "native_tau3_environment_openai_schema",
        "structured_representation_count": structured_total,
        "structured_equivalence_rate": structured_rate,
        "provisional_decision_sufficiency_rate": sufficiency_rate,
        "budgets": budget_summary,
        "primary_budget": primary_budget,
        "ablations": ablation_summary,
        "r2_gate": {**checks, "passed": all(checks.values())},
        "limitations": [
            "Decision sufficiency is a machine outcome check, not human construct gold.",
            "Serialized costs use the deterministic repository estimator, not provider-actual usage.",
            "Archive handles are provenance-only and no recovery-effect claim is made.",
        ],
    }
    report["report_sha256"] = sha256_json(report)
    return report, budget_rows, ablation_rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["empty"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--budgets", type=int, nargs="+", default=[2048, 4096, 8192, 12288, 16384]
    )
    parser.add_argument("--hard-context-limit", type=int, default=128000)
    parser.add_argument("--shard-count", type=int, default=1)
    parser.add_argument("--shard-index", type=int, default=0)
    args = parser.parse_args()
    if sorted(set(args.budgets)) != list(args.budgets) or any(value <= 0 for value in args.budgets):
        raise ValueError("budgets must be unique, ascending, and positive")
    if args.shard_count <= 0 or not 0 <= args.shard_index < args.shard_count:
        raise ValueError("shard-index must be in [0, shard-count)")
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    if args.shard_count > 1:
        points = sorted(
            dataset.get("decision_points", ()),
            key=lambda item: str(item["decision_point_id"]),
        )
        dataset = dict(dataset)
        dataset["decision_points"] = [
            point
            for index, point in enumerate(points)
            if index % args.shard_count == args.shard_index
        ]
    report, budget_rows, ablation_rows = evaluate(
        dataset, budgets=args.budgets, hard_context_limit=args.hard_context_limit
    )
    report["shard"] = {
        "index": args.shard_index,
        "count": args.shard_count,
        "selection": "sorted_decision_point_id_modulo",
    }
    report.pop("report_sha256", None)
    report["report_sha256"] = sha256_json(report)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "r2_offline_mechanism.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(args.output / "r2_budget_rows.csv", budget_rows)
    _write_csv(args.output / "r2_ablation_rows.csv", ablation_rows)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
