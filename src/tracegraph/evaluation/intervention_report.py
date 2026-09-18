"""Definitions moved from ``tracegraph.interventions``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import csv
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable
from ..archive import ArchiveStore
from ..capture import ToolExecutor, estimate_tokens
from ..context import ContextView, build_context_managers
from ..failure_cards import build_failure_cards
from ..graph import TraceGraph
from ..lifecycle import LifecycleEngine
from ..message_protocol import project_context_items_to_messages
from ..schema import FailureClass, Node, NodeType, ToolStatus, utc_now

from .intervention_constants import (
    P1_CONDITIONS as P1_CONDITIONS,
    P1_INTERVENTION_KINDS as P1_INTERVENTION_KINDS,
)



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


# Imported after definitions so mutually-referential helpers initialize safely.
from .intervention_runner import (
    _run_one as _run_one,
)

from .intervention_scenarios import (
    InterventionConfig as InterventionConfig,
    build_intervention_specs as build_intervention_specs,
)
