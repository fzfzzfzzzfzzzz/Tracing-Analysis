"""Run query-hidden AMA canary comparisons under one uniform answer protocol.

This development runner changes only the trajectory memory shown to the model.
Every method receives the same task, question, prompt, decoding settings, and
one model call per question. Gold answers are used only by the separate judge.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
AMA_ROOT = ROOT / "vendor" / "ama-hub-ddfd319"
CONFIG = ROOT / "configs" / "ama_qwen38_27b_full_history_canary_260918.yaml"
DEFAULT_ROOT = ROOT / "outputs" / "ama_uniform_canary_260918"
METHODS = ("full_history", "flat_bm25_archive", "tracegraph_0_4")
MEMORY_BUDGET = 4096
INGEST_BUDGET = 2048
ANSWER_PROTOCOL_REVISION = "ama_uniform_single_question_v4_turn_anchored"
EMPTY_OUTPUT_REPAIR_REVISION = "ama_uniform_invalid_output_repair_v2_once"
TRAJECTORY_NORMALIZATION_REVISION = "ama_action_observation_turn_index_v2"

sys.path.insert(0, str(ROOT / "scripts"))
from run_ama_official_canary_260918 import (  # noqa: E402
    AMA_COMMIT,
    DATASET_SHA256,
    FREEZE,
    FREEZE_SHA256,
    TOKENIZER_SHA256,
    UsageLoggingClient,
    aggregate_usage,
    atomic_json,
    load_canary,
    stable_json,
    verify_inputs,
)

sys.path.insert(0, str(AMA_ROOT))
from utils.extract_final_answer import extract_final_answer  # noqa: E402

from tracegraph.benchmark.compression_audit.development_adapters import (  # noqa: E402
    make_development_adapter,
)
from tracegraph.benchmark.compression_audit.models import (  # noqa: E402
    PrefixRecord,
    QueryRecord,
)
from tracegraph.compression_audit_real import (  # noqa: E402
    FAILURE_RE,
    normalize_ama_events,
)
from tracegraph.compression_audit_tokenization import (  # noqa: E402
    VerifiedContextTokenizer,
)


def append_jsonl(path: Path, value: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(stable_json(value) + "\n")
        handle.flush()


def failure_observation(value: Any) -> bool:
    if isinstance(value, dict):
        if value.get("error") or value.get("success") is False:
            return True
    text = value if isinstance(value, str) else stable_json(value)
    return bool(FAILURE_RE.search(text))


def make_prefix(episode: dict[str, Any], budget: int) -> PrefixRecord:
    raw_events, raw_messages = normalize_ama_events(episode)
    events = []
    for index, raw in enumerate(raw_events, 1):
        event = dict(raw)
        event["event_id"] = str(event.pop("source_event_id"))
        turn_match = re.match(r"t(\d+):", event["event_id"])
        if turn_match is None:
            raise ValueError(f"AMA event lacks a turn anchor: {event['event_id']}")
        turn_idx = int(turn_match.group(1))
        original_content = event.get("content")
        event["step_id"] = index
        if event["kind"] == "tool_result":
            event["kind"] = (
                "error" if failure_observation(original_content) else "observation"
            )
            event["content"] = {
                "turn_idx": turn_idx,
                "observation": original_content,
            }
        elif isinstance(original_content, dict):
            event["content"] = {"turn_idx": turn_idx, **original_content}
        else:
            event["content"] = {"turn_idx": turn_idx, "action": original_content}
        events.append(event)
    episode_id = str(episode["episode_id"])
    return PrefixRecord(
        prefix_id=f"ama-{episode_id}",
        source_kind="ama_bench_open_end_qa",
        source_ref={
            "episode_id": episode_id,
            "task_type": str(episode.get("task_type") or "unknown"),
            "source_commit": AMA_COMMIT,
            "dataset_sha256": DATASET_SHA256,
        },
        split="dev",
        failure_family="external_ama_unlabeled",
        task_domain=str(episode.get("task_type") or episode.get("domain") or "unknown"),
        recoverability="R3",
        context_length="external",
        budget_tokens=budget,
        events=tuple(events),
        messages=tuple(raw_messages),
        tool_schemas=(),
        environment_snapshot={
            "task_sha256": hashlib.sha256(
                str(episode.get("task") or "").encode("utf-8")
            ).hexdigest()
        },
        future_query_hidden=True,
    )


def answer_prompt(task: str, records: tuple[dict[str, Any], ...], question: str) -> str:
    memory = json.dumps(list(records), ensure_ascii=False, separators=(",", ":"))
    return f"""## Task Description
{task}

