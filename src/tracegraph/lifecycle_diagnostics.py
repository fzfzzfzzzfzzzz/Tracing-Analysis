"""Diagnostics for lifecycle/no-lifecycle paired live experiments."""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .graph import TraceGraph


_TRACE_COUNT_FIELDS = (
    "node_count",
    "edge_count",
    "error_nodes",
    "side_effect_nodes",
    "failed_with_edges",
    "retry_edges",
    "resolve_edges",
    "supersede_edges",
    "context_view_count",
    "views_with_selected_errors",
    "selected_error_items",
    "unresolved_failure_reason_items",
    "over_budget_views",
)


def _context_view_summary(trace_path: Path) -> dict[str, int]:
    path = trace_path.parent / "context_views.jsonl"
    summary = {
        "context_view_count": 0,
        "views_with_selected_errors": 0,
        "selected_error_items": 0,
        "unresolved_failure_reason_items": 0,
        "over_budget_views": 0,
    }
    if not path.is_file():
        return summary
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        view = json.loads(line)
        if not isinstance(view, dict):
            continue
        summary["context_view_count"] += 1
        items = [
            item
            for item in (view.get("items") or [])
            if isinstance(item, dict)
        ]
        error_items = [
            item for item in items if item.get("node_type") == "error"
        ]
        summary["selected_error_items"] += len(error_items)
        summary["views_with_selected_errors"] += bool(error_items)
        summary["unresolved_failure_reason_items"] += sum(
            "unresolved_failure" in str(item.get("reason") or "")
            for item in error_items
        )
        selected = view.get("selected_tokens")
        budget = view.get("budget")
        if (
            isinstance(selected, (int, float))
            and isinstance(budget, (int, float))
            and selected > budget
        ):
            summary["over_budget_views"] += 1
    return summary


def _trace_summary(path: Path) -> dict[str, Any]:
    graph = TraceGraph.load(path)
    node_types = Counter(node.node_type.value for node in graph.nodes.values())
    lifecycle_states = Counter(node.lifecycle.value for node in graph.nodes.values())
    edge_types = Counter(edge.edge_type.value for edge in graph.edges.values())
    return {
        "session_id": graph.session_id,
        "node_count": len(graph.nodes),
        "edge_count": len(graph.edges),
        "error_nodes": node_types["error"],
        "side_effect_nodes": sum(node.side_effect for node in graph.nodes.values()),
        "failed_with_edges": edge_types["failed_with"],
        "retry_edges": edge_types["retries"],
        "resolve_edges": edge_types["resolves"],
        "supersede_edges": edge_types["supersedes"],
        "node_type_counts": dict(sorted(node_types.items())),
        "lifecycle_counts": dict(sorted(lifecycle_states.items())),
        "edge_type_counts": dict(sorted(edge_types.items())),
        **_context_view_summary(path),
    }


def _pair_direction(reference_success: bool, comparator_success: bool) -> str:
    if reference_success and comparator_success:
        return "both_success"
    if reference_success:
        return "reference_only_success"
    if comparator_success:
        return "comparator_only_success"
    return "neither_success"


def _priority_tier(*, disagreement: bool, failure_signal: bool) -> str:
    if disagreement and failure_signal:
        return "disagreement_with_failure"
    if disagreement:
        return "disagreement"
    if failure_signal:
        return "agreement_with_failure"
    return "agreement"


def _prefix_trace(prefix: str, summary: dict[str, Any]) -> dict[str, Any]:
    values = {
        f"{prefix}_{field}": summary[field] for field in _TRACE_COUNT_FIELDS
    }
    values[f"{prefix}_session_id"] = summary["session_id"]
    values[f"{prefix}_node_type_counts"] = summary["node_type_counts"]
    values[f"{prefix}_lifecycle_counts"] = summary["lifecycle_counts"]
    values[f"{prefix}_edge_type_counts"] = summary["edge_type_counts"]
    return values


