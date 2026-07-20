"""Deterministic phase-three reliability interventions and P1 smoke runner."""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .archive import ArchiveStore
from .capture import ToolExecutor, estimate_tokens
from .context import ContextView, build_context_managers
from .failure_cards import build_failure_cards
from .graph import TraceGraph
from .lifecycle import LifecycleEngine
from .message_protocol import project_context_items_to_messages
from .schema import FailureClass, Node, NodeType, ToolStatus, utc_now


P1_INTERVENTION_KINDS = (
    "argument_correction",
    "latest_failure_only",
    "alternative_tool_completion",
    "malformed_then_valid",
)
P1_CONDITIONS = (
    "full_trajectory",
    "ours_without_failure_retention",
    "raw_hard_failure_retention",
    "full_ours",
)


@dataclass(frozen=True, slots=True)
class InterventionSpec:
    intervention_id: str
    intervention_kind: str
    task_index: int
    seed: int
    entity_id: str


@dataclass(frozen=True, slots=True)
class InterventionConfig:
    tasks_per_kind: int = 8
    base_seed: int = 4100
    budget: int = 512

    def __post_init__(self) -> None:
        if not 5 <= self.tasks_per_kind <= 10:
            raise ValueError("P1 requires 5-10 fixed tasks per intervention kind")
        if self.budget <= 0:
            raise ValueError("budget must be positive")


def build_intervention_specs(config: InterventionConfig) -> list[InterventionSpec]:
    """Return the frozen deterministic P1 task/seed matrix."""

    specs: list[InterventionSpec] = []
    ordinal = 0
    for kind in P1_INTERVENTION_KINDS:
        for task_index in range(config.tasks_per_kind):
            seed = config.base_seed + ordinal
            specs.append(
                InterventionSpec(
                    intervention_id=f"p1_{kind}_{task_index:02d}",
                    intervention_kind=kind,
                    task_index=task_index,
                    seed=seed,
                    entity_id=f"E-{task_index:03d}",
                )
            )
            ordinal += 1
    return specs


def _tool_message(call: Node, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": call.node_id,
                "function": {
                    "name": call.metadata.get("tool_name"),
                    "arguments": arguments,
                },
            }
        ],
    }


def _result_message(call: Node, result: Node) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call.node_id,
        "content": result.content,
    }


def _attach_message_ordinals(
    call: Node,
    result: Node,
    arguments: dict[str, Any],
    messages: list[dict[str, Any]],
) -> None:
    call.metadata["source_message_ordinal"] = len(messages) + 1
    messages.append(_tool_message(call, arguments))
    result.metadata["source_message_ordinal"] = len(messages) + 1
    messages.append(_result_message(call, result))


def _diagnostic_payload(error: str, spec: InterventionSpec) -> dict[str, Any]:
    # The long raw diagnostic makes the P1 token intervention identifiable:
    # card conditions keep the actionable cause while raw conditions replay it.
    return {
        "error": error,
        "diagnostic_blob": f"{spec.intervention_id}:" + "x" * (720 + spec.task_index * 8),
    }


def _record_result(
    executor: ToolExecutor,
    messages: list[dict[str, Any]],
    *,
    tool_name: str,
    arguments: dict[str, Any],
    step_id: int,
    status: ToolStatus,
    payload: Any,
) -> tuple[Node, Node]:
    call, result = executor.record_result(
        tool_name=tool_name,
        arguments=arguments,
        step_id=step_id,
        status=status,
        payload=payload,
    )
    _attach_message_ordinals(call, result, arguments, messages)
    return call, result


