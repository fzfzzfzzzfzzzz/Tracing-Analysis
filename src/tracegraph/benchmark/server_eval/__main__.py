"""Portable CLI: python -m tracegraph.benchmark.server_eval --help."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.request import urlopen

from ...plain_cli import PlainArgumentParser, run_cli

from ..compression_audit.development_experiment import write_json
from ..compression_audit.io import file_sha256
from .config import load_config
from .external import verify_source
from .prepare import prepare
from .report import rescore, rescore_gold_migration
from .runner import run


def main():
    parser = PlainArgumentParser(description="冻结并运行自托管模型的记忆评测矩阵，默认只运行离线夹具。")
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--config", type=Path, required=True)
    prep.add_argument("--dataset", type=Path, required=True)
    prep.add_argument("--output", type=Path, required=True)
    execute = sub.add_parser("run")
    execute.add_argument("--prepared", type=Path, required=True)
    execute.add_argument("--dataset", type=Path, required=True)
    execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--mode", choices=("offline", "live"), default="offline")
    execute.add_argument("--execute", action="store_true", help="Explicitly start self-hosted inference")
    execute.add_argument("--resume", action="store_true")
    execute.add_argument("--max-new-jobs", type=int)
    execute.add_argument("--assume-gates-passed", action="store_true",
        help="Development-only override: run the full matrix while preserving failed raw gates")
    score = sub.add_parser("score")
    score.add_argument("--prepared", type=Path, required=True)
    score.add_argument("--run", type=Path, required=True)
    score.add_argument("--output", type=Path, required=True)
    score.add_argument("--dataset", type=Path,
        help="Rebuild rubrics from this frozen dataset before a zero-call rescore")
    score.add_argument("--rubrics", type=Path,
        help="Apply externally reviewed rubric rows; requires --dataset and makes no model calls")
    migrated_score = sub.add_parser(
        "score-gold-migration",
        help="Rescore saved answers against a manifest-linked development gold child dataset",
    )
    migrated_score.add_argument("--prepared", type=Path, required=True)
    migrated_score.add_argument("--run", type=Path, required=True)
    migrated_score.add_argument("--source-dataset", type=Path, required=True)
    migrated_score.add_argument("--target-dataset", type=Path, required=True)
    migrated_score.add_argument("--rubrics", type=Path)
    migrated_score.add_argument("--output", type=Path, required=True)
    judge_review = sub.add_parser("judge-review-export",
        help="匿名导出真实模型校准答案，供两位审阅者独立盲审")
    judge_review.add_argument("--prepared", type=Path, required=True)
    judge_review.add_argument("--run", type=Path, required=True)
    judge_review.add_argument("--output", type=Path, required=True)
    judge_import = sub.add_parser("judge-review-import",
        help="导入两份独立盲审，输出分歧、仲裁项和真实答案裁判门禁")
    judge_import.add_argument("--packet", type=Path, required=True)
    judge_import.add_argument("--reviews", type=Path, nargs=2, required=True)
    judge_import.add_argument("--adjudication", type=Path)
    judge_import.add_argument("--output", type=Path, required=True)
    fetch = sub.add_parser("fetch-sources", help="Download pinned public source files, no model requests")
    fetch.add_argument("--config", type=Path, required=True)
    bind = sub.add_parser("bind-model", help="Bind a model slot and fingerprint local tokenizer files")
    bind.add_argument("--config", type=Path, required=True)
    for flag in ("model-id", "base-url", "served-model", "weights-revision", "server-version"):
        bind.add_argument("--" + flag, required=True)
    bind.add_argument("--tokenizer-dir", type=Path, required=True)
    bind.add_argument("--output", type=Path, required=True)
    bind.add_argument("--runtime-receipt", type=Path)
    for name, flags in {
        "record-runtime": ("spec", "weights", "tokenizer", "output"),
        "review-export": ("dataset", "output"),
        "import-data": ("source", "rubrics", "provenance", "output", "exposure"),
        "tune-prepare": ("config", "dataset", "output"),
        "tune-select": ("plan", "runs", "output"),
        "mini-prepare": ("config", "tasks", "output"),
        "export-mini-trace": ("run", "output"),
    }.items():
        item = sub.add_parser(name)
        for flag in flags:
            item.add_argument("--" + flag, type=Path, required=True)
    mini = sub.add_parser("mini-run")
    for flag in ("prepared", "output", "restore"):
        mini.add_argument("--" + flag, type=Path, required=flag != "restore")
    for flag in ("task-id", "model-id", "method"):
        mini.add_argument("--" + flag, required=True)
    mini.add_argument("--budget", type=int, required=True)
    mini.add_argument("--execute", action="store_true")
    mini.add_argument("--calibration", type=Path, required=True)
    mini.add_argument("--memory-ledger", type=Path,
        help="内容寻址的递归记忆 ledger 快照")
    mini.add_argument("--memory-revision", type=Path,
        help="内容寻址的单个 revision 快照，用于候选更新的隔离回放")
    mini.add_argument("--memory-budget", type=int, default=0,
        help="从总历史预算中预留给失败经验记忆的 token 数")
    mini_probe = sub.add_parser("mini-probe")
    mini_probe.add_argument("--config", type=Path, required=True)
    mini_probe.add_argument("--model-id", required=True)
    mini_probe.add_argument("--output", type=Path, required=True)
    mini_probe.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    workspace = Path.cwd()
    if args.command == "export-mini-trace":
        from .data_workflow import export_mini_trace
        result = export_mini_trace(args.run, args.output)
    elif args.command == "judge-review-export":
        from .judge_review import export_real_answer_review
        result = export_real_answer_review(args.prepared, args.run, args.output)
    elif args.command == "judge-review-import":
        from .judge_review import import_real_answer_reviews
        result = import_real_answer_reviews(args.packet, args.reviews, args.output,
                                            adjudication=args.adjudication)
    elif args.command == "record-runtime":
        from .runtime import record_runtime
        result = record_runtime(args.spec, args.weights, args.tokenizer, args.output)
    elif args.command == "review-export":
        from .data_workflow import review_export
        result = review_export(args.dataset, args.output)
    elif args.command == "import-data":
        from .data_workflow import import_dataset
        result = import_dataset(args.source, args.rubrics, args.provenance, args.output, args.exposure)
    elif args.command == "tune-prepare":
        from .tuning import prepare_tuning
        result = prepare_tuning(args.config, args.dataset, args.output, workspace)
    elif args.command == "tune-select":
        from .tuning import select_guidance
        result = select_guidance(args.plan, args.runs, args.output)
    elif args.command == "mini-prepare":
        from .mini_runner import prepare_mini
        result = prepare_mini(args.config, args.tasks, args.output, workspace)
    elif args.command == "mini-probe":
        from .mini_runner import calibrate_mini_agent
        result = calibrate_mini_agent(
            args.config, args.output, workspace,
            model_id=args.model_id, execute=args.execute,
        )
    elif args.command == "mini-run":
        from .mini_runner import execute_mini
        result = execute_mini(args.prepared, args.output, workspace, task_id=args.task_id,
            model_id=args.model_id, method=args.method, budget=args.budget,
            execute=args.execute, restore=args.restore, calibration=args.calibration,
            memory_ledger=args.memory_ledger, memory_revision=args.memory_revision,
            memory_budget=args.memory_budget)
    elif args.command == "prepare":
        result = prepare(args.config, args.dataset, args.output, workspace)
    elif args.command == "run":
        result = run(args.prepared, args.dataset, args.output, workspace, mode=args.mode,
                     execute=args.execute, resume=args.resume, max_new_jobs=args.max_new_jobs,
                     assume_gates_passed=args.assume_gates_passed)
    elif args.command == "score":
        result = rescore(
            args.prepared, args.run, args.output,
            dataset=args.dataset, external_rubrics=args.rubrics,
        )
    elif args.command == "score-gold-migration":
        result = rescore_gold_migration(
            args.prepared, args.run, args.source_dataset, args.target_dataset,
            args.output, external_rubrics=args.rubrics,
        )
    elif args.command == "bind-model":
        config = load_config(args.config)
        if args.output.exists():
            raise ValueError("bound config output must be new")
        model = next(m for m in config["models"] if m["id"] == args.model_id)
        root = args.tokenizer_dir.resolve()
        files = {p.relative_to(root).as_posix(): file_sha256(p) for p in root.rglob("*") if p.is_file()}
        if not {"tokenizer.json", "tokenizer_config.json"} <= files.keys():
            raise ValueError("provide a tokenizer-only directory with pinned chat-template files")
        if any(p.stat().st_size > 100_000_000 for p in root.rglob("*") if p.is_file()):
            raise ValueError("use a tokenizer-only directory, not a weight directory")
        model.update(base_url=args.base_url, served_model=args.served_model,
            returned_model_allowlist=[args.served_model], weights_revision=args.weights_revision,
            server_version=args.server_version, tokenizer={"path": str(root), "files": files})
        if args.runtime_receipt:
            model["runtime_receipt"] = {"path": str(args.runtime_receipt.resolve()),
                                        "sha256": file_sha256(args.runtime_receipt)}
        write_json(args.output, config)
        result = {"bound_model": args.model_id, "output": str(args.output), "provider_requests": 0}
    else:
        config = load_config(args.config)
        for spec in config["sources"].values():
            root = (workspace / spec["path"]).resolve()
            if not root.is_relative_to((workspace / "vendor").resolve()):
                raise ValueError("source downloads must stay inside vendor")
            for relative, digest in spec["files"].items():
                path = (root / relative).resolve()
                if not path.is_relative_to(root):
                    raise ValueError("source path escapes vendor")
                if path.exists():
                    if file_sha256(path) != digest:
                        raise ValueError("existing source differs; refusing overwrite")
                    continue
                url = f"https://raw.githubusercontent.com/{spec['github_repo']}/{spec['revision']}/{relative}"
                with urlopen(url, timeout=30) as response:
                    data = response.read(32_000_001)
                import hashlib
                if len(data) > 32_000_000 or hashlib.sha256(data).hexdigest() != digest:
                    raise ValueError("downloaded source identity differs")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            verify_source(spec, workspace)
        result = {"sources_verified": True, "provider_requests": 0}
    compact = {k: v for k, v in result.items() if k not in (
        "implementation", "cells", "stage_usage", "deployment_usage_by_method", "failure_counts")}
    print(json.dumps(compact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    raise SystemExit(run_cli(main))
