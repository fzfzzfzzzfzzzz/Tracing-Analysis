"""Command-line entrypoint for graph validation and archive checks."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adapters import TauTraceImporter
from .archive import ArchiveStore
from .context import build_context_managers
from .experiments import ExperimentConfig, ExperimentRunner, discover_graphs
from .graph import TraceGraph
from .interventions import InterventionConfig, run_p1_interventions
from .synthetic import build_synthetic_trace


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tracegraph")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-trace", help="validate a saved trace graph")
    validate.add_argument("path", type=Path)

    archive = subparsers.add_parser("verify-archive", help="verify all archive object hashes")
    archive.add_argument("path", type=Path)

    subparsers.add_parser("list-managers", help="list baselines and ablations")

    synthetic = subparsers.add_parser(
        "make-synthetic", help="create a labeled synthetic trace for smoke testing"
    )
    synthetic.add_argument("--output", type=Path, required=True)
    synthetic.add_argument("--archive", type=Path, required=True)

    tau = subparsers.add_parser("import-tau", help="import τ-bench/τ³-bench saved results")
    tau.add_argument("--input", type=Path, required=True)
    tau.add_argument("--output", type=Path, required=True)
    tau.add_argument("--archive", type=Path, required=True)
    tau.add_argument("--policy-file", type=Path)

    experiment = subparsers.add_parser(
        "run-offline", help="run lifecycle, oracle, baseline, and ablation experiments"
    )
    experiment.add_argument("--input", type=Path, required=True)
    experiment.add_argument("--output", type=Path, required=True)
    experiment.add_argument("--archive", type=Path)
    experiment.add_argument("--budget", type=int, default=2048)
    experiment.add_argument("--last-k", type=int, default=8)
    experiment.add_argument("--manager", action="append", default=[])
    experiment.add_argument("--no-online-replay", action="store_true")
    experiment.add_argument("--provenance", default="cli")

    interventions = subparsers.add_parser(
        "run-p1-interventions",
        help="run the deterministic four-condition phase-three P1 matrix",
    )
    interventions.add_argument("--output", type=Path, required=True)
    interventions.add_argument("--tasks-per-kind", type=int, default=8)
    interventions.add_argument("--base-seed", type=int, default=4100)
    interventions.add_argument("--budget", type=int, default=512)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "validate-trace":
        graph = TraceGraph.load(args.path)
        errors = graph.validate()
        print(json.dumps({"valid": not errors, "errors": errors}, ensure_ascii=False, indent=2))
        return 0 if not errors else 1
    if args.command == "verify-archive":
        failures = ArchiveStore(args.path).verify_all()
        print(json.dumps({"valid": not failures, "failures": failures}, indent=2))
        return 0 if not failures else 1
    if args.command == "list-managers":
        print(json.dumps(sorted(build_context_managers()), indent=2))
        return 0
    if args.command == "make-synthetic":
        graph = build_synthetic_trace(ArchiveStore(args.archive))
        graph.save(args.output)
        print(json.dumps({"output": str(args.output), "session_id": graph.session_id}, indent=2))
        return 0
    if args.command == "import-tau":
        policy = args.policy_file.read_text(encoding="utf-8") if args.policy_file else None
        importer = TauTraceImporter(ArchiveStore(args.archive))
        graphs = importer.import_path(args.input, policy=policy)
        args.output.mkdir(parents=True, exist_ok=True)
        for graph in graphs:
            graph.save(args.output / f"{graph.session_id}.json")
        print(json.dumps({"imported": len(graphs), "output": str(args.output)}, indent=2))
        return 0
    if args.command == "run-offline":
        archive_store = ArchiveStore(args.archive) if args.archive else None
        runner = ExperimentRunner(
            ExperimentConfig(
                budget=args.budget,
                manager_names=args.manager,
                online_replay=not args.no_online_replay,
                last_k=args.last_k,
                provenance=args.provenance,
            ),
            archive=archive_store,
        )
        manifest = runner.run(discover_graphs(args.input), args.output)
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    if args.command == "run-p1-interventions":
        manifest = run_p1_interventions(
            args.output,
            config=InterventionConfig(
                tasks_per_kind=args.tasks_per_kind,
                base_seed=args.base_seed,
                budget=args.budget,
            ),
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    return 2