def _initial_trace(
    spec: InterventionSpec,
    archive: ArchiveStore,
) -> tuple[
    TraceGraph,
    ToolExecutor,
    list[dict[str, Any]],
    Node,
    str,
    dict[str, Any],
    str,
]:
    graph = TraceGraph(
        session_id=spec.intervention_id,
        metadata={
            "source": "phase3_p1_controlled_intervention",
            "synthetic": True,
            "controlled_ground_truth": True,
            "intervention_kind": spec.intervention_kind,
            "seed": spec.seed,
        },
    )
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": f"Complete {spec.intervention_kind} for {spec.entity_id}.",
        }
    ]
    graph.create_node(
        NodeType.GOAL,
        messages[0]["content"],
        0,
        token_count=estimate_tokens(messages[0]["content"]),
        metadata={"source_message_ordinal": 1},
    )
    executor = ToolExecutor(graph, archive)
    tool_name = "update_record"
    corrected_arguments: dict[str, Any]
    expected_expiry: str

    if spec.intervention_kind == "argument_correction":
        invalid = {"record_id": spec.entity_id, "page": 0}
        corrected_arguments = {"record_id": spec.entity_id, "page": 1}
        _, latest = _record_result(
            executor,
            messages,
            tool_name=tool_name,
            arguments=invalid,
            step_id=1,
            status=ToolStatus.FAILED,
            payload=_diagnostic_payload("page 0 is invalid; use page 1", spec),
        )
        latest.metadata["next_admissible_correction"] = json.dumps(
            corrected_arguments, sort_keys=True
        )
        expected_expiry = "resolved"
    elif spec.intervention_kind == "latest_failure_only":
        first_arguments = {"record_id": spec.entity_id, "page": 0}
        second_arguments = {"record_id": spec.entity_id, "page": 1}
        corrected_arguments = {"record_id": spec.entity_id, "page": 2}
        _record_result(
            executor,
            messages,
            tool_name=tool_name,
            arguments=first_arguments,
            step_id=1,
            status=ToolStatus.FAILED,
            payload=_diagnostic_payload("page 0 is invalid; try page 1", spec),
        )
        _, latest = _record_result(
            executor,
            messages,
            tool_name=tool_name,
            arguments=second_arguments,
            step_id=2,
            status=ToolStatus.FAILED,
            payload=_diagnostic_payload("page 1 is stale; use page 2", spec),
        )
        latest.metadata["next_admissible_correction"] = json.dumps(
            corrected_arguments, sort_keys=True
        )
        expected_expiry = "resolved"
    elif spec.intervention_kind == "alternative_tool_completion":
        tool_name = "primary_fetch"
        invalid = {"record_id": spec.entity_id}
        corrected_arguments = {"record_id": spec.entity_id}
        _, latest = _record_result(
            executor,
            messages,
            tool_name=tool_name,
            arguments=invalid,
            step_id=1,
            status=ToolStatus.FAILED,
            payload=_diagnostic_payload(
                "primary service unavailable; use fallback_fetch", spec
            ),
        )
        latest.metadata["next_admissible_correction"] = "use fallback_fetch"
        expected_expiry = "alternative_completed"
    elif spec.intervention_kind == "malformed_then_valid":
        invalid = {"record": spec.entity_id}
        corrected_arguments = {"record_id": spec.entity_id, "format": "json"}
        call, latest = _record_result(
            executor,
            messages,
            tool_name=tool_name,
            arguments=invalid,
            step_id=1,
            status=ToolStatus.FAILED,
            payload=_diagnostic_payload(
                "invalid argument: missing record_id and format", spec
            ),
        )
        call.metadata["arguments_valid"] = False
        latest.metadata["failure_class"] = FailureClass.MALFORMED.value
        latest.metadata["malformed_call"] = True
        latest.metadata["next_admissible_correction"] = json.dumps(
            corrected_arguments, sort_keys=True
        )
        expected_expiry = "corrected_syntax"
    else:
        raise ValueError(f"unknown intervention kind: {spec.intervention_kind}")

    messages.append(
        {
            "role": "user",
            "content": "Continue safely using the available failure evidence.",
        }
    )
    LifecycleEngine().apply(graph)
    return (
        graph,
        executor,
        messages,
        latest,
        tool_name,
        corrected_arguments,
        expected_expiry,
    )


def _failure_visible(view: ContextView) -> bool:
    return any(
        item.node_type == NodeType.ERROR
        or (
            item.node_type == NodeType.SUMMARY
            and item.reason.startswith("failure_card")
        )
        for item in view.items
    )


