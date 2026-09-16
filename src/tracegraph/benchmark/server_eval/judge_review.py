"""Blind human review for judge transfer on real model calibration answers."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..compression_audit.build import verify_file_manifest, write_file_manifest
from ..compression_audit.development_experiment import write_json, write_rows
from ..compression_audit.io import file_sha256, load_jsonl, stable_digest


PACKET_SCHEMA = "real_answer_judge_review_packet_v1"
REVIEW_SCHEMA = "real_answer_judge_human_review_v1"
LABELS = {"supported", "missing", "contradicted", "uncertain"}
REAL_ANSWER_GATES = {
    "holdout_count": 20,
    "holdout_correct": 18,
    "holdout_protocol_valid": 19,
    "critical_false_positives": 0,
}


def _load_real_calibration(prepared: Path, run: Path) -> tuple[list[dict], dict[str, dict], dict]:
    verify_file_manifest(prepared)
    identity = json.loads((run / "identity.json").read_text(encoding="utf-8"))
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    if identity.get("prepared_hash") != file_sha256(prepared / "manifest.json"):
        raise ValueError("prepared run identity differs")
    if (identity.get("mode") != "live" or identity.get("assume_gates_passed") is not False
            or report.get("gate_override", {}).get("enabled") is not False):
        raise ValueError("blind review requires a live calibration without gate override")
    jobs = load_jsonl(run / "completed_jobs.jsonl")
    for job in jobs:
        if stable_digest(job.get("result")) != job.get("result_hash"):
            raise ValueError("saved calibration job was altered")
    episodes = [job["result"] for job in jobs
                if job.get("kind") == "episode" and job["result"].get("phase") == "calibration"]
    if len(episodes) != 40 or len({e["episode_id"] for e in episodes}) != 40:
        raise ValueError("real-answer review requires exactly 40 unique calibration episodes")
    if any(e.get("answer") is None or e.get("status") != "complete" for e in episodes):
        raise ValueError("all real-answer calibration episodes must have complete answers")
    rubrics = {r["query_id"]: r for r in load_jsonl(prepared / "rubrics.jsonl")}
    if any(e["query_id"] not in rubrics for e in episodes):
        raise ValueError("calibration rubric is missing")
    return episodes, rubrics, identity


def _split_prefixes(episodes: list[dict]) -> dict[str, str]:
    by_level: dict[str, set[str]] = defaultdict(set)
    for episode in episodes:
        by_level[episode["recoverability"]].add(episode["prefix_id"])
    if set(by_level) != {"R0", "R1", "R2", "R3"} or any(
            len(prefixes) != 2 for prefixes in by_level.values()):
        raise ValueError("expected two calibration prefixes at every recoverability level")
    result = {}
    for level, prefixes in sorted(by_level.items()):
        ordered = sorted(prefixes, key=lambda value: stable_digest(
            ["real-answer-judge-split-v1", level, value]))
        result[ordered[0]], result[ordered[1]] = "tuning", "holdout"
    return result


def _answer_request_payload(episode: dict) -> dict:
    calls = [row for row in episode.get("model_calls", []) if row.get("kind") == "answer"]
    if len(calls) != 1:
        raise ValueError("each calibration episode needs one original answer request")
    messages = calls[0].get("attempt", {}).get("request", {}).get("messages", [])
    users = [m for m in messages if m.get("role") == "user"]
    if not users:
        raise ValueError("answer request has no user payload")
    payload = json.loads(users[-1]["content"])
    if not isinstance(payload.get("question"), str) or not isinstance(payload.get("records"), list):
        raise ValueError("answer request payload is incomplete")
    return payload


def export_real_answer_review(prepared: Path, run: Path, output: Path) -> dict[str, Any]:
    """Export all real calibration answers, hiding method and prior judge outcomes."""

    if (output.exists() or output.resolve().is_relative_to(run.resolve())
            or output.resolve().is_relative_to(prepared.resolve())):
        raise ValueError("blind review export requires a new separate output directory")
    episodes, rubrics, identity = _load_real_calibration(prepared, run)
    splits = _split_prefixes(episodes)
    ordered = sorted(episodes, key=lambda e: stable_digest(
        ["real-answer-blind-order-v1", identity["prepared_hash"], e["episode_id"]]))
    cases, templates, private = [], [], []
    for index, episode in enumerate(ordered, 1):
        blind_id = f"real-answer-{index:03d}"
        rubric = rubrics[episode["query_id"]]
        payload = _answer_request_payload(episode)
        facts = dict(rubric["necessary_facts"])
        answer_text = episode["answer"]["a"]
        cases.append({
            "schema_version": PACKET_SCHEMA,
            "blind_id": blind_id,
            "question": payload["question"],
            "records": payload["records"],
            "answer": episode["answer"],
            "necessary_facts": facts,
            "review_task": (
                "Label whether answer.a explicitly supports each supplied necessary fact from "
                "the records, and quote every contradictory assertion exactly from answer.a."),
        })
        templates.append({
            "schema_version": REVIEW_SCHEMA,
            "blind_id": blind_id,
            "reviewer_id": "",
            "human_reviewed": False,
            "facts": {name: None for name in facts},
            "contradictions": [],
            "overall_semantic_pass": None,
            "notes": "",
        })
        score = episode["score"]
        private.append({
            "blind_id": blind_id,
            "episode_id": episode["episode_id"],
            "prefix_id": episode["prefix_id"],
            "query_id": episode["query_id"],
            "query_type": episode["query_type"],
            "method_id": episode["method_id"],
            "recoverability": episode["recoverability"],
            "split": splits[episode["prefix_id"]],
            "rubric_hash": rubric["rubric_hash"],
            "prior_judge": episode.get("judge"),
            "prior_judge_protocol_valid": score.get("judge_protocol_valid"),
            "prior_judge_protocol_errors": score.get("judge_protocol_errors", []),
            "prior_judge_semantic_pass": score.get("judge_auxiliary_pass"),
            "prior_audit_pass": score.get("audit_pass"),
            "answer_text_digest": stable_digest(answer_text),
        })
    if Counter(row["split"] for row in private) != {"tuning": 20, "holdout": 20}:
        raise ValueError("real-answer tuning/holdout split is not balanced")
    reviewer = output / "reviewer_packet"
    organizer = output / "organizer_private"
    reviewer.mkdir(parents=True)
    organizer.mkdir()
    write_rows(reviewer / "cases.jsonl", cases)
    write_rows(reviewer / "reviews.reviewer_a.template.jsonl", templates)
    write_rows(reviewer / "reviews.reviewer_b.template.jsonl", templates)
    (reviewer / "README.md").write_text(
        "# 真实答案盲审\n\n"
        "只查看本目录；不要查看原 run 或 organizer_private。两位审阅者分别把各自模板复制到"
        "本盲审包之外，再独立填写；不要在本目录新增或修改文件。\n\n"
        "对 facts 的每个键填写 supported、missing、contradicted 或 uncertain。supported 要求答案"
        "明确表达该事实，只有 evidence ID 不够。contradictions 中每项必须逐字复制自 answer.a。"
        "当且仅当所有 facts 均为 supported 且 contradictions 为空时，overall_semantic_pass=true。"
        "reviewer_id 必须非空且两位不同，human_reviewed 必须为 true。不要改 blind_id 或增删行。\n",
        encoding="utf-8")
    write_rows(organizer / "private_mapping.jsonl", private)
    (output / "ORGANIZER.md").write_text(
        "# 真实答案裁判校准组织说明\n\n"
        "1. 将 `reviewer_packet` 单独交给两位审阅者；不要交付 `organizer_private` 或原运行目录。\n"
        "2. 两人分别把 A/B 模板复制到本盲审包之外，独立填写全部 40 条，不得互看结果。\n"
        "3. 用 `judge-review-import --packet <本目录> --reviews <A.jsonl> <B.jsonl> "
        "--output <新目录>` 导入。\n"
        "4. 如有分歧，只把导入结果中的空白 `adjudication.template.jsonl` 和原 `cases.jsonl` "
        "交给第三位审阅者；不要给他/她看 `adjudication_workitems.jsonl`。\n"
        "5. 使用同一导入命令并添加 `--adjudication <第三人.jsonl>`。只有 "
        "`real_answer_judge_gate_pass=true` 才表示具备主矩阵候选资格；"
        "`main_matrix_authorized` 仍保持 false，避免盲审导入自动触发推理。\n",
        encoding="utf-8")
    manifest = {
        "schema_version": PACKET_SCHEMA,
        "case_count": len(cases),
        "reviewer_packet_count": len(cases),
        "tuning_count": 20,
        "holdout_count": 20,
        "target_prior_protocol_failures": sum(
            row["prior_judge_protocol_valid"] is False for row in private),
        "control_count": sum(row["prior_judge_protocol_valid"] is not False for row in private),
        "required_independent_reviewers": 2,
        "human_reviews_completed": 0,
        "source_prepared_manifest_sha256": file_sha256(prepared / "manifest.json"),
        "source_run_identity_sha256": file_sha256(run / "identity.json"),
        "source_completed_jobs_sha256": file_sha256(run / "completed_jobs.jsonl"),
        "provider_requests": 0,
        "development_only": True,
        "independent_validation": False,
        "gates": REAL_ANSWER_GATES,
    }
    write_json(output / "manifest.json", manifest)
    write_file_manifest(output)
    return manifest


def _validated_reviews(cases: dict[str, dict], path: Path, *,
                       expected_ids: set[str] | None = None) -> tuple[str, dict[str, dict]]:
    expected_ids = set(cases) if expected_ids is None else expected_ids
    rows = load_jsonl(path)
    row_ids = {row.get("blind_id") for row in rows}
    if len(rows) != len(expected_ids) or row_ids != expected_ids:
        raise ValueError("review file must contain every expected blind case exactly once")
    reviewer_ids = {row.get("reviewer_id") for row in rows}
    if len(reviewer_ids) != 1 or not next(iter(reviewer_ids), ""):
        raise ValueError("one nonempty reviewer_id is required per review file")
    reviewer_id = next(iter(reviewer_ids))
    result = {}
    for row in rows:
        blind_id = row.get("blind_id")
        if blind_id not in cases or row.get("schema_version") != REVIEW_SCHEMA:
            raise ValueError("review case or schema differs")
        if row.get("human_reviewed") is not True:
            raise ValueError("review must be explicitly marked human_reviewed")
        facts = row.get("facts")
        expected = set(cases[blind_id]["necessary_facts"])
        if not isinstance(facts, dict) or set(facts) != expected or any(
                value not in LABELS for value in facts.values()):
            raise ValueError("review fact labels differ")
        contradictions = row.get("contradictions")
        answer_text = cases[blind_id]["answer"]["a"]
        if (not isinstance(contradictions, list)
                or any(not isinstance(item, str) or item not in answer_text
                       for item in contradictions)
                or len(contradictions) != len(set(contradictions))):
            raise ValueError("human contradiction must be a unique exact answer substring")
        semantic_pass = all(value == "supported" for value in facts.values()) and not contradictions
        if row.get("overall_semantic_pass") is not semantic_pass:
            raise ValueError("overall_semantic_pass differs from fact labels")
        if not isinstance(row.get("notes", ""), str):
            raise ValueError("review notes must be text")
        result[blind_id] = {**row, "contradictions": sorted(contradictions)}
    return str(reviewer_id), result


def import_real_answer_reviews(packet: Path, review_files: list[Path], output: Path, *,
                               adjudication: Path | None = None) -> dict[str, Any]:
    """Validate two independent reviews and evaluate saved judge transfer."""

    if output.exists() or output.resolve().is_relative_to(packet.resolve()):
        raise ValueError("review import requires a new separate output directory")
    verify_file_manifest(packet)
    manifest = json.loads((packet / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema_version") != PACKET_SCHEMA or len(review_files) != 2:
        raise ValueError("exactly two independent reviews are required")
    cases = {row["blind_id"]: row for row in load_jsonl(
        packet / "reviewer_packet" / "cases.jsonl")}
    private = {row["blind_id"]: row for row in load_jsonl(
        packet / "organizer_private" / "private_mapping.jsonl")}
    if set(cases) != set(private) or len(cases) != manifest["case_count"]:
        raise ValueError("review packet mapping differs")
    first_id, first = _validated_reviews(cases, review_files[0])
    second_id, second = _validated_reviews(cases, review_files[1])
    if first_id == second_id:
        raise ValueError("independent review files need different reviewer IDs")
    initial_disagreements = {
        blind_id for blind_id in cases
        if (first[blind_id]["facts"] != second[blind_id]["facts"]
            or first[blind_id]["contradictions"] != second[blind_id]["contradictions"]
            or first[blind_id]["overall_semantic_pass"]
               is not second[blind_id]["overall_semantic_pass"])
    }
    adjudicated = {}
    if adjudication is not None:
        if not initial_disagreements:
            raise ValueError("adjudication was supplied but the reviewers have no disagreements")
        adjudicator_id, adjudicated = _validated_reviews(
            cases, adjudication, expected_ids=initial_disagreements)
        if adjudicator_id in {first_id, second_id}:
            raise ValueError("adjudicator must differ from both reviewers")
    consensus, disagreements = [], []
    for blind_id in sorted(cases):
        left, right = first[blind_id], second[blind_id]
        same = (left["facts"] == right["facts"]
                and left["contradictions"] == right["contradictions"]
                and left["overall_semantic_pass"] is right["overall_semantic_pass"])
        chosen = left if same else adjudicated.get(blind_id)
        if chosen is None:
            disagreements.append({"blind_id": blind_id, "reviewer_a": left,
                                  "reviewer_b": right, "adjudication": None})
            continue
        consensus.append({"blind_id": blind_id, "facts": chosen["facts"],
            "contradictions": chosen["contradictions"],
            "overall_semantic_pass": chosen["overall_semantic_pass"],
            "source": "reviewer_agreement" if same else "third_reviewer_adjudication"})
    consensus_by_id = {row["blind_id"]: row for row in consensus}
    evaluated = []
    for blind_id, human in consensus_by_id.items():
        prior = private[blind_id]
        judge = prior.get("prior_judge") or {}
        valid = prior.get("prior_judge_protocol_valid") is True
        judge_pass = prior.get("prior_judge_semantic_pass")
        evaluated.append({
            "blind_id": blind_id,
            "split": prior["split"],
            "judge_protocol_valid": valid,
            "judge_semantic_pass": judge_pass,
            "human_semantic_pass": human["overall_semantic_pass"],
            "semantic_pass_correct": valid and judge_pass is human["overall_semantic_pass"],
            "fact_labels_exact": valid and judge.get("facts") == human["facts"],
            "critical_false_positive": valid and judge_pass is True
                                      and human["overall_semantic_pass"] is False,
        })
    holdout = [row for row in evaluated if row["split"] == "holdout"]
    metrics = {
        "holdout_count": len(holdout),
        "holdout_protocol_valid": sum(row["judge_protocol_valid"] for row in holdout),
        "holdout_correct": sum(row["semantic_pass_correct"] for row in holdout),
        "holdout_fact_labels_exact": sum(row["fact_labels_exact"] for row in holdout),
        "critical_false_positives": sum(row["critical_false_positive"] for row in holdout),
    }
    ready = (not disagreements and len(consensus) == len(cases)
             and metrics["holdout_count"] == REAL_ANSWER_GATES["holdout_count"]
             and metrics["holdout_protocol_valid"] >= REAL_ANSWER_GATES["holdout_protocol_valid"]
             and metrics["holdout_correct"] >= REAL_ANSWER_GATES["holdout_correct"]
             and metrics["critical_false_positives"] <= REAL_ANSWER_GATES["critical_false_positives"])
    report = {
        "schema_version": "real_answer_judge_review_report_v1",
        "reviewer_ids": [first_id, second_id],
        "case_count": len(cases),
        "consensus_count": len(consensus),
        "disagreement_count": len(disagreements),
        "adjudication_supplied": adjudication is not None,
        "metrics": metrics,
        "gates": REAL_ANSWER_GATES,
        "real_answer_judge_gate_pass": ready,
        "main_matrix_eligible": ready,
        "main_matrix_authorized": False,
        "main_matrix_blocker": (
            "explicit execution authorization is still required" if ready else
            "real-answer judge review or transfer gate is incomplete/failed"),
        "provider_requests": 0,
        "human_validation_claim": not disagreements and len(consensus) == len(cases),
        "development_only": True,
        "independent_validation": False,
    }
    output.mkdir(parents=True)
    write_rows(output / "consensus_reviews.jsonl", consensus)
    write_rows(output / "judge_transfer_evaluation.jsonl", evaluated)
    write_rows(output / "adjudication_workitems.jsonl", disagreements)
    adjudication_template = [{
        "schema_version": REVIEW_SCHEMA,
        "blind_id": row["blind_id"],
        "reviewer_id": "",
        "human_reviewed": False,
        "facts": {name: None for name in cases[row["blind_id"]]["necessary_facts"]},
        "contradictions": [],
        "overall_semantic_pass": None,
        "notes": "",
    } for row in disagreements]
    write_rows(output / "adjudication.template.jsonl", adjudication_template)
    write_json(output / "report.json", report)
    write_json(output / "identity.json", {
        "packet_manifest_sha256": file_sha256(packet / "manifest.json"),
        "packet_file_manifest_sha256": file_sha256(packet / "file_manifest.jsonl"),
        "review_file_sha256": {path.name: file_sha256(path) for path in review_files},
        "adjudication_sha256": file_sha256(adjudication) if adjudication else None,
        "new_provider_requests": 0,
    })
    write_file_manifest(output)
    return report
