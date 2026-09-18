"""Analyze the frozen adjudicated Full History/Oracle attribution run offline."""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from tracegraph.benchmark.compression_audit.build import verify_file_manifest
from tracegraph.benchmark.compression_audit.io import load_jsonl, stable_digest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    args = parse_args()
    if args.output.exists():
        raise ValueError("analysis output must be new")
    verify_file_manifest(args.dataset)
    report = json.loads((args.run / "report.json").read_text(encoding="utf-8"))
    episodes = load_jsonl(args.run / "episodes.jsonl")
    provider = load_jsonl(args.run / "provider_ledger.jsonl")
    attempts = load_jsonl(args.run / "provider_attempts.jsonl")
    if len(provider) != len(attempts) or any(not row["valid_usage"] for row in provider):
        raise ValueError("provider ledger is incomplete or invalid")
    gold_rows = load_jsonl(args.dataset / "private" / "real_all_gold.jsonl")
    prefix_rows = load_jsonl(args.dataset / "public" / "real_prefixes.jsonl")
    gold = {row["prefix_id"]: row for row in gold_rows}
    prefixes = {row["prefix_id"]: row for row in prefix_rows}
    calls = defaultdict(list)
    for row in provider:
        calls[row["job_id"]].append(row)

    targets = [
        episode
        for episode in episodes
        if episode["phase"] in {"main", "diagnostic"}
        and episode["query_type"] == "audit_chain"
        and episode["method_id"] in {"full_history", "oracle"}
    ]
    detail_rows = []
    missing_rows = []
    for episode in targets:
        prefix_id = episode["prefix_id"]
        ordered = gold[prefix_id]["ordered_event_ids"]
        cited = (episode.get("answer") or {}).get("e", [])
        missing = [event_id for event_id in ordered if event_id not in cited]
        extra = [event_id for event_id in cited if event_id not in ordered]
        core_roles = (
            "failed_action", "failure_result", "replacement_action", "resolution_evidence"
        )
        core_ids = list(dict.fromkeys(
            event_id
            for role in core_roles
            for event_id in gold[prefix_id]["evidence_by_field"].get(role, [])
        ))
        core_missing = [event_id for event_id in core_ids if event_id not in cited]
        relevant_calls = [
            row for row in calls[episode["episode_id"]]
            if row["kind"] in {"answer", "format_repair"}
        ]
        thinking_nonempty = 0
        direct_tools = 0
        for row in relevant_calls:
            message = row["response"]["choices"][0]["message"]
            match = re.search(
                r"<think>\s*(.*?)\s*</think>",
                message.get("content") or "",
                flags=re.DOTALL,
            )
            thinking_nonempty += bool(match and match.group(1).strip())
            direct_tools += bool(message.get("tool_calls"))
        score = episode["score"]
        detail_rows.append({
            "model_id": episode["model_id"],
            "method_id": episode["method_id"],
            "prefix_id": prefix_id,
            "recoverability": episode["recoverability"],
            "status": episode["status"],
            "hard_pass": score["hard_pass"],
            "protocol_valid": score["protocol_error"] is None,
            "judge_auxiliary_pass": score["judge_auxiliary_pass"],
            "answer_citation_pass": score["answer_citation_pass"],
            "evidence_precision": score["evidence_precision"],
            "evidence_recall": score["evidence_recall"],
            "causal_constraint_rate": score["causal_constraint_rate"],
            "gold_chain_length": len(ordered),
            "cited_count": len(cited),
            "missing_count": len(missing),
            "extra_count": len(extra),
            "core_chain_recall_pass": not core_missing,
            "core_missing_count": len(core_missing),
            "gold_event_ids": "|".join(ordered),
            "cited_event_ids": "|".join(cited),
            "missing_event_ids": "|".join(missing),
            "extra_event_ids": "|".join(extra),
            "core_missing_event_ids": "|".join(core_missing),
            "answer_calls": len(relevant_calls),
            "nonempty_thinking_calls": thinking_nonempty,
            "direct_submission_tool_calls": direct_tools,
            "history_tokens": episode["artifact"]["token_count"],
        })
        event_map = {
            event["event_id"]: event for event in prefixes[prefix_id]["events"]
        }
        roles_by_event = defaultdict(list)
        for role, event_ids in gold[prefix_id]["evidence_by_field"].items():
            for event_id in event_ids:
                roles_by_event[event_id].append(role)
        for event_id in missing:
            event = event_map[event_id]
            missing_rows.append({
                "model_id": episode["model_id"],
                "method_id": episode["method_id"],
                "prefix_id": prefix_id,
                "event_id": event_id,
                "event_kind": event["kind"],
                "gold_roles": "|".join(sorted(roles_by_event[event_id])),
                "content_preview": json.dumps(
                    event["content"], ensure_ascii=False, sort_keys=True
                )[:500],
            })

    args.output.mkdir(parents=True)
    with (args.output / "target_episodes.csv").open(
            "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(detail_rows[0]))
        writer.writeheader()
        writer.writerows(detail_rows)
    with (args.output / "missing_gold_events.csv").open(
            "w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(missing_rows[0]))
        writer.writeheader()
        writer.writerows(missing_rows)

    cells = {}
    for key in sorted({(row["model_id"], row["method_id"]) for row in detail_rows}):
        group = [row for row in detail_rows if (row["model_id"], row["method_id"]) == key]
        cells["/".join(key)] = {
            "n": len(group),
            "hard_pass": sum(bool(row["hard_pass"]) for row in group),
            "protocol_valid": sum(bool(row["protocol_valid"]) for row in group),
            "judge_auxiliary_pass": sum(bool(row["judge_auxiliary_pass"]) for row in group),
            "answer_citation_pass": sum(bool(row["answer_citation_pass"]) for row in group),
            "core_chain_recall_pass": sum(
                bool(row["core_chain_recall_pass"]) for row in group
            ),
            "core_chain_plus_content_protocol_pass": sum(
                bool(row["core_chain_recall_pass"])
                and bool(row["judge_auxiliary_pass"])
                and bool(row["protocol_valid"])
                for row in group
            ),
            "mean_evidence_precision": sum(row["evidence_precision"] for row in group) / len(group),
            "mean_evidence_recall": sum(row["evidence_recall"] for row in group) / len(group),
            "mean_causal_constraint_rate": sum(row["causal_constraint_rate"] for row in group) / len(group),
            "mean_history_tokens": sum(row["history_tokens"] for row in group) / len(group),
            "answer_calls": sum(row["answer_calls"] for row in group),
            "nonempty_thinking_calls": sum(row["nonempty_thinking_calls"] for row in group),
            "direct_submission_tool_calls": sum(row["direct_submission_tool_calls"] for row in group),
        }
    missing_by_cell_role = {}
    for key in sorted(cells):
        model_id, method_id = key.split("/")
        rows = [
            row for row in missing_rows
            if row["model_id"] == model_id and row["method_id"] == method_id
        ]
        missing_by_cell_role[key] = dict(sorted(Counter(
            role
            for row in rows
            for role in row["gold_roles"].split("|")
            if role
        ).items()))
    summary = {
        "format": "adjudicated_oracle_attribution_analysis_v1",
        "development_only": True,
        "independent_validation": False,
        "provider_requests": len(provider),
        "provider_attempts_equal_ledger": len(provider) == len(attempts),
        "report_stop_reason": report["stop_reason"],
        "report_digest": stable_digest(report),
        "target_episode_count": len(targets),
        "cells": cells,
        "missing_gold_roles": missing_by_cell_role,
    }
    write_json(args.output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