def _input_accounting(
    graph: TraceGraph,
    messages: list[dict[str, Any]],
    view: ContextView,
) -> dict[str, int]:
    ordinals, fragments = project_context_items_to_messages(
        messages,
        view.items,
        graph.nodes,
    )
    selected_messages = [
        message
        for ordinal, message in enumerate(messages, start=1)
        if ordinal in ordinals
    ]
    return {
        "selected_representation_tokens": view.selected_tokens,
        "protocol_closed_message_tokens": sum(
            estimate_tokens(message) for message in selected_messages
        ),
        # This is exact for the deterministic local controller's serialized
        # input, not a claim about an external LLM tokenizer.
        "actual_provider_input_tokens": estimate_tokens(
            {
                "system": "deterministic_p1_controller_v1",
                "messages": selected_messages,
                "active_trace_context": fragments,
            }
        ),
    }


def _sum_accounting(total: dict[str, int], current: dict[str, int]) -> None:
    for key, value in current.items():
        total[key] += value


def _run_one(
    spec: InterventionSpec,
    manager_name: str,
    *,
    budget: int,
    archive: ArchiveStore,
) -> tuple[dict[str, Any], TraceGraph]:
    (
        graph,
        executor,
        messages,
        initial_latest,
        tool_name,
        corrected_arguments,
        expected_expiry,
    ) = _initial_trace(spec, archive)
    graph.metadata["evaluated_context_manager"] = manager_name
    manager = build_context_managers()[manager_name]
    manager_budget = None if manager_name == "full_trajectory" else budget

    totals = {
        "selected_representation_tokens": 0,
        "protocol_closed_message_tokens": 0,
        "actual_provider_input_tokens": 0,
    }
    first_view = manager.select(graph, budget=manager_budget)
    _sum_accounting(totals, _input_accounting(graph, messages, first_view))
    failure_visible = _failure_visible(first_view)
    repeated_invalid_action = 0
    recovery_steps = 1
    fallback_intervention_used = 0

    selected_card_items = [
        item
        for item in first_view.items
        if item.node_type == NodeType.SUMMARY
        and item.reason.startswith("failure_card")
    ]
    card_precision = None
    if manager_name == "full_ours":
        card_precision = float(
            len(selected_card_items) == 1
            and initial_latest.node_id in selected_card_items[0].source_node_ids
        )

    if not failure_visible:
        repeated_invalid_action = 1
        recovery_steps = 2
        fallback_intervention_used = 1
        failed_arguments = (
            {"record": spec.entity_id}
            if spec.intervention_kind == "malformed_then_valid"
            else (
                {"record_id": spec.entity_id}
                if spec.intervention_kind == "alternative_tool_completion"
                else {"record_id": spec.entity_id, "page": 0}
            )
        )
        _, repeated = _record_result(
            executor,
            messages,
            tool_name=tool_name,
            arguments=failed_arguments,
            step_id=initial_latest.step_id + 1,
            status=ToolStatus.FAILED,
            payload=_diagnostic_payload("repeated invalid action", spec),
        )
        if spec.intervention_kind == "malformed_then_valid":
            repeated.metadata["failure_class"] = FailureClass.MALFORMED.value
            repeated.metadata["malformed_call"] = True
        messages.append(
            {
                "role": "user",
                "content": "Safety monitor: apply the known admissible correction now.",
            }
        )
        LifecycleEngine().apply(graph)
        second_view = manager.select(graph, budget=manager_budget)
        _sum_accounting(totals, _input_accounting(graph, messages, second_view))

    recovery_tool = (
        "fallback_fetch"
        if spec.intervention_kind == "alternative_tool_completion"
        else tool_name
    )
    recovery_step = max(node.step_id for node in graph.nodes.values()) + 1
    recovery_call, _ = _record_result(
        executor,
        messages,
        tool_name=recovery_tool,
        arguments=corrected_arguments,
        step_id=recovery_step,
        status=ToolStatus.SUCCESS,
        payload={"status": "completed", "entity_id": spec.entity_id},
    )
    if spec.intervention_kind == "malformed_then_valid":
        recovery_call.metadata["arguments_valid"] = True
    if spec.intervention_kind == "alternative_tool_completion":
        scope = initial_latest.lifecycle_profile.scope.get("operation_key")
        if scope:
            graph.metadata["completed_operation_scopes"] = [scope]
    LifecycleEngine().apply(graph)
    cards_after, expiry_events = build_failure_cards(graph, ttl_steps=8)
    observed_expiry = {
        event.get("expiry_trigger")
        for event in expiry_events
        if event.get("expiry_trigger") is not None
    }
    expiry_correctness = None
    if manager_name == "full_ours":
        expiry_correctness = float(
            not cards_after and expected_expiry in observed_expiry
        )

    graph.metadata.update(
        {
            "task_success": 1.0,
            "policy_violation": 0.0,
            "normal_stop": True,
            "repeated_invalid_action": repeated_invalid_action,
            "recovery_steps": recovery_steps,
            "fallback_intervention_used": fallback_intervention_used,
        }
    )
    row = {
        **asdict(spec),
        "manager": manager_name,
        "budget": manager_budget,
        "failure_visible": int(failure_visible),
        "repeated_invalid_action": repeated_invalid_action,
        "recovery_steps": recovery_steps,
        "normal_stop": 1,
        "policy_violation": 0,
        "task_success": 1,
        "fallback_intervention_used": fallback_intervention_used,
        **totals,
        "provider_kind": "deterministic_local_controller",
        "provider_usage_scope": "exact_serialized_controller_input",
        "card_precision_controlled_gold": card_precision,
        "expiry_correctness_controlled_gold": expiry_correctness,
        "expected_expiry_trigger": expected_expiry,
        "observed_expiry_triggers": sorted(observed_expiry),
        "active_card_count_after_completion": len(cards_after),
        "graph_validation_errors": graph.validate(),
    }
    return row, graph


