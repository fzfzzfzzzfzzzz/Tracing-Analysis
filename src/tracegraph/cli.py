"""检查、导入和整理工具使用记录的主命令。"""

from __future__ import annotations

import json
from pathlib import Path

from .adapters import TauTraceImporter
from .archive import ArchiveStore
from .benchmark.compression_audit.dataset import build_benchmark, validate_benchmark
from .benchmark.compression_audit.live import prepare_live_run, run_live_v0
from .benchmark.compression_audit.metrics import score_run
from .benchmark.compression_audit.runtime import RANKED_REFERENCE_METHODS, run_deterministic
from .context_engine.context import build_context_managers
from .experiments import ExperimentConfig, ExperimentRunner, discover_graphs
from .graph import TraceGraph
from .evaluation.interventions import InterventionConfig, run_p1_interventions
from .plain_cli import PlainArgumentParser
from .synthetic import build_synthetic_trace


def build_parser() -> PlainArgumentParser:
    parser = PlainArgumentParser(
        prog="tracegraph",
        description="检查、导入或整理已经保存的工具使用记录。",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-trace", help="检查一份工具使用记录是否完整")
    validate.add_argument("path", type=Path, help="要检查的记录文件")

    archive = subparsers.add_parser("verify-archive", help="检查单独保存的旧记录及其文件指纹")
    archive.add_argument("path", type=Path, help="旧记录所在目录")

    subparsers.add_parser("list-managers", help="列出可以使用的记录整理办法")

    synthetic = subparsers.add_parser(
        "make-synthetic", help="生成一份项目自带的示例记录，用来检查程序能否运行"
    )
    synthetic.add_argument("--output", type=Path, required=True)
    synthetic.add_argument("--archive", type=Path, required=True)

    tau = subparsers.add_parser(
        "import-tau", help="导入 tau-bench 或 tau3-bench 公开测试任务的保存结果"
    )
    tau.add_argument("--input", type=Path, required=True)
    tau.add_argument("--output", type=Path, required=True)
    tau.add_argument("--archive", type=Path, required=True)
    tau.add_argument("--policy-file", type=Path)

    experiment = subparsers.add_parser(
        "run-offline", help="不调用外部模型，比较多种记录整理办法"
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
        help="运行第三阶段四种固定条件的本地比较",
    )
    interventions.add_argument("--output", type=Path, required=True)
    interventions.add_argument("--tasks-per-kind", type=int, default=8)
    interventions.add_argument("--base-seed", type=int, default=4100)
    interventions.add_argument("--budget", type=int, default=512)

    benchmark_build = subparsers.add_parser(
        "benchmark-build",
        help="构建失败历史压缩测试集，不调用外部模型",
    )
    benchmark_build.add_argument("--config", type=Path, required=True)
    benchmark_build.add_argument("--output", type=Path, required=True)

    benchmark_validate = subparsers.add_parser(
        "benchmark-validate",
        help="检查失败历史压缩测试集及正式发布门槛",
    )
    benchmark_validate.add_argument("--dataset", type=Path, required=True)
    benchmark_validate.add_argument("--require-v1", action="store_true")

    benchmark_run = subparsers.add_parser(
        "benchmark-run",
        help="运行确定性检查或受费用上限保护的真实模型试跑",
    )
    benchmark_run.add_argument("--config", type=Path, required=True)
    benchmark_run.add_argument("--dataset", type=Path, required=True)
    benchmark_run.add_argument("--output", type=Path, required=True)
    benchmark_run.add_argument(
        "--mode",
        choices=("deterministic", "prepare-live", "live"),
        default="deterministic",
    )
    benchmark_run.add_argument("--legacy", action="store_true")
    benchmark_run.add_argument("--method", action="append", default=[])
    benchmark_run.add_argument("--query-type", action="append", default=[])
    benchmark_run.add_argument("--max-new-requests", type=int)
    benchmark_run.add_argument("--resume", action="store_true")
    benchmark_run.add_argument(
        "--live-authorization-id",
        help="仅 live 模式使用；必须与新授权配置中的一次性编号完全一致",
    )

    benchmark_score = subparsers.add_parser(
        "benchmark-score",
        help="按源前缀配对计算审计损失和实际重获成本",
    )
    benchmark_score.add_argument("--dataset", type=Path, required=True)
    benchmark_score.add_argument("--run", type=Path, required=True)
    benchmark_score.add_argument("--output", type=Path, required=True)
    benchmark_score.add_argument("--bootstrap-samples", type=int, default=10000)
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
    if args.command == "benchmark-build":
        manifest = build_benchmark(args.config, args.output, workspace=Path.cwd())
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0
    if args.command == "benchmark-validate":
        report = validate_benchmark(args.dataset)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        if not report["diagnostic_ready"]:
            return 1
        return 1 if args.require_v1 and not report["v1_ready"] else 0
    if args.command == "benchmark-run":
        if args.mode == "deterministic":
            report = run_deterministic(
                args.dataset,
                args.output,
                methods=tuple(args.method) or RANKED_REFERENCE_METHODS,
                legacy=args.legacy,
                query_types=tuple(args.query_type) or None,
                config_path=args.config,
            )
        elif args.mode == "prepare-live":
            report = prepare_live_run(args.config, args.dataset, args.output)
        else:
            report = run_live_v0(
                args.config,
                args.dataset,
                args.output,
                workspace=Path.cwd(),
                max_new_requests=args.max_new_requests,
                resume=args.resume,
                authorization_id=args.live_authorization_id,
            )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    if args.command == "benchmark-score":
        report = score_run(
            args.dataset,
            args.run,
            args.output,
            bootstrap_samples=args.bootstrap_samples,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    return 2