## Agent Trajectory Memory
The following records are chronological evidence from the agent trajectory. Record IDs encode the original step. Base the answer only on the task and these records.
{memory}

## Questions
Question 1: {question}

## Instructions
Please answer the question based on the task description and agent trajectory memory above. Provide a direct and concise answer.
Return only the substantive answer text. Do not output an answer label, preamble, or placeholder. Your answer must be at least one complete sentence."""


def parse_answer(response: str) -> str:
    text = re.sub(r"^\s*Answer\[1\]:\s*", "", response, flags=re.IGNORECASE).strip()
    return extract_final_answer(f"###Answer: {text}", mcq_mode=False)


def substantive_answer(response: str) -> bool:
    answer = parse_answer(response).strip()
    if len(answer) < 12:
        return False
    if answer.casefold() in {"answer", "answer:", "based", "looking", "let"}:
        return False
    return len(re.findall(r"[\w.-]+", answer)) >= 3


def query_with_one_empty_repair(
    client: UsageLoggingClient, prompt: str
) -> tuple[str, str, str]:
    """Use one frozen repair prompt; never silently loop or change context."""
    try:
        response = client.query(
            prompt, temperature=0.0, max_tokens=2048, max_retries=1
        )
        if substantive_answer(response):
            return response, "primary", prompt
    except ValueError as error:
        if "empty visible content" not in str(error):
            raise
    repair = (
        prompt
        + "\n\nThe previous transport attempt returned no visible answer. "
        + "Respond now with only a substantive answer of at least one complete sentence. "
        + "Do not output a label, preamble, or placeholder."
    )
    response = client.query(
        repair, temperature=0.0, max_tokens=2048, max_retries=1
    )
    if not substantive_answer(response):
        raise ValueError("repair returned a non-substantive visible answer")
    return response, "invalid_output_repair", repair


def load_tokenizer() -> VerifiedContextTokenizer:
    return VerifiedContextTokenizer(
        {
            "path": "data/model_assets/Qwen3.8-27B-rev-1d4bf0f-tokenizer/tokenizer.json",
            "sha256": TOKENIZER_SHA256,
            "model": "Qwen3.8-27B-rev-1d4bf0f",
            "source": "pinned_local_official_tokenizer",
        },
        ROOT,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", choices=METHODS, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--episode-limit", type=int, default=0)
    parser.add_argument("--memory-budget", type=int, default=MEMORY_BUDGET)
    parser.add_argument("--ingest-budget", type=int, default=INGEST_BUDGET)
    parser.add_argument("--freeze-path", type=Path)
    parser.add_argument("--expected-freeze-hash")
    parser.add_argument("--expected-episode-count", type=int, default=5)
    parser.add_argument("--base-url")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--continue-on-unsafe", action="store_true")
    args = parser.parse_args()

    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    config["sampling"] = dict(config["sampling"])
    config["sampling"]["min_tokens"] = 32
    if args.base_url:
        config["base_url"] = args.base_url
    if not 256 <= args.ingest_budget < args.memory_budget:
        raise ValueError("budgets must satisfy 256 <= ingest-budget < memory-budget")
    active_freeze = (args.freeze_path or FREEZE).resolve()
    active_freeze_hash = args.expected_freeze_hash or FREEZE_SHA256
    frozen = verify_inputs(
        config,
        freeze=active_freeze,
        freeze_sha256=active_freeze_hash,
        expected_count=args.expected_episode_count,
    )
    episodes = load_canary(frozen)
    if args.episode_limit:
        if not 1 <= args.episode_limit <= len(episodes):
            raise ValueError(
                f"episode-limit must select 1..{len(episodes)} frozen episodes"
            )
        episodes = episodes[: args.episode_limit]
    output = (
        args.output
        or DEFAULT_ROOT
        / f"{args.method}_b{args.memory_budget}_i{args.ingest_budget}_q1_v4"
    ).resolve()

    tokenizer = load_tokenizer()
    prefixes = [make_prefix(episode, args.memory_budget) for episode in episodes]
    adapter = make_development_adapter(
        args.method,
        token_counter=tokenizer.count,
        ingest_budget=args.ingest_budget,
    )
    readiness = {
        "status": "ready",
        "method": args.method,
        "episode_ids": [str(row["episode_id"]) for row in episodes],
        "question_count": sum(len(row.get("qa_pairs", [])) for row in episodes),
        "memory_budget_tokens": args.memory_budget,
        "ingest_budget_tokens": args.ingest_budget,
        "uniform_single_question_protocol": True,
        "answer_protocol_revision": ANSWER_PROTOCOL_REVISION,
        "empty_output_repair_revision": EMPTY_OUTPUT_REPAIR_REVISION,
        "minimum_completion_tokens": 32,
        "trajectory_normalization_revision": TRAJECTORY_NORMALIZATION_REVISION,
        "gold_answers_sent_to_model": False,
    }
    if args.dry_run:
        preflight = []
        for episode, prefix in zip(episodes, prefixes, strict=True):
            state = adapter.ingest(prefix, args.memory_budget)
            bundles = []
            missing_explicit_turn_anchors = []
            for question_index, pair in enumerate(episode.get("qa_pairs", []), 1):
                query = QueryRecord(
                    query_id=f"ama-{episode['episode_id']}-q{question_index:02d}",
                    prefix_id=prefix.prefix_id,
                    track="audit_qa",
                    query_type="audit_chain",
                    text=str(pair.get("question") or ""),
                    allowed_tools=(),
                    required_fields=(),
                )
                bundle = adapter.materialize(state, query, args.memory_budget)
                bundles.append(bundle)
                referenced_turns = {
                    int(value)
                    for value in re.findall(
                        r"\b(?:step|turn)\s+(\d+)\b", query.text, re.IGNORECASE
                    )
                }
                visible_turns = {
                    int(match.group(1))
                    for event_id in bundle.visible_event_ids
                    if (match := re.match(r"t(\d+):", event_id))
                }
                if referenced_turns - visible_turns:
                    missing_explicit_turn_anchors.append(query.query_id)
            preflight.append(
                {
                    "episode_id": str(episode["episode_id"]),
                    "send_eligible": all(
                        bundle.retrieval_usage.get("send_eligible") is True
                        for bundle in bundles
                    ),
                    "safety_reasons": sorted(
                        {
                            str(reason)
                            for bundle in bundles
                            for reason in bundle.retrieval_usage.get("safety_reasons", [])
                        }
                    ),
                    "context_tokens_min": min(bundle.token_count for bundle in bundles),
                    "context_tokens_max": max(bundle.token_count for bundle in bundles),
                    "missing_explicit_turn_anchor_queries": missing_explicit_turn_anchors,
                }
            )
        readiness["preflight"] = preflight
        print(stable_json(readiness))
        return

    if output.exists() != args.resume:
        raise ValueError("use a new output directory, or pass --resume")
    output.mkdir(parents=True, exist_ok=True)
    answers_path = output / "answers.jsonl"
    artifacts_path = output / "memory_artifacts.jsonl"
    question_results_path = output / "question_results.jsonl"
    completed = set()
    if answers_path.exists():
        completed = {
            str(json.loads(line)["episode_id"])
            for line in answers_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }
    recovered_questions = {}
    if question_results_path.exists():
        recovered_questions = {
            (str(row["episode_id"]), str(row["question_uuid"])): row
            for row in (
                json.loads(line)
                for line in question_results_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
        }

    manifest = {
        "schema_version": "ama_uniform_memory_canary_v1",
        **readiness,
        "status": "running",
        "benchmark": "AMA-Bench open-end QA",
        "source_commit": AMA_COMMIT,
        "dataset_sha256": DATASET_SHA256,
        "freeze_sha256": active_freeze_hash,
        "freeze_path": str(active_freeze),
        "config_sha256": hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
        "tokenizer": tokenizer.provenance,
        "development_only": True,
        "confirmation_pool_sent_to_model": False,
        "query_hidden_ingestion": True,
        "answer_protocol": "one identical direct-answer call per question",
        "answer_protocol_revision": ANSWER_PROTOCOL_REVISION,
        "empty_output_repair_revision": EMPTY_OUTPUT_REPAIR_REVISION,
        "minimum_completion_tokens": 32,
        "trajectory_normalization_revision": TRAJECTORY_NORMALIZATION_REVISION,
    }
    atomic_json(output / "run_manifest.json", manifest)
    client = UsageLoggingClient(config, output)

    for episode_index, (episode, prefix) in enumerate(zip(episodes, prefixes, strict=True), 1):
        episode_id = str(episode["episode_id"])
        if episode_id in completed:
            continue
        print(
            f"AMA uniform {args.method} episode {episode_index}/{len(episodes)}: {episode_id}",
            flush=True,
        )
        state = adapter.ingest(prefix, args.memory_budget)
        generated = []
        staged_artifacts = []
        for question_index, pair in enumerate(episode.get("qa_pairs", []), 1):
            recovery_key = (episode_id, str(pair.get("question_uuid")))
            if recovery_key in recovered_questions:
                recovered = recovered_questions[recovery_key]
                generated.append(dict(recovered["answer"]))
                staged_artifacts.append(dict(recovered["artifact"]))
                continue
            question = str(pair.get("question") or "")
            query = QueryRecord(
                query_id=f"ama-{episode_id}-q{question_index:02d}",
                prefix_id=prefix.prefix_id,
                track="audit_qa",
                query_type="audit_chain",
                text=question,
                allowed_tools=(),
                required_fields=(),
                independent_reset=True,
            )
            bundle = adapter.materialize(state, query, args.memory_budget)
            if bundle.retrieval_usage.get("send_eligible") is not True:
                if not args.continue_on_unsafe:
                    raise RuntimeError(
                        f"unsafe memory plan for {query.query_id}: "
                        f"{bundle.retrieval_usage.get('safety_reasons')}"
                    )
                answer = {
                    "question_uuid": pair.get("question_uuid"),
                    "qa_type": pair.get("type"),
                    "predicted_answer": (
                        "Unable to answer from the available bounded memory."
                    ),
                }
                artifact = {
                    "episode_id": episode_id,
                    "question_uuid": pair.get("question_uuid"),
                    "query_id": query.query_id,
                    "query_hash": query.query_hash,
                    "method": args.method,
                    "state_hash": state.state_hash,
                    "context_hash": bundle.context_hash,
                    "context_tokens": bundle.token_count,
                    "budget_tokens": bundle.budget_tokens,
                    "visible_event_ids": list(bundle.visible_event_ids),
                    "retrieved_event_ids": list(bundle.retrieved_event_ids),
                    "retrieval_usage": dict(bundle.retrieval_usage),
                    "call_mode": "safe_unavailable",
                    "run_validity": "method_failure",
                    "provider_call_made": False,
                }
                generated.append(answer)
                staged_artifacts.append(artifact)
                append_jsonl(
                    question_results_path,
                    {
                        "episode_id": episode_id,
                        "question_uuid": pair.get("question_uuid"),
                        "answer": answer,
                        "artifact": artifact,
                    },
                )
                continue
            prompt = answer_prompt(str(episode.get("task") or ""), bundle.records, question)
            response, call_mode, selected_prompt = query_with_one_empty_repair(
                client, prompt
            )
            answer = {
                "question_uuid": pair.get("question_uuid"),
                "qa_type": pair.get("type"),
                "predicted_answer": parse_answer(response),
            }
            artifact = {
                    "episode_id": episode_id,
                    "question_uuid": pair.get("question_uuid"),
                    "query_id": query.query_id,
                    "query_hash": query.query_hash,
                    "method": args.method,
                    "state_hash": state.state_hash,
                    "context_hash": bundle.context_hash,
                    "context_tokens": bundle.token_count,
                    "budget_tokens": bundle.budget_tokens,
                    "visible_event_ids": list(bundle.visible_event_ids),
                    "retrieved_event_ids": list(bundle.retrieved_event_ids),
                    "retrieval_usage": dict(bundle.retrieval_usage),
                    "primary_prompt_sha256": hashlib.sha256(
                        prompt.encode("utf-8")
                    ).hexdigest(),
                    "selected_prompt_sha256": hashlib.sha256(
                        selected_prompt.encode("utf-8")
                    ).hexdigest(),
                    "call_mode": call_mode,
                    "run_validity": "valid",
                    "provider_call_made": True,
            }
            generated.append(answer)
            staged_artifacts.append(artifact)
            append_jsonl(
                question_results_path,
                {
                    "episode_id": episode_id,
                    "question_uuid": pair.get("question_uuid"),
                    "answer": answer,
                    "artifact": artifact,
                },
            )
        append_jsonl(
            answers_path,
            {
                "episode_id": episode_id,
                "task_type": episode.get("task_type"),
                "answers": generated,
            },
        )
        for artifact in staged_artifacts:
            append_jsonl(artifacts_path, artifact)

    manifest["status"] = "completed"
    manifest["usage"] = aggregate_usage(output / "provider_calls")
    manifest["completed_episode_count"] = len(episodes)
    manifest["completed_question_count"] = sum(
        len(row.get("qa_pairs", [])) for row in episodes
    )
    atomic_json(output / "run_manifest.json", manifest)
    print(stable_json({"status": "completed", "usage": manifest["usage"]}))


if __name__ == "__main__":
    main()
