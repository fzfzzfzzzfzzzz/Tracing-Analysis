"""Disaggregated diagnostic reports; no model ranking or independent-validation claim."""

from __future__ import annotations

import csv
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .development_experiment import write_json, write_rows
from .io import canonical_json


def model_gate(episodes: list[dict], gates: dict) -> dict:
    rows = [e for e in episodes if e["phase"] == "calibration"]
    counts = {"episode_count": len(rows),
        "first_format_valid": sum(e["first_format_valid"] for e in rows),
        "final_format_valid": sum(e["score"]["structured_output_valid"] for e in rows),
        "full_pass": sum(e["score"]["audit_pass"] for e in rows if e["method_id"] == "full_history"),
        "oracle_pass": sum(e["score"]["audit_pass"] for e in rows if e["method_id"] == "oracle")}
    counts["pass"] = len(rows) == 40 and all(counts[k] >= gates[k] for k in
        ("first_format_valid", "final_format_valid", "full_pass", "oracle_pass"))
    return counts


def judge_gate(examples: list[dict], gates: dict) -> dict:
    holdout = [e for e in examples if e["split"] == "holdout"]
    correct = sum(e["predicted_pass"] == e["expected_pass"] and e.get("judge_valid", False)
                  for e in holdout)
    critical = sum(e.get("critical_false_positive", False) for e in holdout)
    return {"holdout_count": len(holdout), "correct": correct,
            "critical_false_positives": critical,
            "pass": len(holdout) == 40 and correct >= gates["judge_holdout_correct"]
                    and critical <= gates["judge_critical_false_positives"],
            "calibration_kind": "rule_examples_only", "human_validated": False}