def _mean(rows: Iterable[dict[str, Any]], key: str) -> float | None:
    values = [
        float(row[key])
        for row in rows
        if isinstance(row.get(key), (int, float))
    ]
    return statistics.fmean(values) if values else None


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    metrics = (
        "repeated_invalid_action",
        "recovery_steps",
        "normal_stop",
        "policy_violation",
        "task_success",
        "selected_representation_tokens",
        "protocol_closed_message_tokens",
        "actual_provider_input_tokens",
        "card_precision_controlled_gold",
        "expiry_correctness_controlled_gold",
    )
    for manager in P1_CONDITIONS:
        manager_rows = [row for row in rows if row["manager"] == manager]
        by_kind = {}
        for kind in P1_INTERVENTION_KINDS:
            kind_rows = [
                row for row in manager_rows if row["intervention_kind"] == kind
            ]
            by_kind[kind] = {
                "n": len(kind_rows),
                **{f"mean_{key}": _mean(kind_rows, key) for key in metrics},
            }
        result[manager] = {
            "n": len(manager_rows),
            **{f"mean_{key}": _mean(manager_rows, key) for key in metrics},
            "by_intervention_kind": by_kind,
        }
    return result


def _paired_comparisons(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_key = {
        (row["intervention_id"], row["manager"]): row
        for row in rows
    }
    metrics = (
        "repeated_invalid_action",
        "recovery_steps",
        "selected_representation_tokens",
        "protocol_closed_message_tokens",
        "actual_provider_input_tokens",
        "task_success",
    )
    comparisons: dict[str, Any] = {}
    for reference in (
        "ours_without_failure_retention",
        "raw_hard_failure_retention",
        "full_trajectory",
    ):
        pairs = []
        for spec_id in sorted({row["intervention_id"] for row in rows}):
            candidate = by_key[(spec_id, "full_ours")]
            baseline = by_key[(spec_id, reference)]
            pairs.append((candidate, baseline))
        comparisons[f"full_ours_vs_{reference}"] = {
            "paired_n": len(pairs),
            **{
                f"mean_{metric}_delta": statistics.fmean(
                    float(candidate[metric]) - float(baseline[metric])
                    for candidate, baseline in pairs
                )
                for metric in metrics
            },
        }
    return comparisons


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = sorted({key for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(fieldnames=fieldnames, f=handle)
        writer.writeheader()
        for row in rows:
            values = {
                key: (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else value
                )
                for key, value in row.items()
            }
            writer.writerow(values)


def run_p1_interventions(
    output_dir: str | Path,
    *,
    config: InterventionConfig | None = None,
) -> dict[str, Any]:
    """Run and persist the complete deterministic P1 four-condition matrix."""

    frozen = config or InterventionConfig()
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    graph_dir = output / "graphs"
    archive_root = output / "archive"
    graph_dir.mkdir(exist_ok=True)
    archive_root.mkdir(exist_ok=True)

    specs = build_intervention_specs(frozen)
    rows: list[dict[str, Any]] = []
    for spec in specs:
        for manager_name in P1_CONDITIONS:
            row_archive = ArchiveStore(
                archive_root / f"{spec.intervention_id}_{manager_name}"
            )
            row, graph = _run_one(
                spec,
                manager_name,
                budget=frozen.budget,
                archive=row_archive,
            )
            rows.append(row)
            graph.save(
                graph_dir / f"{spec.intervention_id}_{manager_name}.json"
            )

    aggregate = _aggregate(rows)
    comparisons = _paired_comparisons(rows)
    card_metrics = aggregate["full_ours"]
    vs_remove = comparisons["full_ours_vs_ours_without_failure_retention"]
    vs_raw = comparisons["full_ours_vs_raw_hard_failure_retention"]
    mechanism_gate = {
        "complete": len(rows) == len(specs) * len(P1_CONDITIONS),
        "all_graphs_valid": all(not row["graph_validation_errors"] for row in rows),
        "card_precision_controlled_gold": card_metrics[
            "mean_card_precision_controlled_gold"
        ],
        "expiry_correctness_controlled_gold": card_metrics[
            "mean_expiry_correctness_controlled_gold"
        ],
        "card_reduces_repeated_invalid_action_vs_remove": (
            vs_remove["mean_repeated_invalid_action_delta"] < 0
        ),
        "card_reduces_recovery_steps_vs_remove": (
            vs_remove["mean_recovery_steps_delta"] < 0
        ),
        "card_reduces_protocol_tokens_vs_raw": (
            vs_raw["mean_protocol_closed_message_tokens_delta"] < 0
        ),
        "card_reduces_controller_input_vs_raw": (
            vs_raw["mean_actual_provider_input_tokens_delta"] < 0
        ),
        "task_success_non_degraded": (
            vs_remove["mean_task_success_delta"] >= 0
            and vs_raw["mean_task_success_delta"] >= 0
        ),
        "all_failure_types_directionally_consistent": all(
            aggregate["full_ours"]["by_intervention_kind"][kind][
                "mean_repeated_invalid_action"
            ]
            < aggregate["ours_without_failure_retention"][
                "by_intervention_kind"
            ][kind]["mean_repeated_invalid_action"]
            and aggregate["full_ours"]["by_intervention_kind"][kind][
                "mean_actual_provider_input_tokens"
            ]
            < aggregate["raw_hard_failure_retention"][
                "by_intervention_kind"
            ][kind]["mean_actual_provider_input_tokens"]
            for kind in P1_INTERVENTION_KINDS
        ),
        "human_construct_validation": "not_run",
    }
    mechanism_gate["p1_engineering_gate_passed"] = all(
        value is True
        for key, value in mechanism_gate.items()
        if key not in {
            "card_precision_controlled_gold",
            "expiry_correctness_controlled_gold",
            "human_construct_validation",
            "p1_engineering_gate_passed",
        }
    ) and all(
        mechanism_gate[key] == 1.0
        for key in (
            "card_precision_controlled_gold",
            "expiry_correctness_controlled_gold",
        )
    )

    manifest = {
        "schema_version": "1.0",
        "generated_at": utc_now(),
        "phase": "P1",
        "experiment": "controlled_failure_card_interventions",
        "config": asdict(frozen),
        "intervention_kinds": list(P1_INTERVENTION_KINDS),
        "conditions": list(P1_CONDITIONS),
        "task_count": len(specs),
        "run_count": len(rows),
        "controlled_ground_truth": True,
        "interpretation_warning": (
            "This deterministic local-controller matrix validates mechanism "
            "identifiability. It is not human construct validation or external-LLM "
            "benchmark evidence."
        ),
        "mechanism_gate": mechanism_gate,
        "files": [
            "per_run.jsonl",
            "per_run.csv",
            "aggregate.json",
            "paired_comparisons.json",
            "manifest.json",
            "graphs/",
            "archive/",
        ],
    }
    _write_jsonl(output / "per_run.jsonl", rows)
    _write_csv(output / "per_run.csv", rows)
    _write_json(output / "aggregate.json", aggregate)
    _write_json(output / "paired_comparisons.json", comparisons)
    _write_json(output / "manifest.json", manifest)
    return manifest
