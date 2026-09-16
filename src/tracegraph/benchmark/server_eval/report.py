"""Cell-level scores, prefix-cluster paired intervals, and complete coverage."""

from __future__ import annotations

import copy
import random
from collections import Counter, defaultdict
from pathlib import Path

from ..compression_audit.development_experiment import write_json, write_rows
from ..compression_audit.development_results import judge_gate, model_gate, write_results
from ..compression_audit.development_scoring import score_submission
from ..compression_audit.io import load_jsonl, stable_digest


def paired_interval(rows, reference, metric, seed, draws=2000):
    """Resample prefixes, keeping the correlated questions in a prefix together."""
    clusters = defaultdict(list)
    for row in rows:
        base = reference.get(row["query_id"])
        if base and row["score"].get(metric) is not None and base["score"].get(metric) is not None:
            clusters[row["prefix_id"]].append(float(row["score"][metric]) - float(base["score"][metric]))
    if not clusters:
        return {"pairs": 0, "prefixes": 0, "difference": None, "ci95": None}
    values = list(clusters.values())
    flattened = [x for v in values for x in v]
    rng = random.Random(seed)
    boot = []
    for _ in range(draws):
        sample = [x for _ in values for x in rng.choice(values)]
        boot.append(sum(sample) / len(sample))
    boot.sort()
    return {"pairs": len(flattened), "prefixes": len(values),
            "difference": sum(flattened) / len(flattened),
            "ci95": [boot[int(draws * .025)], boot[min(draws - 1, int(draws * .975))]],
            "resampling_unit": "prefix", "exploratory_no_multiple_comparison_correction": True}


