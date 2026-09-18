"""Development-only Qwen judge for the frozen AMA-Bench canary.

The answer generator writes a provenance-rich schema that differs from the
AMA-Hub command line schema.  This adapter reconstructs the official QA rows,
then calls the pinned AMA-Hub ``evaluate_batch`` implementation unchanged.
It is deliberately labelled development-only because the answer model and
judge model are both Qwen3.8-27B.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
AMA_ROOT = ROOT / "vendor" / "ama-hub-ddfd319"
DATASET = (
    ROOT
    / "data"
    / "external"
    / "compression_audit_sources_v1"
    / "ama_bench_a577737"
    / "open_end_qa_set.jsonl"
)
FREEZE = (
    ROOT
    / "data"
    / "external_benchmark_freezes"
    / "ama_bench_260917"
    / "canary_episode_ids.jsonl"
)
CONFIG = ROOT / "configs" / "ama_qwen38_27b_full_history_canary_260918.yaml"
DEFAULT_ANSWERS = (
    ROOT
    / "outputs"
    / "ama_external_canary_260918"
    / "full_history_qwen38_v3"
    / "answers.jsonl"
)
DEFAULT_OUTPUT = (
    ROOT
    / "outputs"
    / "ama_external_canary_260918"
    / "full_history_qwen38_v3"
    / "development_judge_qwen38_v1"
)

DATASET_SHA256 = "45c36052e1520d87ad9de4114f71c9df42d4aac9cf158c0c353e800b653d65ff"
FREEZE_SHA256 = "e6888a0f863146f6743a504b06b2919fa812e60be791a6202acd0b411c50d655"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def build_qa_rows(
    dataset_rows: list[dict[str, Any]],
    answer_rows: list[dict[str, Any]],
    frozen_ids: list[str],
) -> list[dict[str, Any]]:
    dataset = {str(row["episode_id"]): row for row in dataset_rows}
    answers = {str(row["episode_id"]): row for row in answer_rows}
    if set(answers) != set(frozen_ids):
        raise ValueError("answer episode IDs do not exactly match the frozen canary")

    qa_rows = []
    for episode_id in frozen_ids:
        episode = dataset[episode_id]
        generated = answers[episode_id]
        generated_by_uuid = {
            str(row["question_uuid"]): row for row in generated.get("answers", [])
        }
        pairs = episode.get("qa_pairs", [])
        if len(generated_by_uuid) != len(pairs):
            raise ValueError(f"answer count mismatch for episode {episode_id}")
        for pair in pairs:
            question_uuid = str(pair["question_uuid"])
            answer = generated_by_uuid.get(question_uuid)
            if answer is None:
                raise ValueError(
                    f"missing answer for episode {episode_id}, question {question_uuid}"
                )
            qa_rows.append(
                {
                    "episode_id": episode_id,
                    "question_uuid": question_uuid,
                    "task_type": episode.get("task_type", "unknown"),
                    "domain": episode.get("domain", "unknown"),
                    "task_description": episode.get("task", ""),
                    "question": pair.get("question", ""),
                    "golden_answer": pair.get("answer", ""),
                    "predicted_answer": answer.get("predicted_answer", ""),
                    "qa_type": pair.get("type") or "unknown",
                }
            )
    return qa_rows


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    def grouped(field: str) -> dict[str, dict[str, float | int]]:
        values: dict[str, list[float]] = defaultdict(list)
        for row in results:
            values[str(row.get(field, "unknown"))].append(float(row["score"]))
        return {
            key: {
                "count": len(scores),
                "correct": sum(score == 1.0 for score in scores),
                "accuracy": sum(score == 1.0 for score in scores) / len(scores),
            }
            for key, scores in sorted(values.items())
        }

    scores = [float(row["score"]) for row in results]
    return {
        "overall": {
            "total_questions": len(scores),
            "correct": sum(score == 1.0 for score in scores),
            "accuracy": sum(score == 1.0 for score in scores) / len(scores),
        },
        "by_episode": grouped("episode_id"),
        "by_task_type": grouped("task_type"),
        "by_domain": grouped("domain"),
        "by_qa_type": grouped("qa_type"),
    }


def judge_prompt(row: dict[str, Any]) -> str:
    context_parts = []
    if row.get("task_type"):
        context_parts.append(f"Task Type: {row['task_type']}")
    if row.get("episode_id"):
        context_parts.append(f"Episode ID: {row['episode_id']}")
    if row.get("task_description"):
        context_parts.append(f"Task Context: {row['task_description']}")
    context = "\n".join(context_parts)
    return f"""You are an expert evaluator. You will be given a question, a reference answer, and a predicted answer.