def _mean_optional(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def analyze_lifecycle_disagreements(
    report: dict[str, Any],
    *,
    project_root: Path,
    reference_manager: str = "ours_without_lifecycle_states",
    comparator_manager: str = "full_ours",
) -> dict[str, Any]:
    """Compare aligned live sessions and rank traces for label validation."""

    project_root = project_root.resolve()
    sessions = [
        row for row in report.get("sessions") or [] if isinstance(row, dict)
    ]
    by_key = {
        (
            str(row.get("manager") or ""),
            str(row.get("domain") or ""),
            str(row.get("task_id") or ""),
            int(row.get("trial") or 0),
        ): row
        for row in sessions
    }
    reference_keys = sorted(
        key[1:] for key in by_key if key[0] == reference_manager
    )
    if not reference_keys:
        raise ValueError(f"reference manager has no sessions: {reference_manager}")

    pair_rows: list[dict[str, Any]] = []
    missing_pairs: list[dict[str, Any]] = []
    selected_trace_paths: list[str] = []
    selected_seen: set[str] = set()
    for domain, task_id, trial in reference_keys:
        reference = by_key[(reference_manager, domain, task_id, trial)]
        comparator = by_key.get((comparator_manager, domain, task_id, trial))
        if comparator is None:
            missing_pairs.append(
                {"domain": domain, "task_id": task_id, "trial": trial}
            )
            continue
        reference_trace_path = project_root / str(reference.get("trace_file") or "")
        comparator_trace_path = project_root / str(comparator.get("trace_file") or "")
        if not reference_trace_path.is_file() or not comparator_trace_path.is_file():
            raise FileNotFoundError(
                "paired trace missing for "
                f"{domain}/{task_id}/trial={trial}: "
                f"{reference_trace_path}, {comparator_trace_path}"
            )
        reference_trace = _trace_summary(reference_trace_path)
        comparator_trace = _trace_summary(comparator_trace_path)
        reference_success = bool(reference.get("task_success"))
        comparator_success = bool(comparator.get("task_success"))
        disagreement = reference_success != comparator_success
        failure_signal_count = sum(
            int(summary[field])
            for summary in (reference_trace, comparator_trace)
            for field in (
                "error_nodes",
                "failed_with_edges",
                "retry_edges",
                "resolve_edges",
            )
        )
        failure_signal = failure_signal_count > 0
        tier = _priority_tier(
            disagreement=disagreement, failure_signal=failure_signal
        )
        reference_tokens = reference.get("total_selected_context_tokens")
        comparator_tokens = comparator.get("total_selected_context_tokens")
        token_delta = (
            float(comparator_tokens) - float(reference_tokens)
            if reference_tokens is not None and comparator_tokens is not None
            else None
        )
        row = {
            "domain": domain,
            "task_id": task_id,
            "trial": trial,
            "reference_manager": reference_manager,
            "comparator_manager": comparator_manager,
            "reference_success": reference_success,
            "comparator_success": comparator_success,
            "direction": _pair_direction(reference_success, comparator_success),
            "success_disagreement": disagreement,
            "reference_termination": reference.get("termination_reason"),
            "comparator_termination": comparator.get("termination_reason"),
            "reference_trace_file": str(reference.get("trace_file") or ""),
            "comparator_trace_file": str(comparator.get("trace_file") or ""),
            "selected_context_token_delta": token_delta,
            "failure_signal_count": failure_signal_count,
            "has_failure_signal": failure_signal,
            "priority_tier": tier,
            **_prefix_trace("reference", reference_trace),
            **_prefix_trace("comparator", comparator_trace),
        }
        pair_rows.append(row)
        if tier != "agreement":
            trace_file = row["comparator_trace_file"]
            if trace_file not in selected_seen:
                selected_trace_paths.append(trace_file)
                selected_seen.add(trace_file)

    tier_order = {
        "disagreement_with_failure": 0,
        "disagreement": 1,
        "agreement_with_failure": 2,
        "agreement": 3,
    }
    pair_rows.sort(
        key=lambda row: (
            tier_order[row["priority_tier"]],
            0 if row["domain"] == "retail" else 1,
            row["domain"],
            row["task_id"],
            row["trial"],
        )
    )
    selected_trace_paths.sort(
        key=lambda path: (
            next(
                tier_order[row["priority_tier"]]
                for row in pair_rows
                if row["comparator_trace_file"] == path
            ),
            0
            if next(
                row["domain"]
                for row in pair_rows
                if row["comparator_trace_file"] == path
            )
            == "retail"
            else 1,
            path,
        )
    )

    task_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in pair_rows:
        task_groups[(row["domain"], row["task_id"])].append(row)
    task_rows = []
    for (domain, task_id), rows in sorted(task_groups.items()):
        task_rows.append(
            {
                "domain": domain,
                "task_id": task_id,
                "trials": len(rows),
                "reference_successes": sum(row["reference_success"] for row in rows),
                "comparator_successes": sum(
                    row["comparator_success"] for row in rows
                ),
                "success_delta": statistics.fmean(
                    float(row["comparator_success"])
                    - float(row["reference_success"])
                    for row in rows
                ),
                "success_disagreements": sum(
                    row["success_disagreement"] for row in rows
                ),
                "reference_only_successes": sum(
                    row["direction"] == "reference_only_success" for row in rows
                ),
                "comparator_only_successes": sum(
                    row["direction"] == "comparator_only_success" for row in rows
                ),
                "failure_signal_pairs": sum(
                    row["has_failure_signal"] for row in rows
                ),
                "retry_edge_pairs": sum(
                    row["reference_retry_edges"] + row["comparator_retry_edges"] > 0
                    for row in rows
                ),
                "selected_error_pairs": sum(
                    row["reference_selected_error_items"]
                    + row["comparator_selected_error_items"]
                    > 0
                    for row in rows
                ),
                "mean_selected_context_token_delta": _mean_optional(
                    [
                        float(row["selected_context_token_delta"])
                        for row in rows
                        if row["selected_context_token_delta"] is not None
                    ]
                ),
            }
        )

    direction_counts = Counter(row["direction"] for row in pair_rows)
    priority_counts = Counter(row["priority_tier"] for row in pair_rows)
    return {
        "schema_version": "1.0",
        "matrix_id": report.get("matrix_id"),
        "reference_manager": reference_manager,
        "comparator_manager": comparator_manager,
        "counts": {
            "reference_pairs": len(reference_keys),
            "matched_pairs": len(pair_rows),
            "missing_pairs": len(missing_pairs),
            "success_disagreements": sum(
                row["success_disagreement"] for row in pair_rows
            ),
            "pairs_with_failure_signal": sum(
                row["has_failure_signal"] for row in pair_rows
            ),
            "pairs_with_retry_edges": sum(
                row["reference_retry_edges"] + row["comparator_retry_edges"] > 0
                for row in pair_rows
            ),
            "pairs_with_resolve_edges": sum(
                row["reference_resolve_edges"] + row["comparator_resolve_edges"] > 0
                for row in pair_rows
            ),
            "pairs_with_selected_error_items": sum(
                row["reference_selected_error_items"]
                + row["comparator_selected_error_items"]
                > 0
                for row in pair_rows
            ),
            "reference_unresolved_failure_reason_items": sum(
                row["reference_unresolved_failure_reason_items"]
                for row in pair_rows
            ),
            "comparator_unresolved_failure_reason_items": sum(
                row["comparator_unresolved_failure_reason_items"]
                for row in pair_rows
            ),
            "selected_comparator_traces": len(selected_trace_paths),
        },
        "direction_counts": dict(sorted(direction_counts.items())),
        "priority_tier_counts": dict(sorted(priority_counts.items())),
        "selection_policy": (
            "Select comparator traces from all success-disagreement pairs and "
            "agreement pairs containing error/failed_with/retry/resolve signals; "
            "rank disagreement+failure first, then other disagreements, then "
            "agreement+failure, with retail first inside each tier."
        ),
        "interpretation_warning": (
            "Machine lifecycle states are pseudo labels. This diagnostic ranks "
            "human-annotation candidates and measures failure coverage; it does "
            "not establish lifecycle-label correctness or causal benefit."
        ),
        "missing_pair_keys": missing_pairs,
        "selected_comparator_trace_files": selected_trace_paths,
        "task_metrics": task_rows,
        "pairs": pair_rows,
    }


def write_lifecycle_diagnostics(report: dict[str, Any], output_dir: Path) -> None:
    """Write JSON plus flat pair/task tables for audit and review."""

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "lifecycle_diagnostics.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for name, rows in (
        ("lifecycle_pairs.csv", report.get("pairs") or []),
        ("lifecycle_tasks.csv", report.get("task_metrics") or []),
    ):
        fieldnames: list[str] = []
        flat_rows = []
        for row in rows:
            flat = {
                key: (
                    json.dumps(value, ensure_ascii=False, sort_keys=True)
                    if isinstance(value, (dict, list))
                    else value
                )
                for key, value in row.items()
            }
            flat_rows.append(flat)
            for key in flat:
                if key not in fieldnames:
                    fieldnames.append(key)
        with (output_dir / name).open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            if fieldnames:
                writer.writeheader()
                writer.writerows(flat_rows)