def write_report(output: Path, jobs: list, ledger: list, trials: list, *, config, mode, stop,
                 assume_gates_passed=False):
    examples = [r["result"] for r in jobs if r["kind"] == "judge_example"]
    episodes = copy.deepcopy([r["result"] for r in jobs if r["kind"] == "episode"])
    interrupted_path = output / "interrupted_jobs.jsonl"
    if interrupted_path.exists():
        episodes += [r["result"] for r in load_jsonl(interrupted_path)]
    cells = sorted({t["cell_id"] for t in trials})
    summaries, comparisons = [], []
    for cell in cells:
        planned = [t for t in trials if t["cell_id"] == cell]
        rows = [e for e in episodes if e["cell_id"] == cell]
        calibration = [e for e in rows if e["phase"] == "calibration"]
        gate = model_gate(calibration, config["gates"])
        destination = output / "cells" / cell
        destination.mkdir(parents=True, exist_ok=True)
        # Existing protocol/content/evidence/causal/safety failure table, per cell.
        cell_ledger = [r for r in ledger if r["job_id"].startswith(cell + ":")
                       or r["job_id"].startswith("build:" + cell + ":")]
        report = write_results(destination, rows, examples, cell_ledger,
                               config=config, mode=mode, stop_reason=stop)
        summary = {"cell_id": cell, "model_id": planned[0]["model_id"], "budget": planned[0]["budget"],
                   "planned": len(planned), "completed": len([e for e in rows if not e.get("incomplete")]),
                   "calibration": gate, "summaries": report["summaries"],
                   "interactive_analysis": report["interactive_analysis"],
                   "failure_stage_counts": report["failure_stage_counts"],
                   "calibration_effective_pass": gate["pass"] or bool(assume_gates_passed),
                   "main_skipped_after_calibration": (
                       len(calibration) == 40 and not gate["pass"] and not assume_gates_passed)}
        summaries.append(summary)
        for reference_method in ("full_history", "flat_bm25_archive", "tracegraph_0_4"):
            reference = {e["query_id"]: e for e in rows if e["phase"] == "main"
                         and e["method_id"] == reference_method}
            for method in config["methods"]:
                if method == reference_method:
                    continue
                candidate = [e for e in rows if e["phase"] == "main" and e["method_id"] == method]
                for metric in ("structured_output_valid", "hard_pass", "audit_pass", "safety_pass",
                               "evidence_precision", "evidence_recall", "causal_constraint_rate",
                               "strict_chain_recovered"):
                    comparisons.append({"cell_id": cell, "method": method, "reference": reference_method,
                        "metric": metric, **paired_interval(candidate, reference, metric, config["seed"])})
    stages = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0, "seconds": 0})
    owners = {t["episode_id"]: (t["cell_id"], t["method_id"]) for t in trials}
    owners.update({f"build:{t['cell_id']}:{t['prefix_id']}:{t['method_id']}":
                   (t["cell_id"], t["method_id"]) for t in trials})
    method_usage = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                        "seconds": 0})
    main_jobs = {t["episode_id"] for t in trials if t["phase"] == "main"}
    main_jobs.update(f"build:{t['cell_id']}:{t['prefix_id']}:{t['method_id']}"
                     for t in trials if t["phase"] == "main")
    main_usage = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "usage_complete": True})
    for row in ledger:
        stage = stages[row["kind"]]
        stage["calls"] += 1
        stage["input_tokens"] += row.get("prompt_tokens") or 0
        stage["output_tokens"] += row.get("completion_tokens") or 0
        stage["seconds"] += row.get("latency_seconds") or 0
        if row["kind"] != "judge" and row["job_id"] in owners:
            owner = owners[row["job_id"]]
            cost = method_usage[(*owner, row["kind"])]
            cost["calls"] += 1
            cost["input_tokens"] += row.get("prompt_tokens") or 0
            cost["output_tokens"] += row.get("completion_tokens") or 0
            cost["seconds"] += row.get("latency_seconds") or 0
            if row["job_id"] in main_jobs:
                main_usage[owner]["input_tokens"] += row.get("prompt_tokens") or 0
                main_usage[owner]["output_tokens"] += row.get("completion_tokens") or 0
                if not row.get("valid_usage"):
                    main_usage[owner]["usage_complete"] = False
    interpretation = ("offline_wiring_only" if mode == "offline" else
        "development_comparison_gate_override" if assume_gates_passed else "development_comparison")
    summary = {"schema_version": "server_eval_results_v1", "mode": mode,
        "interpretation": interpretation,
        "gate_override": {"enabled": bool(assume_gates_passed),
            "scope": "rule_judge_and_model_calibration",
            "raw_gates_remain_authoritative": True,
            "development_only": True},
        "stop_reason": stop, "judge_gate": judge_gate(examples, config["gates"]),
        "cells": summaries, "planned_episodes": len(trials), "completed_episodes": sum(
            s["completed"] for s in summaries), "provider_requests": len(ledger),
        "stage_usage": dict(stages), "hardware_cost_cny": None, "hardware_cost_known": False,
        "deployment_usage_by_method": [{"cell_id": c, "method_id": m, "stage": s, **v}
                                        for (c, m, s), v in sorted(method_usage.items())],
        "cost_note": "Self-hosted token usage is measured; hardware/electricity cost is unknown, not zero.",
        "independent_validation": False, "development_only": True,
        "failure_counts": dict(Counter(label for e in episodes for label in e["score"]["failure_labels"])),
        "failure_stage_counts": dict(Counter(stage for e in episodes
            for stage in e["score"].get("failure_stages", ()))),
        "answer_contract_revisions": sorted({e.get("answer_contract_revision", "unversioned")
                                             for e in episodes}),
        "interaction_policy_revisions": sorted({e.get(
            "interaction_policy_revision", "unversioned") for e in episodes}),
        "single_aggregate_score": None}
    write_rows(output / "episodes.jsonl", episodes)
    write_rows(output / "paired_comparisons.jsonl", comparisons)
    write_json(output / "report.json", summary)
    from .analysis import budget_frontiers
    write_json(output / "budget_frontiers.json", budget_frontiers(
        episodes, [{"cell_id": c, "method_id": m, **v} for (c, m), v in main_usage.items()]))
    text = ["# 服务器评测矩阵", "", "所有结果仍为开发用途；规则样例校准不等于人工标定。",
            f"模式：{mode}；已完成 {summary['completed_episodes']}/{len(trials)}；模型请求 {len(ledger)}。",
            f"停止原因：{stop or '计划内阶段完成；检查各单元是否因校准未过而跳过'}。", "",
            "|单元|预算|完成/计划|模型校准|", "|---|---:|---:|---|"]
    text += [f"|{s['cell_id']}|{s['budget']}|{s['completed']}/{s['planned']}|"
             f"{'通过' if s['calibration']['pass'] else '未通过或未完成'}|" for s in summaries]
    text += ["", "离线模式仅验证代码路径，外部方法夹具不代表正式算法成绩。",
             "正式方法版本、压缩/恢复额外调用、档案读入、未装入内容见各 job 和 provider 账本。",
             "各单元的逐题失败表与分项指标在 cells/；配对置信区间按前缀重采样，不是排行榜。",
             "裁判费用/调用单列。自托管硬件费用未知，不能将零 API 标价写成零部署成本。",
             "本报告只比较固定最终上下文预算；外部方法额外计算权限须结合各方法 profile 阅读。"]
    if assume_gates_passed:
        text += ["", "警告：本次运行显式覆盖了规则裁判与模型校准门禁。原始门禁结果仍保留；",
                 "以下矩阵只能用于探索和人工复核，不能解释为门禁通过后的正式开发比较。"]
    (output / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return summary


def rescore(
    prepared: Path,
    run: Path,
    output: Path,
    *,
    dataset: Path | None = None,
    external_rubrics: Path | None = None,
):
    import json
    from ..compression_audit.build import verify_file_manifest
    from ..compression_audit.artifacts import load_dataset
    from ..compression_audit.io import file_sha256
    from ..compression_audit.development_scoring import (
        RUBRIC_VERSION, SCORING_REVISION, make_rubric,
    )
    from .rubrics import import_rubrics
    from .config import load_config
    verify_file_manifest(prepared)
    prepared_manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
    identity = json.loads((run / "identity.json").read_text(encoding="utf-8"))
    if identity["prepared_hash"] != file_sha256(prepared / "manifest.json"):
        raise ValueError("prepared run identity differs")
    if output.exists():
        raise ValueError("rescore output must be new")
    jobs = load_jsonl(run / "completed_jobs.jsonl")
    rubrics = {r["query_id"]: r for r in load_jsonl(prepared / "rubrics.jsonl")}
    config = load_config(prepared / "config.snapshot.json")
    queries = {}
    rubric_source = "prepared_package"
    rubric_dataset_manifest_sha256 = None
    rubric_dataset_file_manifest_sha256 = None
    external_rubrics_sha256 = None
    if external_rubrics is not None and dataset is None:
        raise ValueError("external rubric rescore requires the frozen dataset")
    if dataset is not None:
        if config["data"].get("rubrics") or config["data"].get("calibration_dataset"):
            raise ValueError("dataset rubric rebuild does not support external rubric/calibration inputs")
        verify_file_manifest(dataset)
        rubric_dataset_manifest_sha256 = file_sha256(dataset / "manifest.json")
        if rubric_dataset_manifest_sha256 != prepared_manifest["dataset_manifest_sha256"]:
            raise ValueError("rescore dataset differs from the prepared dataset")
        rubric_dataset_file_manifest_sha256 = file_sha256(dataset / "file_manifest.jsonl")
        prefix_rows, query_rows, gold_rows = load_dataset(dataset, legacy=False)
        queries = {query.query_id: query for query in query_rows}
        gold = {item.prefix_id: item for item in gold_rows}
        prefixes = {item.prefix_id: item for item in prefix_rows}
        missing = set(rubrics) - set(queries)
        if missing:
            raise ValueError("rescore dataset lacks prepared queries")
        if external_rubrics is None:
            rebuilt = {
                query_id: make_rubric(
                    queries[query_id], gold[queries[query_id].prefix_id]
                ) for query_id in rubrics
            }
            rubric_source = "frozen_dataset_rebuild"
        else:
            external_ids = {
                row["query_id"] for row in load_jsonl(external_rubrics)
            }
            unknown = external_ids - set(rubrics)
            if unknown:
                raise ValueError("external rubrics contain queries outside the prepared package")
            selected_queries = [queries[query_id] for query_id in rubrics]
            selected_prefixes = {
                query.prefix_id: prefixes[query.prefix_id] for query in selected_queries
            }
            rebuilt = import_rubrics(
                external_rubrics, selected_queries, selected_prefixes, gold,
            )
            external_rubrics_sha256 = file_sha256(external_rubrics)
            rubric_source = "external_rubric_overlay"
        immutable_fields = {
            "query_id", "gold_hash", "strict_values", "necessary_facts",
            "contradictory_facts", "expected_scope", "strict_chain",
        }
        for query_id, old in rubrics.items():
            old_fixed = {key: old.get(key) for key in immutable_fields}
            new_fixed = {key: rebuilt[query_id].get(key) for key in immutable_fields}
            if old_fixed != new_fixed:
                raise ValueError("rebuilt rubric changes facts or query identity")
        rubrics = rebuilt
    examples = [r["result"] for r in jobs if r["kind"] == "judge_example"]
    assumed = bool(identity.get("assume_gates_passed", False))
    calibrated = judge_gate(examples, config["gates"])["pass"] or assumed
    for job in jobs:
        if stable_digest(job["result"]) != job["result_hash"]:
            raise ValueError("saved job altered")
        if job["kind"] == "episode":
            row = job["result"]
            rubric = rubrics[row["query_id"]]
            row["score"] = score_submission(row["answer"], rubric,
                row["final_visible_ids"], judge=row["judge"], judge_calibrated=calibrated,
                status=row["status"], executed_side_effects=sum(bool(t.get("executed_side_effect"))
                for t in row["tool_calls"]), unsafe_attempts=sum(bool(t.get("unsafe_side_effect_attempt"))
                for t in row["tool_calls"]))
            initial_visible = set(row["artifact"]["visible_event_ids"])
            initial_support = any(set(ids) <= initial_visible
                                  for ids in rubric["alternative_evidence_sets"])
            row["initial_necessary_evidence_present"] = initial_support
            row["reacquisition_opportunity"] = (
                row["track"] == "interactive_reacquisition"
                and not initial_support and row["recoverability"] != "R0")
            row["unrecoverable_initial_context"] = (
                row["track"] == "interactive_reacquisition"
                and not initial_support and row["recoverability"] == "R0")
            if row["query_id"] in queries:
                from ..compression_audit.development_runner import (
                    public_evidence_roles_supported,
                )
                row["initial_public_evidence_roles_supported"] = (
                    public_evidence_roles_supported(
                        queries[row["query_id"]], row["artifact"]["records"]))
    ledger_file = run / "provider_ledger.jsonl"
    ledger = load_jsonl(ledger_file) if ledger_file.exists() else []
    for row in ledger:
        if stable_digest(row["response"]) != row["response_hash"]:
            raise ValueError("provider response altered")
    output.mkdir(parents=True)
    if external_rubrics is not None:
        write_rows(output / "rubrics.jsonl", [rubrics[key] for key in sorted(rubrics)])
    if (run / "interrupted_jobs.jsonl").exists():
        (output / "interrupted_jobs.jsonl").write_bytes((run / "interrupted_jobs.jsonl").read_bytes())
    report = write_report(output, jobs, ledger, load_jsonl(prepared / "trials.jsonl"),
                        config=config, mode=identity["mode"], stop="offline_rescore_no_new_calls",
                        assume_gates_passed=assumed)
    code_files = [Path(__file__).resolve(),
        Path(__file__).resolve().parents[1] / "compression_audit" / "development_scoring.py",
        Path(__file__).resolve().parents[1] / "compression_audit" / "development_results.py"]
    code_hashes = {path.name: file_sha256(path) for path in code_files}
    rescore_identity = {"schema_version": "server_eval_rescore_identity_v3",
        "prepared_manifest_sha256": file_sha256(prepared / "manifest.json"),
        "rubric_source": rubric_source,
        "rubric_version": RUBRIC_VERSION,
        "scoring_revision": SCORING_REVISION,
        "rubric_dataset_manifest_sha256": rubric_dataset_manifest_sha256,
        "rubric_dataset_file_manifest_sha256": rubric_dataset_file_manifest_sha256,
        "external_rubrics_sha256": external_rubrics_sha256,
        "rescored_rubrics_sha256": (
            file_sha256(output / "rubrics.jsonl") if external_rubrics is not None else None
        ),
        "source_report_sha256": file_sha256(run / "report.json"),
        "source_episodes_sha256": file_sha256(run / "episodes.jsonl"),
        "rescored_episodes_sha256": file_sha256(output / "episodes.jsonl"),
        "scoring_code_hashes": code_hashes, "scoring_code_digest": stable_digest(code_hashes),
        "new_provider_requests": 0, "development_only": True,
        "independent_validation": False,
        "post_hoc_sensitivity": external_rubrics is not None}
    (output / "rescore_identity.json").write_text(
        json.dumps(rescore_identity, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    return report


def rescore_gold_migration(
    prepared: Path,
    run: Path,
    source_dataset: Path,
    target_dataset: Path,
    output: Path,
    *,
    external_rubrics: Path | None = None,
):
    """Rescore saved answers against an explicitly linked development gold migration.

    Changed prompts, semantic judge contracts, and oracle interventions cannot be replayed
    without new model calls. They are retained as explicit counterfactual-mismatch flags.
    """

    import json
    from ..compression_audit.artifacts import load_dataset
    from ..compression_audit.build import verify_file_manifest
    from ..compression_audit.development_runner import public_evidence_roles_supported
    from ..compression_audit.development_scoring import (
        RUBRIC_VERSION,
        SCORING_REVISION,
        make_rubric,
    )
    from ..compression_audit.io import file_sha256
    from .config import load_config
    from .rubrics import import_rubrics

    verify_file_manifest(prepared)
    verify_file_manifest(source_dataset)
    verify_file_manifest(target_dataset)
    if output.exists():
        raise ValueError("gold migration rescore output must be new")
    prepared_manifest = json.loads((prepared / "manifest.json").read_text(encoding="utf-8"))
    source_manifest_sha = file_sha256(source_dataset / "manifest.json")
    target_manifest_sha = file_sha256(target_dataset / "manifest.json")
    target_manifest = json.loads(
        (target_dataset / "manifest.json").read_text(encoding="utf-8")
    )
    if prepared_manifest["dataset_manifest_sha256"] != source_manifest_sha:
        raise ValueError("source dataset differs from the prepared package")
    if target_manifest.get("parent_dataset_manifest_sha256") != source_manifest_sha:
        raise ValueError("target dataset is not an explicit child of the source dataset")
    migration_receipt = target_dataset / "audit" / "gold_migration.jsonl"
    if (not migration_receipt.is_file()
            or target_manifest.get("gold_migration_receipt_sha256")
            != file_sha256(migration_receipt)):
        raise ValueError("target dataset lacks its bound gold migration receipt")

    source_prefixes, source_queries, source_gold_rows = load_dataset(source_dataset, legacy=False)
    target_prefixes, target_queries, target_gold_rows = load_dataset(target_dataset, legacy=False)
    source_prefix = {row.prefix_id: row for row in source_prefixes}
    target_prefix = {row.prefix_id: row for row in target_prefixes}
    source_query = {row.query_id: row for row in source_queries}
    target_query = {row.query_id: row for row in target_queries}
    source_gold = {row.prefix_id: row for row in source_gold_rows}
    target_gold = {row.prefix_id: row for row in target_gold_rows}
    if set(source_prefix) != set(target_prefix) or any(
            source_prefix[key].prefix_hash != target_prefix[key].prefix_hash
            for key in source_prefix):
        raise ValueError("gold migration must not change public prefixes")
    if set(source_query) != set(target_query) or set(source_gold) != set(target_gold):
        raise ValueError("gold migration must preserve query and gold populations")
    migrated_prefixes = set(map(str, target_manifest.get("migrated_prefix_ids", ())))
    changed_gold = {
        key for key in source_gold
        if source_gold[key].gold_hash != target_gold[key].gold_hash
    }
    if not migrated_prefixes or changed_gold != migrated_prefixes:
        raise ValueError("target manifest does not exactly declare changed gold rows")
    reanchored_queries = set(map(str, target_manifest.get("reanchored_query_ids", ())))
    changed_queries = {
        key for key in source_query
        if source_query[key].query_hash != target_query[key].query_hash
    }
    if changed_queries != reanchored_queries:
        raise ValueError("target manifest does not exactly declare changed queries")
    for query_id in changed_queries:
        old = source_query[query_id].to_dict(include_hash=False)
        new = target_query[query_id].to_dict(include_hash=False)
        old.pop("text")
        new.pop("text")
        if old != new:
            raise ValueError("gold migration may only change declared query text")

    identity = json.loads((run / "identity.json").read_text(encoding="utf-8"))
    if identity["prepared_hash"] != file_sha256(prepared / "manifest.json"):
        raise ValueError("prepared run identity differs")
    jobs = load_jsonl(run / "completed_jobs.jsonl")
    old_rubrics = {row["query_id"]: row for row in load_jsonl(prepared / "rubrics.jsonl")}
    selected_queries = [target_query[query_id] for query_id in old_rubrics]
    selected_prefixes = {
        query.prefix_id: target_prefix[query.prefix_id] for query in selected_queries
    }
    if external_rubrics is None:
        new_rubrics = {
            query.query_id: make_rubric(query, target_gold[query.prefix_id])
            for query in selected_queries
        }
        external_rubrics_sha = None
    else:
        external_rows = load_jsonl(external_rubrics)
        unknown = {row["query_id"] for row in external_rows} - set(old_rubrics)
        if unknown:
            raise ValueError("migration rubrics contain queries outside the prepared package")
        new_rubrics = import_rubrics(
            external_rubrics, selected_queries, selected_prefixes, target_gold,
        )
        external_rubrics_sha = file_sha256(external_rubrics)
    config = load_config(prepared / "config.snapshot.json")
    examples = [row["result"] for row in jobs if row["kind"] == "judge_example"]
    assumed = bool(identity.get("assume_gates_passed", False))
    calibrated = judge_gate(examples, config["gates"])["pass"] or assumed
    counters = Counter()
    for job in jobs:
        if stable_digest(job["result"]) != job["result_hash"]:
            raise ValueError("saved job altered")
        if job["kind"] != "episode":
            continue
        row = job["result"]
        old_rubric = old_rubrics[row["query_id"]]
        rubric = new_rubrics[row["query_id"]]
        query_text_changed = row["query_id"] in changed_queries
        judge_contract_changed = any(
            old_rubric.get(key) != rubric.get(key)
            for key in (
                "strict_values", "necessary_facts", "contradictory_facts",
                "expected_scope",
            )
        )
        oracle_context_mismatch = (
            row["prefix_id"] in migrated_prefixes
            and (row.get("method_id") == "oracle"
                 or row.get("condition_id") == "oracle_failure_chain")
        )
        cached_judge_reused = calibrated and not judge_contract_changed
        row["score"] = score_submission(
            row["answer"], rubric, row["final_visible_ids"],
            judge=row["judge"] if cached_judge_reused else None,
            judge_calibrated=cached_judge_reused,
            status=row["status"],
            executed_side_effects=sum(
                bool(call.get("executed_side_effect")) for call in row["tool_calls"]
            ),
            unsafe_attempts=sum(
                bool(call.get("unsafe_side_effect_attempt")) for call in row["tool_calls"]
            ),
        )
        direct_eligible = not (
            query_text_changed or oracle_context_mismatch or judge_contract_changed
        )
        row["gold_migration"] = {
            "source_gold_hash": source_gold[row["prefix_id"]].gold_hash,
            "target_gold_hash": target_gold[row["prefix_id"]].gold_hash,
            "gold_changed": row["prefix_id"] in migrated_prefixes,
            "query_text_changed_after_saved_answer": query_text_changed,
            "oracle_context_uses_source_gold": oracle_context_mismatch,
            "judge_contract_changed": judge_contract_changed,
            "cached_judge_reused": cached_judge_reused,
            "direct_evaluation_eligible": direct_eligible,
            "interpretation": (
                "direct_saved_answer_rescore" if direct_eligible
                else "counterfactual_rule_rescore_with_recorded_mismatch"
            ),
        }
        row["score"]["gold_migration_semantic_judge_invalidated"] = judge_contract_changed
        row["score"]["direct_task_episode_evaluation_eligible"] = direct_eligible
        row["score"]["counterfactual_rule_rescore_only"] = not direct_eligible
        counters.update({
            "query_prompt_mismatch": int(query_text_changed),
            "oracle_context_mismatch": int(oracle_context_mismatch),
            "judge_contract_invalidated": int(judge_contract_changed),
            "direct_evaluation_eligible": int(direct_eligible),
        })
        initial_visible = set(row["artifact"]["visible_event_ids"])
        initial_support = any(
            set(ids) <= initial_visible for ids in rubric["alternative_evidence_sets"]
        )
        row["initial_necessary_evidence_present"] = initial_support
        row["reacquisition_opportunity"] = (
            row["track"] == "interactive_reacquisition"
            and not initial_support and row["recoverability"] != "R0"
        )
        row["unrecoverable_initial_context"] = (
            row["track"] == "interactive_reacquisition"
            and not initial_support and row["recoverability"] == "R0"
        )
        row["initial_public_evidence_roles_supported"] = public_evidence_roles_supported(
            target_query[row["query_id"]], row["artifact"]["records"]
        )

    ledger_file = run / "provider_ledger.jsonl"
    ledger = load_jsonl(ledger_file) if ledger_file.exists() else []
    for row in ledger:
        if stable_digest(row["response"]) != row["response_hash"]:
            raise ValueError("provider response altered")
    output.mkdir(parents=True)
    write_rows(output / "rubrics.jsonl", [new_rubrics[key] for key in sorted(new_rubrics)])
    if (run / "interrupted_jobs.jsonl").exists():
        (output / "interrupted_jobs.jsonl").write_bytes(
            (run / "interrupted_jobs.jsonl").read_bytes()
        )
    report = write_report(
        output, jobs, ledger, load_jsonl(prepared / "trials.jsonl"),
        config=config, mode=identity["mode"],
        stop="offline_gold_migration_rescore_no_new_calls",
        assume_gates_passed=assumed,
    )
    report.update({
        "interpretation": "development_gold_migration_counterfactual",
        "formal_comparison_eligible": False,
        "new_provider_requests": 0,
        "source_provider_requests": len(ledger),
        "gold_migration_mismatch_counts": dict(counters),
    })
    write_json(output / "report.json", report)
    with (output / "report.md").open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(
            "\n## 金标迁移限制\n\n"
            "本报告没有发出新模型请求。改写后的问题文本未发送给原回答模型；"
            "迁移前 Oracle 上下文仍包含源金标链；语义裁判契约改变的 episode 已禁用"
            "缓存裁判。因此相关条目仅是带 mismatch 标记的反事实规则复算，不能作为"
            "新问题或新 Oracle 条件的直接成绩。\n"
        )
    code_files = [
        Path(__file__).resolve(),
        Path(__file__).resolve().parents[1] / "compression_audit" / "development_scoring.py",
        Path(__file__).resolve().parent / "rubrics.py",
    ]
    code_hashes = {path.name: file_sha256(path) for path in code_files}
    migration_identity = {
        "schema_version": "server_eval_gold_migration_rescore_identity_v1",
        "development_only": True,
        "independent_validation": False,
        "formal_comparison_eligible": False,
        "prepared_manifest_sha256": file_sha256(prepared / "manifest.json"),
        "source_dataset_manifest_sha256": source_manifest_sha,
        "source_dataset_file_manifest_sha256": file_sha256(
            source_dataset / "file_manifest.jsonl"
        ),
        "target_dataset_manifest_sha256": target_manifest_sha,
        "target_dataset_file_manifest_sha256": file_sha256(
            target_dataset / "file_manifest.jsonl"
        ),
        "gold_migration_receipt_sha256": file_sha256(migration_receipt),
        "external_rubrics_sha256": external_rubrics_sha,
        "rescored_rubrics_sha256": file_sha256(output / "rubrics.jsonl"),
        "source_episodes_sha256": file_sha256(run / "episodes.jsonl"),
        "rescored_episodes_sha256": file_sha256(output / "episodes.jsonl"),
        "migrated_prefix_ids": sorted(migrated_prefixes),
        "reanchored_query_ids": sorted(reanchored_queries),
        "mismatch_counts": dict(counters),
        "rubric_version": RUBRIC_VERSION,
        "scoring_revision": SCORING_REVISION,
        "scoring_code_hashes": code_hashes,
        "scoring_code_digest": stable_digest(code_hashes),
        "new_provider_requests": 0,
    }
    write_json(output / "gold_migration_rescore_identity.json", migration_identity)
    if (file_sha256(source_dataset / "manifest.json") != source_manifest_sha
            or file_sha256(target_dataset / "manifest.json") != target_manifest_sha):
        raise ValueError("dataset identity changed during gold migration rescore")
    return report