Your task is to determine if the predicted answer is correct based on:
1. Factual correctness compared to the reference
2. Completeness of the answer
3. Relevance to the question

{context}

Question: {row['question']}

Reference Answer: {row['golden_answer']}

Predicted Answer: {row['predicted_answer']}

Is the predicted answer correct? Respond with ONLY "yes" or "no". Do not include any thinking process, explanation, or additional text.

Answer:<think></think>"""


def parse_judgment(response: str) -> float:
    cleaned = re.sub(
        r"<think>.*?</think>", "", response, flags=re.DOTALL | re.IGNORECASE
    ).strip().lower()
    yes = list(re.finditer(r"\byes\b", cleaned))
    no = list(re.finditer(r"\bno\b", cleaned))
    last_yes = yes[-1].start() if yes else -1
    last_no = no[-1].start() if no else -1
    if last_yes > last_no:
        return 1.0
    if last_no > last_yes:
        return 0.0
    raise ValueError(f"unparseable judge response: {response!r}")


def repair_judge_prompt(row: dict[str, Any]) -> str:
    return f"""Evaluate whether the predicted answer is factually correct, complete, and relevant to the question when compared with the reference answer.

Task type: {row['task_type']}
Episode: {row['episode_id']}
Question: {row['question']}
Reference answer: {row['golden_answer']}
Predicted answer: {row['predicted_answer']}

