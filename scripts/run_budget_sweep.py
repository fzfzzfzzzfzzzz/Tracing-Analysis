"""Run a zero-API structural budget sweep over materialized TraceGraphs."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tracegraph.archive import ArchiveStore
from tracegraph.budget_sweep import choose_budget, summarize_budget
from tracegraph.experiments import ExperimentConfig, ExperimentRunner, discover_graphs


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


def _jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--budgets", type=int, nargs="+", required=True)
    parser.add_argument("--maximum-overflow-rate", type=float, default=0.05)
    args = parser.parse_args()

    budgets = sorted(set(args.budgets))
    if not budgets or any(value <= 0 for value in budgets):
        raise ValueError("budgets must contain positive integers")
    graphs = discover_graphs(args.input)
    archive = ArchiveStore(args.archive)
    summaries = []
    for budget in budgets:
        run_output = args.output / f"budget_{budget}"
        runner = ExperimentRunner(
            ExperimentConfig(
                budget=budget,
                manager_names=["full_ours"],
                online_replay=False,
                provenance="glm_stage1_mandatory_context_budget_sweep",
            ),
            archive=archive,
        )
        runner.run(graphs, run_output)
        manager_rows = _jsonl(run_output / "per_session.jsonl")
        oracle_payload = json.loads(
            (run_output / "oracle_upper_bound.json").read_text(encoding="utf-8")
        )
        summaries.append(
            summarize_budget(
                budget=budget,
                manager_rows=manager_rows,
                oracle_rows=oracle_payload["per_session"],
                maximum_overflow_rate=args.maximum_overflow_rate,
            )
        )

    recommendation = choose_budget(summaries)
    report = {
        "schema_version": "1.0",
        "graph_count": len(graphs),
        "budgets": budgets,
        "maximum_overflow_rate": args.maximum_overflow_rate,
        "recommended_budget": recommendation,
        "counterfactual_task_success_claimed": False,
        "interpretation_warning": (
            "This sweep selects a structurally feasible context budget only. "
            "Task success requires live paired runs."
        ),
        "summaries": summaries,
    }
    args.output.mkdir(parents=True, exist_ok=True)
    report_path = args.output / "budget_sweep.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
