"""Run the zero-API GDSC R2.1 cost-attribution and attainability audit."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from evaluate_gdsc_offline import (
    _domain_tool_schemas,
    _policy_text,
    _prefix_graph,
    _prefix_messages,
)
from tau2.utils.llm_utils import to_litellm_messages

from tracegraph.compiler import compile as compile_decision_state
from tracegraph.cost_attribution import summarize_cost_attribution
from tracegraph.decision_query import build_decision_query
from tracegraph.graph import TraceGraph
from tracegraph.integrations.tau3_agent import TraceGraphTauAgent
from tracegraph.policy_rules import compile_policy_rule
from tracegraph.provider_cost import (
    ProviderProtocol,
    canonical_request_json,
    provider_prompt_request,
    request_sha256,
    serialized_request_cost,
)
from tracegraph.schema import NodeType
from tracegraph.state_reducer import reduce_event_graph
from tracegraph.trajectory_artifacts import sha256_json


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


POLICY_ATOM_TYPES = {"global_policy_rule", "applicable_policy_rule"}


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verified_json_hash(path: Path, key: str) -> tuple[str, bool]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    declared = str(payload.pop(key))
    return declared, declared == sha256_json(payload)


def build_baseline_manifest(
    *,
    prompt_cost_report: Path,
    eligibility_report: Path,
    dataset_path: Path,
    r2_report: Path,
    r2_budget_rows: Path,
    config_path: Path,
) -> dict[str, Any]:
    embedded = {
        "prompt_cost_report": (prompt_cost_report, "report_sha256"),
        "eligibility_report": (eligibility_report, "report_sha256"),
        "decision_point_dataset": (dataset_path, "dataset_sha256"),
        "r2_report": (r2_report, "report_sha256"),
    }
    artifacts: dict[str, Any] = {}
    for name, (path, key) in embedded.items():
        declared, valid = _verified_json_hash(path, key)
        artifacts[name] = {
            "path": path.as_posix(),
            "file_sha256": _file_sha256(path),
            "embedded_sha256": declared,
            "embedded_hash_valid": valid,
        }
    for name, path in {
        "r2_budget_rows": r2_budget_rows,
        "gdsc_core_config": config_path,
    }.items():
        artifacts[name] = {
            "path": path.as_posix(),
            "file_sha256": _file_sha256(path),
        }
    manifest: dict[str, Any] = {
        "schema_version": "gdsc_r2_1_baseline_manifest_v1",
        "baseline": "gdsc_core_v1_r2_no_go",
        "artifacts": artifacts,
        "all_embedded_hashes_valid": all(
            item.get("embedded_hash_valid", True) for item in artifacts.values()
        ),
        "external_provider_generations": 0,
        "historical_r2_result_is_immutable": True,
    }
    manifest["manifest_sha256"] = sha256_json(manifest)
    return manifest


def _load_budget_rows(path: Path, budget: int) -> dict[str, dict[str, Any]]:
    with path.open(encoding="utf-8", newline="") as handle:
        selected = [
            row
            for row in csv.DictReader(handle)
            if int(row["budget"]) == budget
        ]
    result = {str(row["decision_point_id"]): row for row in selected}
    if len(result) != len(selected):
        raise ValueError("R2 budget rows contain duplicate decision points")
    return result


def _system_messages(rules: Sequence[str]) -> tuple[dict[str, Any], ...]:
    return (
        ({"role": "system", "content": "\n\n".join(rules)},)
        if rules
        else ()
    )


def _cost(
    *,
    model: str,
    messages: Sequence[Mapping[str, Any]],
    tools: Sequence[Mapping[str, Any]],
    hard_context_limit: int,
) -> int:
    protocol = ProviderProtocol(
        model=model,
        tools=tuple(dict(item) for item in tools),
        hard_context_limit=hard_context_limit,
    )
    return serialized_request_cost(protocol, messages)


def _state_message_ids(bundle, policy_atom_ids: set[str]) -> tuple[set[str], set[str]]:
    state_ids: set[str] = set()
    policy_ids: set[str] = set()
    for item in bundle.representation_manifest:
        representation_id = str(item["representation_id"])
        state_ids.add(representation_id)
        if policy_atom_ids.intersection(map(str, item.get("covered_atoms", ()))):
            policy_ids.add(representation_id)
    return state_ids, policy_ids


def _filter_state_messages(
    messages: Sequence[Mapping[str, Any]],
    *,
    remove_representation_ids: set[str] | None = None,
    remove_all_state: bool = False,
) -> tuple[dict[str, Any], ...]:
    result: list[dict[str, Any]] = []
    for message in messages:
        payload = dict(message)
        if str(payload.get("role")) != "system" or not isinstance(
            payload.get("content"), str
        ):
            result.append(payload)
            continue
        try:
            content = json.loads(payload["content"])
        except json.JSONDecodeError:
            result.append(payload)
            continue
        fragments = content.get("gdsc_decision_state") if isinstance(content, dict) else None
        if not isinstance(fragments, list):
            result.append(payload)
            continue
        if remove_all_state:
            continue
        kept = [
            fragment
            for fragment in fragments
            if str(fragment.get("representation_id"))
            not in (remove_representation_ids or set())
        ]
        if kept:
            payload["content"] = json.dumps(
                {"gdsc_decision_state": kept},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            result.append(payload)
    return tuple(result)


def _count_exact_strings(value: Any, targets: set[str]) -> dict[str, int]:
    counts = {target: 0 for target in targets}

    def visit(item: Any) -> None:
        if isinstance(item, str):
            if item in counts:
                counts[item] += 1
        elif isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)

    visit(value)
    return counts


def _constructive_hard_messages(state, policies: Sequence[str]) -> tuple[dict[str, Any], ...]:
    policy_atoms = [
        atom for atom in state.atoms if atom.atom_type.value in POLICY_ATOM_TYPES
    ]
    hard_atoms = [
        atom
        for atom in state.atoms
        if atom.hard and atom.atom_type.value not in POLICY_ATOM_TYPES
    ]
    compact = {
        "schema": "gdsc_constructive_hard_floor_v1",
        "policy_provenance": [
            {
                "id": atom.atom_id,
                "src": list(atom.source_event_ids),
            }
            for atom in policy_atoms
        ],
        "hard_state": [
            {
                "id": atom.atom_id,
                "t": atom.atom_type.value,
                "k": atom.key,
                "v": atom.value,
                "s": atom.status,
                "src": list(atom.source_event_ids),
            }
            for atom in hard_atoms
        ],
    }
    messages = list(_system_messages(policies))
    messages.append(
        {
            "role": "system",
            "content": json.dumps(
                compact,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ),
        }
    )
    return tuple(messages)


def attribution_row(
    *,
    point: Mapping[str, Any],
    graph: TraceGraph,
    baseline: Mapping[str, Any],
    schemas: Sequence[Mapping[str, Any]],
    budget: int,
    hard_context_limit: int,
    model: str,
) -> dict[str, Any]:
    decision = graph.nodes[str(point["outcome"]["decision_node_id"])]
    prefix = _prefix_graph(graph, decision)
    messages = _prefix_messages(prefix)
    policies = _policy_text(prefix)
    rules = tuple(
        compile_policy_rule(node.content, source_event_ids=(node.node_id,))
        for node in prefix.find_nodes(node_types={NodeType.CONSTRAINT})
    )
    state = reduce_event_graph(prefix, tool_schemas=schemas, policy_rules=rules)
    query = build_decision_query(state, tool_schemas=schemas, policy_rules=rules)
    baseline_protocol = ProviderProtocol(
        model=model,
        base_messages=messages,
        tools=tuple(dict(item) for item in schemas),
        hard_context_limit=hard_context_limit,
    )
    baseline_bundle = compile_decision_state(
        prefix, state, query, baseline_protocol, budget
    )

    # Exercise the same provider-message conversion used by the live τ³ agent.
    # The first round trip canonicalizes the reconstructed prefix; the second
    # proves that the compiler bundle survives Tau models and reaches LiteLLM
    # byte-for-byte within the prompt-hash scope.
    runtime_source_messages = tuple(
        to_litellm_messages(
            [TraceGraphTauAgent._load_message(dict(message)) for message in messages]
        )
    )
    runtime_protocol = ProviderProtocol(
        model=model,
        base_messages=runtime_source_messages,
        tools=tuple(dict(item) for item in schemas),
        hard_context_limit=hard_context_limit,
    )
    bundle = compile_decision_state(
        prefix, state, query, runtime_protocol, budget
    )
    runtime_sent_messages = tuple(
        to_litellm_messages(
            [
                TraceGraphTauAgent._load_message(dict(message))
                for message in bundle.messages
            ]
        )
    )
    runtime_request = provider_prompt_request(
        model=model,
        messages=runtime_sent_messages,
        tools=schemas,
    )

    frozen_raw_messages = _system_messages(policies) + tuple(messages)
    frozen_raw_tokens = _cost(
        model=model,
        messages=frozen_raw_messages,
        tools=schemas,
        hard_context_limit=hard_context_limit,
    )
    raw_messages = _system_messages(policies) + runtime_source_messages
    raw_tokens = _cost(
        model=model,
        messages=raw_messages,
        tools=schemas,
        hard_context_limit=hard_context_limit,
    )
    raw_without_policy = _cost(
        model=model,
        messages=runtime_source_messages,
        tools=schemas,
        hard_context_limit=hard_context_limit,
    )
    raw_without_tools = _cost(
        model=model,
        messages=raw_messages,
        tools=(),
        hard_context_limit=hard_context_limit,
    )
    raw_fixed = _cost(
        model=model,
        messages=_system_messages(policies),
        tools=schemas,
        hard_context_limit=hard_context_limit,
    )
    empty = _cost(
        model=model,
        messages=(),
        tools=(),
        hard_context_limit=hard_context_limit,
    )

    compiled_messages = tuple(bundle.messages)
    policy_atom_ids = {
        atom.atom_id
        for atom in state.atoms
        if atom.atom_type.value in POLICY_ATOM_TYPES
    }
    _all_representation_ids, policy_representation_ids = _state_message_ids(
        bundle, policy_atom_ids
    )
    no_state_messages = _filter_state_messages(
        compiled_messages, remove_all_state=True
    )
    no_policy_messages = _filter_state_messages(
        compiled_messages,
        remove_representation_ids=policy_representation_ids,
    )
    state_only_messages = tuple(
        message
        for message in compiled_messages
        if message not in no_state_messages
    )
    compiled_without_tools = _cost(
        model=model,
        messages=compiled_messages,
        tools=(),
        hard_context_limit=hard_context_limit,
    )
    compiled_without_state = _cost(
        model=model,
        messages=no_state_messages,
        tools=schemas,
        hard_context_limit=hard_context_limit,
    )
    compiled_without_policy = _cost(
        model=model,
        messages=no_policy_messages,
        tools=schemas,
        hard_context_limit=hard_context_limit,
    )
    compiled_state_only = _cost(
        model=model,
        messages=state_only_messages,
        tools=schemas,
        hard_context_limit=hard_context_limit,
    )

    constructive_messages = _constructive_hard_messages(state, policies)
    constructive_tokens = _cost(
        model=model,
        messages=constructive_messages,
        tools=schemas,
        hard_context_limit=hard_context_limit,
    )
    hard_atom_ids = {atom.atom_id for atom in state.atoms if atom.hard}
    constructive_ids = {
        atom.atom_id
        for atom in state.atoms
        if atom.hard or atom.atom_type.value in POLICY_ATOM_TYPES
    }
    policy_counts = _count_exact_strings(
        bundle.representation_manifest,
        set(policies),
    )
    baseline_raw = int(float(baseline["raw_serialized_tokens"]))
    baseline_compiled = int(float(baseline["compiled_serialized_tokens"]))
    compiled_tokens = bundle.serialized_token_cost
    return {
        "decision_point_id": point["decision_point_id"],
        "session_id": point["session_id"],
        "domain": point["domain"],
        "task_id": point["task_id"],
        "budget": budget,
        "state_atom_count": len(state.atoms),
        "hard_atom_count": len(hard_atom_ids),
        "representation_count": len(bundle.representation_manifest),
        "raw_serialized_tokens": raw_tokens,
        "compiled_serialized_tokens": compiled_tokens,
        "current_serialized_reduction": (
            (raw_tokens - compiled_tokens) / raw_tokens if raw_tokens else None
        ),
        "raw_empty_request_tokens": empty,
        "raw_fixed_policy_tools_tokens": raw_fixed,
        "raw_policy_marginal_tokens": raw_tokens - raw_without_policy,
        "raw_tool_schema_marginal_tokens": raw_tokens - raw_without_tools,
        "raw_dynamic_history_marginal_tokens": raw_tokens - raw_fixed,
        "fixed_floor_max_reduction": (
            (raw_tokens - raw_fixed) / raw_tokens if raw_tokens else None
        ),
        "target_tokens_at_30_percent": math.floor(raw_tokens * 0.70),
        "fixed_floor_meets_30_percent_target": raw_fixed <= raw_tokens * 0.70,
        "compiled_graph_selected_tokens": bundle.costs.graph_selected,
        "compiled_representation_tokens": bundle.costs.compiled,
        "compiled_protocol_closed_tokens": bundle.costs.protocol_closed,
        "compiled_protocol_closure_increment": (
            bundle.costs.protocol_closed - bundle.costs.compiled
        ),
        "compiled_serializer_and_schema_overhead": (
            bundle.costs.serialized_request - bundle.costs.protocol_closed
        ),
        "compiled_tool_schema_marginal_tokens": (
            compiled_tokens - compiled_without_tools
        ),
        "compiled_state_marginal_tokens": (
            compiled_tokens - compiled_without_state
        ),
        "compiled_policy_marginal_tokens": (
            compiled_tokens - compiled_without_policy
        ),
        "compiled_closed_history_marginal_tokens": (
            compiled_tokens - compiled_state_only
        ),
        "constructive_hard_floor_tokens": constructive_tokens,
        "constructive_hard_floor_reduction": (
            (raw_tokens - constructive_tokens) / raw_tokens if raw_tokens else None
        ),
        "constructive_hard_coverage": hard_atom_ids.issubset(constructive_ids),
        "policy_atom_count": len(policy_atom_ids),
        "policy_representation_count": len(policy_representation_ids),
        "policy_exposed_exactly_once": bool(policies)
        and all(policy_counts.get(policy) == 1 for policy in policies),
        "tool_schema_top_level_exact": (
            tuple(bundle.tools) == tuple(dict(item) for item in schemas)
            and bundle.request.get("tools") == [dict(item) for item in schemas]
        ),
        "request_hash": bundle.request_hash,
        "request_hash_recomputed": request_sha256(bundle.request),
        "runtime_prompt_hash_matches": (
            tuple(bundle.messages) == runtime_sent_messages
            and bundle.request == runtime_request
            and bundle.request_hash == request_sha256(runtime_request)
        ),
        "frozen_baseline_request_hash": baseline_bundle.request_hash,
        "request_hash_matches_baseline": (
            baseline_bundle.request_hash == str(baseline["request_hash"])
            and baseline_bundle.request_hash
            == request_sha256(baseline_bundle.request)
        ),
        "raw_cost_matches_baseline": frozen_raw_tokens == baseline_raw,
        "compiled_cost_matches_baseline": (
            baseline_bundle.serialized_token_cost == baseline_compiled
        ),
        "request_canonical_bytes": len(
            canonical_request_json(bundle.request).encode("utf-8")
        ),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["empty"])
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--r2-report", type=Path, required=True)
    parser.add_argument("--r2-budget-rows", type=Path, required=True)
    parser.add_argument("--prompt-cost-report", type=Path, required=True)
    parser.add_argument("--eligibility-report", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budget", type=int, default=8192)
    parser.add_argument("--threshold", type=float, default=0.30)
    parser.add_argument("--hard-context-limit", type=int, default=128000)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    baseline_rows = _load_budget_rows(args.r2_budget_rows, args.budget)
    points = sorted(
        dataset.get("decision_points", ()),
        key=lambda item: str(item["decision_point_id"]),
    )
    if {str(point["decision_point_id"]) for point in points} != set(baseline_rows):
        raise ValueError("dataset and R2 baseline decision points differ")
    sources = {
        str(item["session_id"]): item
        for item in dataset.get("sources", ())
        if isinstance(item, Mapping)
    }
    graphs = {
        session_id: TraceGraph.load(str(source["source_path"]))
        for session_id, source in sources.items()
    }
    schemas_by_domain = {
        domain: _domain_tool_schemas(domain)
        for domain in sorted({str(item["domain"]) for item in sources.values()})
    }
    rows = [
        attribution_row(
            point=point,
            graph=graphs[str(point["session_id"])],
            baseline=baseline_rows[str(point["decision_point_id"])],
            schemas=schemas_by_domain[str(point["domain"])],
            budget=args.budget,
            hard_context_limit=args.hard_context_limit,
            model="zai/glm-4.7-flash",
        )
        for point in points
    ]
    summary = summarize_cost_attribution(rows, threshold=args.threshold)
    manifest = build_baseline_manifest(
        prompt_cost_report=args.prompt_cost_report,
        eligibility_report=args.eligibility_report,
        dataset_path=args.dataset,
        r2_report=args.r2_report,
        r2_budget_rows=args.r2_budget_rows,
        config_path=args.config,
    )
    result: dict[str, Any] = {
        "schema_version": "gdsc_r2_1_cost_attribution_v1",
        "execution": "offline_zero_api",
        "baseline_manifest_sha256": manifest["manifest_sha256"],
        "dataset_sha256": dataset.get("dataset_sha256"),
        "cost_definition": {
            "authoritative_total": "canonical serialized model/messages/tools request",
            "marginals": "leave_one_component_out; not assumed additive",
            "fixed_floor": "model + full policy + native tau3 tool schemas; no dynamic history",
            "constructive_floor": "full policy + native schemas + provenance-carrying hard atoms",
        },
        "summary": summary,
        "external_provider_generations": 0,
    }
    result["report_sha256"] = sha256_json(result)
    attainability: dict[str, Any] = {
        "schema_version": "gdsc_r2_1_attainability_v1",
        "source_report_sha256": result["report_sha256"],
        "threshold": args.threshold,
        "decision": summary["attainability_decision"],
        "diagnostic_gate_passed": summary["diagnostic_gate_passed"],
        "blockers": summary["blockers"],
        "median_fixed_floor_max_reduction": summary[
            "median_fixed_floor_max_reduction"
        ],
        "median_constructive_hard_floor_reduction": summary[
            "median_constructive_hard_floor_reduction"
        ],
        "next_action": {
            "reachable_with_verified_constructive_request": (
                "preregister_gdsc_core_v1.1_lossless_packing"
            ),
            "unreachable_under_frozen_fixed_cost": (
                "freeze_unattainability_and_do_not_rerun_r3_r4"
            ),
            "measurement_invalid": "repair_measurement_before_algorithm_changes",
            "indeterminate_stop": "refine_zero_api_diagnostic_without_entering_r3",
        }[summary["attainability_decision"]],
        "external_provider_generations": 0,
    }
    attainability["report_sha256"] = sha256_json(attainability)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "baseline_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "cost_attribution.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (args.output / "attainability_report.json").write_text(
        json.dumps(attainability, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    _write_csv(args.output / "cost_attribution_rows.csv", rows)
    print(json.dumps({"summary": summary, "attainability": attainability}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