Output only the digit 1 if the predicted answer is correct. Output only the digit 0 if it is incorrect."""


def parse_binary_repair(response: str) -> float:
    cleaned = re.sub(
        r"<think>.*?</think>", "", response, flags=re.DOTALL | re.IGNORECASE
    ).strip()
    matches = list(re.finditer(r"(?<!\d)([01])(?!\d)", cleaned))
    if not matches:
        raise ValueError(f"unparseable repaired judge response: {response!r}")
    return float(matches[-1].group(1))


def recover_from_receipts(
    qa_rows: list[dict[str, Any]], judge: Any, output: Path
) -> list[dict[str, Any]]:
    by_prompt_hash = {}
    for path in sorted((output / "provider_calls").glob("*.json")):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        prompt_hash = str(receipt["request"]["prompt_sha256"])
        previous = by_prompt_hash.get(prompt_hash)
        if previous is None or (
            not previous.get("selected_attempt") and receipt.get("selected_attempt")
        ):
            by_prompt_hash[prompt_hash] = receipt

    def selected_content(receipt: dict[str, Any] | None) -> str | None:
        if receipt is None or not receipt.get("selected_attempt"):
            return None
        return receipt["attempts"][int(receipt["selected_attempt"]) - 1]["response"][
            "choices"
        ][0]["message"]["content"]

    evaluated = []
    for row in qa_rows:
        prompt = judge_prompt(row)
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        receipt = by_prompt_hash.get(prompt_hash)
        if receipt is None:
            raise ValueError(f"missing original judge receipt: {prompt_hash}")
        response = selected_content(receipt)
        score = None
        call_mode = ""
        if response:
            try:
                score = parse_judgment(response)
                call_mode = "official_prompt"
            except ValueError:
                # A natural-language explanation is transport-valid but not a
                # valid binary verdict. Route it through the same frozen repair.
                score = None
        if score is None:
            old_suffix = (
                "\n\nThe previous transport attempt returned no visible token. "
                "Respond now with exactly one lowercase word: yes or no."
            )
            no_stub_suffix = (
                "The previous transport attempt returned no visible token. "
                "Respond now with exactly one lowercase word: yes or no.\nAnswer:"
            )
            cached_repairs = [
                (
                    prompt + old_suffix,
                    "empty_response_repair_yes_no_suffix",
                    parse_judgment,
                ),
                (
                    prompt.removesuffix("<think></think>") + no_stub_suffix,
                    "empty_response_repair_without_think_stub",
                    parse_judgment,
                ),
                (
                    repair_judge_prompt(row),
                    "empty_response_repair_binary_template",
                    parse_binary_repair,
                ),
            ]
            for repair_prompt, repair_mode, parser in cached_repairs:
                repair_hash = hashlib.sha256(repair_prompt.encode("utf-8")).hexdigest()
                cached_response = selected_content(by_prompt_hash.get(repair_hash))
                if cached_response:
                    response = cached_response
                    score = parser(response)
                    call_mode = repair_mode
                    break
            if score is None:
                thinking_before = bool(judge.sampling["enable_thinking"])
                judge.sampling["enable_thinking"] = True
                try:
                    response = judge.query(
                        repair_judge_prompt(row), temperature=0.0, max_tokens=8192
                    )
                finally:
                    judge.sampling["enable_thinking"] = thinking_before
                score = parse_binary_repair(response or "")
                call_mode = "invalid_response_repair_binary_thinking"
        evaluated.append(
            {
                **row,
                "score": score,
                "judge_call_mode": call_mode,
                "original_judge_prompt_sha256": prompt_hash,
            }
        )
    return evaluated


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--answers", type=Path, default=DEFAULT_ANSWERS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-workers", type=int, default=3)
    parser.add_argument("--allow-frozen-subset", action="store_true")
    parser.add_argument("--resume-after-empty-responses", action="store_true")
    args = parser.parse_args()

    if sha256_file(DATASET) != DATASET_SHA256:
        raise ValueError("AMA dataset hash changed")
    if sha256_file(FREEZE) != FREEZE_SHA256:
        raise ValueError("AMA canary freeze hash changed")
    if args.output.exists() and not args.resume_after_empty_responses:
        raise FileExistsError(f"refusing to replace existing output: {args.output}")
    if not args.output.exists() and args.resume_after_empty_responses:
        raise FileNotFoundError("cannot resume: judge output directory does not exist")

    frozen_rows = load_jsonl(FREEZE)
    frozen_ids = [str(row["episode_id"]) for row in frozen_rows]
    answer_rows = load_jsonl(args.answers)
    if args.allow_frozen_subset:
        answer_ids = {str(row["episode_id"]) for row in answer_rows}
        if not answer_ids or not answer_ids.issubset(set(frozen_ids)):
            raise ValueError("answer episode IDs must be a non-empty frozen-canary subset")
        frozen_ids = [episode_id for episode_id in frozen_ids if episode_id in answer_ids]
    qa_rows = build_qa_rows(load_jsonl(DATASET), answer_rows, frozen_ids)
    expected_questions = 12 * len(frozen_ids)
    if len(qa_rows) != expected_questions:
        raise ValueError(
            f"expected {expected_questions} frozen canary questions, got {len(qa_rows)}"
        )

    args.output.mkdir(parents=True, exist_ok=args.resume_after_empty_responses)
    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT / "scripts"))
    sys.path.insert(0, str(AMA_ROOT))
    from run_ama_official_canary_260918 import (  # noqa: PLC0415
        UsageLoggingClient,
        aggregate_usage,
    )
    from src.evaluate import evaluate_batch  # noqa: PLC0415

    judge = UsageLoggingClient(config, args.output)
    if args.resume_after_empty_responses:
        evaluated = recover_from_receipts(qa_rows, judge, args.output)
    else:
        evaluated = evaluate_batch(qa_rows, judge, max_workers=args.max_workers)
    evaluated.sort(key=lambda row: (frozen_ids.index(str(row["episode_id"])), row["question_uuid"]))
    summary = {
        "schema_version": "ama_external_canary_development_judge_v1",
        "status": "completed",
        "benchmark": "AMA-Bench open-end QA",
        "method": "official_llm_as_judge_prompt",
        "development_only": True,
        "independence_warning": "answer model and judge model are both Qwen3.8-27B",
        "evaluated_episode_ids": frozen_ids,
        "frozen_subset_evaluation": len(frozen_ids) < len(frozen_rows),
        "protocol_repairs": {
            mode: sum(row.get("judge_call_mode") == mode for row in evaluated)
            for mode in sorted(
                {
                    str(row.get("judge_call_mode"))
                    for row in evaluated
                    if row.get("judge_call_mode") != "official_prompt"
                }
            )
        },
        "answers_sha256": sha256_file(args.answers),
        "dataset_sha256": DATASET_SHA256,
        "freeze_sha256": FREEZE_SHA256,
        "config_sha256": sha256_file(CONFIG),
        "judge_model": judge.model,
        "usage": aggregate_usage(args.output / "provider_calls"),
        **summarize(evaluated),
        "results": evaluated,
    }
    atomic_json(args.output / "evaluation.json", summary)
    print(json.dumps({key: summary[key] for key in ("status", "overall", "by_episode")}, indent=2))


if __name__ == "__main__":
    main()
