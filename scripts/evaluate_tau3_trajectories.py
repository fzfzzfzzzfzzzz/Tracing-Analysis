"""Plan or execute offline τ³ evaluation over frozen generation artifacts.

Execution is fail-closed: without ``--execute`` the command only prints the
evaluation plan, and execution additionally requires an explicit maximum count.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from tracegraph.tau3_offline import evaluate_persisted_tau3
from tracegraph.trajectory_artifacts import EvaluationConfig, TrajectoryArtifactStore


def _parse_args_json(value: str) -> dict:
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("evaluator args must be a JSON object")
    return parsed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, required=True)
    parser.add_argument("--simulation-id", action="append", default=[])
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--evaluator-model", required=True)
    parser.add_argument(
        "--evaluator-args-json",
        type=_parse_args_json,
        default={
            "temperature": 0.0,
            "max_tokens": 512,
            "extra_body": {"thinking": {"type": "disabled"}},
        },
    )
    parser.add_argument(
        "--json-mode",
        choices=("strict", "strict_then_extract"),
        default="strict_then_extract",
    )
    parser.add_argument("--evaluation-type", default="all")
    parser.add_argument("--max-evaluations", type=int)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--summary-output", type=Path)
    args = parser.parse_args()

    if args.all == bool(args.simulation_id):
        raise SystemExit("select exactly one of --all or --simulation-id")
    store = TrajectoryArtifactStore(args.store)
    selected = store.generation_ids() if args.all else list(dict.fromkeys(args.simulation_id))
    selected = [
        simulation_id
        for simulation_id in selected
        if not (store.simulation_dir(simulation_id) / "merged.json").exists()
    ]
    config = EvaluationConfig(
        model=args.evaluator_model,
        args=args.evaluator_args_json,
        evaluation_type=args.evaluation_type,
        json_mode=args.json_mode,
    )
    plan = {
        "schema_version": "1.0",
        "execution_requested": bool(args.execute),
        "store": args.store.as_posix(),
        "simulation_ids": selected,
        "evaluation_count": len(selected),
        "evaluator": config.to_dict(),
        "warning": "Evaluation may call an external model; generation artifacts are immutable.",
    }
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    if not args.execute:
        return
    if args.max_evaluations is None or args.max_evaluations < len(selected):
        raise SystemExit("--execute requires --max-evaluations covering the selected artifacts")

    completed: list[str] = []
    errors: list[dict[str, str]] = []
    for simulation_id in selected:
        try:
            evaluate_persisted_tau3(store, simulation_id, config)
            completed.append(simulation_id)
        except Exception as error:  # the append-only attempt already contains details
            errors.append(
                {
                    "simulation_id": simulation_id,
                    "error_type": type(error).__name__,
                    "error": str(error),
                }
            )
    summary = {
        **store.summary(),
        "execution": {
            "requested": len(selected),
            "completed": completed,
            "errors": errors,
        },
    }
    if args.summary_output:
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
