"""Compute next-N-action post-failure diagnostics for a frozen τ³ matrix."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from tracegraph.graph import TraceGraph
from tracegraph.post_failure import (
    aggregate_by_condition,
    aggregate_post_failure_events,
    analyze_post_failure_windows,
)


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _context_views(trace_path: Path) -> list[dict[str, Any]]:
    path = trace_path.parent / "context_views.jsonl"
    if not path.is_file():
        return []
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        value = json.loads(line)
        if isinstance(value, dict):
            values.append(value)
    return values


def _write_csv(path: Path, rows: list[Mapping[str, Any]]) -> None:
    fields = sorted({str(key) for row in rows for key in row})
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (
                        json.dumps(value, ensure_ascii=False, sort_keys=True)
                        if isinstance(value, (dict, list))
                        else value
                    )
                    for key, value in row.items()
                }
            )


def analyze_matrix(
    *,
    plan_path: Path,
    results_root: Path,
    project_root: Path,
    output: Path,
    horizon: int,
) -> dict[str, Any]:
    plan = _read_object(plan_path)
    output.mkdir(parents=True, exist_ok=True)
    events: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    missing: list[str] = []
    input_hashes: list[dict[str, str]] = [
        {"path": plan_path.as_posix(), "sha256": _sha256(plan_path)}
    ]

    for run in plan.get("runs") or []:
        run_id = str(run["run_id"])
        result_path = results_root / str(run["save_to"]) / "results.json"
        if not result_path.is_file():
            missing.append(result_path.as_posix())
            continue
        input_hashes.append({"path": result_path.as_posix(), "sha256": _sha256(result_path)})
        result = _read_object(result_path)
        simulations = [item for item in result.get("simulations") or [] if isinstance(item, dict)]
        simulations.sort(
            key=lambda item: (
                int(item.get("trial") or 0),
                str(item.get("start_time") or ""),
                str(item.get("id") or ""),
            )
        )
        simulation_ids = {str(item.get("id") or "") for item in simulations if item.get("id")}
        trace_root = project_root / str(run["trace_output_dir"])
        trace_paths = sorted(
            trace_root.glob("*/trace.json"), key=lambda path: path.stat().st_mtime_ns
        )
        trace_by_simulation: dict[str, Path] = {}
        legacy: list[Path] = []
        for trace_path in trace_paths:
            trace_payload = _read_object(trace_path)
            simulation_id = str((trace_payload.get("metadata") or {}).get("simulation_id") or "")
            if (
                simulation_id
                and simulation_id in simulation_ids
                and simulation_id not in trace_by_simulation
            ):
                trace_by_simulation[simulation_id] = trace_path
            else:
                legacy.append(trace_path)
        legacy_iterator = iter(legacy)

        for simulation in simulations:
            simulation_id = str(simulation.get("id") or "")
            trace_path = trace_by_simulation.get(simulation_id)
            if trace_path is None:
                trace_path = next(legacy_iterator, None)
            if trace_path is None:
                missing.append(f"{run_id}:simulation={simulation_id}:trace")
                continue
            input_hashes.append({"path": trace_path.as_posix(), "sha256": _sha256(trace_path)})
            views_path = trace_path.parent / "context_views.jsonl"
            if views_path.is_file():
                input_hashes.append({"path": views_path.as_posix(), "sha256": _sha256(views_path)})
            graph = TraceGraph.load(trace_path)
            analysis = analyze_post_failure_windows(
                graph,
                messages=[
                    item for item in simulation.get("messages") or [] if isinstance(item, dict)
                ],
                context_views=_context_views(trace_path),
                horizon=horizon,
            )
            common = {
                "run_id": run_id,
                "manager": str(run["manager"]),
                "budget": str(run["budget"]),
                "domain": str(run["domain"]),
                "task_id": str(run["task_id"]),
                "trial": int(simulation.get("trial") or 0),
                "seed": simulation.get("seed"),
                "simulation_id": simulation_id,
                "trace_session_id": graph.session_id,
                "trace_file": trace_path.as_posix(),
            }
            for event in analysis["events"]:
                events.append({**common, **event})
            sessions.append(
                {
                    **common,
                    **analysis["summary"],
                    **{f"alignment_{key}": value for key, value in analysis["alignment"].items()},
                }
            )

    with (output / "events.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    _write_csv(output / "sessions.csv", sessions)
    report = {
        "schema_version": "1.0",
        "analysis": "next_n_agent_actions_after_actionable_failure",
        "matrix_id": plan.get("matrix_id"),
        "horizon": horizon,
        "session_count": len(sessions),
        "event_count": len(events),
        "complete": not missing and len(sessions) == int(plan.get("session_count") or 0),
        "missing": missing,
        "overall": aggregate_post_failure_events(events),
        "by_condition": aggregate_by_condition(events),
        "input_manifest": input_hashes,
        "interpretation_warning": (
            "This is a post-hoc diagnostic over non-common-prefix natural trajectories. "
            "It must not be interpreted as a causal Card effect."
        ),
    }
    (output / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--results-root", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path("."))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--horizon", type=int, default=3)
    args = parser.parse_args()
    report = analyze_matrix(
        plan_path=args.plan,
        results_root=args.results_root,
        project_root=args.project_root.resolve(),
        output=args.output,
        horizon=args.horizon,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