def write_results(output: Path, episodes: list[dict], examples: list[dict], ledger: list[dict],
                  *, config: dict, mode: str, stop_reason: str | None) -> dict[str, Any]:
    groups = defaultdict(list)
    for row in episodes:
        groups[(row["phase"], row["method_id"], row["query_type"])].append(row)
    summaries = []
    def mean(values):
        present = [float(v) for v in values if v is not None]
        return sum(present) / len(present) if present else None

    for (phase, method, query), rows in sorted(groups.items()):
        summaries.append({"phase": phase, "method_id": method, "query_type": query,
            "n": len(rows), "protocol_valid": sum(r["score"]["structured_output_valid"] for r in rows),
            "hard_pass": sum(r["score"]["hard_pass"] for r in rows),
            "judge_auxiliary_pass": sum(r["score"]["judge_auxiliary_pass"] is True for r in rows),
            "combined_pass": sum(r["score"]["audit_pass"] for r in rows),
            "first_format_valid": sum(r["first_format_valid"] for r in rows),
            "pending_review": sum(r["score"]["judge_auxiliary_pass"] is None for r in rows),
            "evidence_precision": mean(r["score"]["evidence_precision"] for r in rows),
            "evidence_recall": mean(r["score"]["evidence_recall"] for r in rows),
            "initial_context_support": sum(r.get("initial_necessary_evidence_present",
                r["score"]["necessary_evidence_present"]) for r in rows),
            "final_context_support": sum(r["score"]["necessary_evidence_present"] for r in rows),
            "answer_citation_pass": sum(r["score"].get(
                "answer_citation_pass", r["score"]["evidence_pass"]) for r in rows),
            "failure_stage_counts": dict(Counter(stage for r in rows
                for stage in r["score"].get("failure_stages", ()))),
            "causal_constraint_rate": mean(r["score"]["causal_constraint_rate"] for r in rows),
            "strict_chain_recovery_rate": mean(r["score"]["strict_chain_recovered"] for r in rows),
            "safety_pass_count": sum(r["score"]["safety_pass"] for r in rows),
            "history_tokens_mean": mean(r["artifact"].get("token_count") for r in rows),
            "answer_model_calls": sum(len(r["model_calls"]) for r in rows),
            "tool_calls": sum(len(r["tool_calls"]) for r in rows),
            "deployment_cost_cny": sum(r["deployment_cost_cny"] for r in rows)})
    pairs = []
    full = {e["query_id"]: e for e in episodes if e["method_id"] == "full_history"}
    oracle = {e["query_id"]: e for e in episodes if e["method_id"] == "oracle"}
    for row in episodes:
        reference = full.get(row["query_id"])
        if reference is None or row is reference:
            continue
        labels = list(row["score"]["failure_labels"])
        o = oracle.get(row["query_id"])
        if o and not reference["score"]["audit_pass"] and not o["score"]["audit_pass"]:
            labels.append("full_and_oracle_failed_check_task_scorer_protocol_model")
        if reference["score"]["audit_pass"] and not row["score"]["audit_pass"]:
            labels.append("candidate_failure_with_successful_full")
            labels.append("memory_evidence_missing" if not row["score"]["necessary_evidence_present"]
                          else "evidence_present_check_answer_or_organization")
        row["score"]["failure_labels"] = sorted(set(labels))
        pairs.append({"episode_id": row["episode_id"], "method_id": row["method_id"],
            "phase": row["phase"], "query_type": row["query_type"],
            "full_correct": reference["score"]["audit_pass"],
            "candidate_correct": row["score"]["audit_pass"],
            "extra_model_calls": len(row["model_calls"]) - len(reference["model_calls"]),
            "extra_tool_calls": len(row["tool_calls"]) - len(reference["tool_calls"]),
            "extra_cost_cny": row["deployment_cost_cny"] - reference["deployment_cost_cny"]})
    costs = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0,
                                 "latency_seconds": 0, "cost_cny": 0})
    for call in ledger:
        group = costs[call["kind"]]
        group["calls"] += 1
        for target, source in (("input_tokens", "prompt_tokens"), ("output_tokens", "completion_tokens"),
                               ("latency_seconds", "latency_seconds"), ("cost_cny", "cost_cny")):
            group[target] += call.get(source) or 0
    conditional = []
    for method in sorted({p["method_id"] for p in pairs}):
        rows = [p for p in pairs if p["method_id"] == method]
        eligible = [p for p in rows if p["full_correct"]]
        conditional.append({"method_id": method, "all_pairs": len(rows),
            "all_correct": sum(p["candidate_correct"] for p in rows),
            "full_correct_subset": len(eligible), "subset_coverage": len(eligible) / len(rows),
            "subset_correct": sum(p["candidate_correct"] for p in eligible)})
    by_query_method = {(e["query_id"], e["method_id"]): e for e in episodes if e["phase"] == "main"}
    graph_flat = []
    for (query_id, method), row in by_query_method.items():
        flat = by_query_method.get((query_id, "flat_bm25_archive"))
        if method == "tracegraph_0_4" and flat:
            graph_flat.append({"query_id": query_id,
                "graph_correct": row["score"]["audit_pass"], "flat_correct": flat["score"]["audit_pass"],
                "extra_deployment_cost_cny": row["deployment_cost_cny"] - flat["deployment_cost_cny"],
                "extra_history_tokens": row["artifact"]["token_count"] - flat["artifact"]["token_count"]})
    interactive_analysis = []
    interactive = [e for e in episodes if e["track"] == "interactive_reacquisition"]
    for method in sorted({e["method_id"] for e in interactive}):
        rows = [e for e in interactive if e["method_id"] == method]
        opportunities = [e for e in rows if e.get("reacquisition_opportunity", False)]
        unavailable = [e for e in rows if e.get("unrecoverable_initial_context", False)]
        initially_supported = [e for e in rows if e.get(
            "initial_necessary_evidence_present", e["score"]["necessary_evidence_present"])]
        interactive_analysis.append({"method_id": method, "episodes": len(rows),
            "initially_supported": len(initially_supported),
            "initially_supported_pass": sum(e["score"]["audit_pass"] for e in initially_supported),
            "reacquisition_opportunities": len(opportunities),
            "reacquisition_successes": sum(e["score"]["audit_pass"] for e in opportunities),
            "unrecoverable_initial_contexts": len(unavailable),
            "unrecoverable_without_unsafe_tool": sum(not any(
                tool.get("unsafe_side_effect_attempt") or tool.get("executed_side_effect")
                for tool in e["tool_calls"]) for e in unavailable),
            "max_turns": sum(e["status"] == "max_turns" for e in rows),
            "tool_calls": sum(len(e["tool_calls"]) for e in rows),
            "forced_initial_tools": sum(e.get("reacquisition_initial_tool_forced", False)
                                        for e in rows),
            "forced_sequence_tools": sum(e.get("reacquisition_sequence_tools_forced", 0)
                                         for e in rows),
            "forced_submissions": sum(e.get("reacquisition_submission_forced", False)
                                      for e in rows),
            "forced_initial_supported_submissions": sum(e.get(
                "initial_supported_submission_forced", False) for e in rows),
            "forced_safe_unavailable_submissions": sum(e.get(
                "safe_unavailable_submission_forced", False) for e in rows),
            "submission_reminders": sum(e.get(
                "reacquisition_submission_reminder_added", False) for e in rows)})
    report = {"protocol": "v0.2-development", "mode": mode,
        "interpretation": "offline_wiring_only" if mode == "offline" else "development_diagnostic",
        "episode_count": len(episodes), "provider_requests": len(ledger),
        "stop_reason": stop_reason, "summaries": summaries,
        "judge_gate": judge_gate(examples, config["gates"]),
        "model_gate": model_gate(episodes, config["gates"]),
        "cost_by_stage": dict(costs), "total_cost_cny": sum(c["cost_cny"] for c in ledger),
        "local_memory_construction_seconds": sum(e.get("construction_seconds", 0) for e in episodes
            if e.get("construction_charged", False)),
        "local_memory_materialization_seconds": sum(e.get("materialization_seconds", 0) for e in episodes),
        "archive_observation_tokens": sum(e.get("archive_observation_tokens", 0) for e in episodes),
        "tool_observation_tokens": sum(e.get("tool_observation_tokens", 0) for e in episodes),
        "full_correct_subset_analysis": conditional,
        "graph_flat_paired_comparison": graph_flat,
        "interactive_analysis": interactive_analysis,
        "failure_counts": dict(Counter(label for e in episodes for label in e["score"]["failure_labels"])),
        "failure_stage_counts": dict(Counter(stage for e in episodes
            for stage in e["score"].get("failure_stages", ()))),
        "answer_contract_revisions": sorted({e.get("answer_contract_revision", "unversioned")
                                             for e in episodes}),
        "interaction_policy_revisions": sorted({e.get(
            "interaction_policy_revision", "unversioned") for e in episodes}),
        "single_aggregate_score": None, "human_validated": False,
        "development_only": True, "independent_validation": False}
    write_rows(output / "episodes.jsonl", episodes)
    write_rows(output / "paired_diagnostics.jsonl", pairs)
    write_json(output / "report.json", report)
    with (output / "failure_table.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        fields = ["episode_id", "phase", "method_id", "query_type", "status", "context",
                  "answer_contract_revision", "interaction_policy_revision",
                  "initial_necessary_evidence_present",
                  "initial_public_evidence_roles_supported",
                  "reacquisition_opportunity", "unrecoverable_initial_context",
                  "reacquisition_submission_forced", "reacquisition_initial_tool_forced",
                  "reacquisition_sequence_tools_forced", "initial_supported_submission_forced",
                  "safe_unavailable_submission_forced", "reacquisition_submission_reminder_added",
                  "first_format_valid", "format_repair_used", "parse_error",
                  "necessary_evidence_present", "protocol_error", "hard_pass", "judge_auxiliary_pass",
                  "judge_protocol_valid", "judge_protocol_errors", "judge_protocol_warnings",
                  "judge_fact_support_rate", "judge_facts_all_supported",
                  "missing_strict_label_fields",
                   "strict_values", "evidence_pass", "evidence_precision", "evidence_recall",
                   "answer_citation_pass", "audit_pass", "failure_labels", "failure_stages",
                   "attribution", "judge", "judge_parse_error",
                  "model_calls", "tool_calls"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in episodes:
            values = {**row, **row["score"], "context": row["artifact"]["records"]}
            writer.writerow({key: canonical_json(values[key]) if isinstance(values.get(key), (dict, list))
                             else values.get(key) for key in fields})
    text = ["# v0.2 开发诊断结果", "",
            "本报告仅用于开发，不是独立验证。裁判只做规则样例校准，没有人工标定。", "",
            f"运行方式：{mode}。已处理 {len(episodes)} 个 episode；真实请求 {len(ledger)} 次。",
            f"停止原因：{stop_reason or '计划内任务完成'}。", ""]
    if mode == "offline":
        text += ["本次使用确定性答案和裁判夹具检查程序接线。以下数字不能解释为真实模型成绩。", ""]
    text += ["|阶段|方法|问题|数量|硬检查通过|裁判辅助通过|", "|---|---|---|---:|---:|---:|"]
    text += [f"|{s['phase']}|{s['method_id']}|{s['query_type']}|{s['n']}|{s['hard_pass']}|"
             f"{s['judge_auxiliary_pass']}|" for s in summaries]
    text += ["", f"总模型费用：{report['total_cost_cny']:.6f} 元。裁判费用单列，不计入方法部署成本。",
             "交互工具为不可变 fixture；耗时不代表真实 Docker 调查。",
             "逐题证据、解析结果、判断与账本见 failure_table.csv 和 episodes.jsonl。",
             "当前不能据此选择算法改进或宣称因果图有增益；需先通过真实模型校准。"]
    (output / "report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return report
