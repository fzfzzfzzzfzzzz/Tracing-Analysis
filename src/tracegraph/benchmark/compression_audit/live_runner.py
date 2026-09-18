"""Definitions moved from ``tracegraph.compression_audit_live``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence
from ...capture import estimate_tokens
from ...compression_audit import BENCHMARK_ID, EpisodeRecord, FailureChainGold, PrefixRecord, QueryRecord, canonical_json, file_sha256, git_provenance, implementation_provenance, load_config, load_jsonl, stable_digest, verify_file_manifest, write_file_manifest
from ...compression_audit_runtime import answer_tool_schema, load_dataset, parse_submit_answer, prepare_v0_trials, reacquisition_tool_schema
from ...compression_audit_tokenization import VerifiedContextTokenizer, retokenize_trials
from ...live_guard import require_live_authorization_id

from .live_constants import (
    LIVE_RUN_SCHEMA_VERSION as LIVE_RUN_SCHEMA_VERSION,
)
from .development_protocol import DEVELOPMENT_ONLY_NOTICE



def _run_live_v0_locked(
    config_path: Path,
    dataset_root: Path,
    output_root: Path,
    *,
    workspace: Path | None = None,
    max_new_requests: int | None = None,
    resume: bool = False,
    authorization_id: str | None = None,
) -> dict[str, Any]:
    config = load_config(config_path)
    require_live_authorization_id(
        config.get("v0_live", {}).get("authorization", {}), authorization_id
    )
    authorization = validate_live_authorization(config)
    if not authorization["valid"]:
        raise ValueError("; ".join(authorization["errors"]))
    root = (workspace or Path.cwd()).resolve()
    verify_file_manifest(dataset_root)
    if not output_root.exists():
        prepare_live_run(config_path, dataset_root, output_root)
    elif not resume:
        raise FileExistsError("live run output exists; pass resume only for this frozen run")
    trials = load_jsonl(output_root / "trials.jsonl")
    preflight = json.loads((output_root / "preflight.json").read_text(encoding="utf-8"))
    if preflight["dataset_manifest_sha256"] != file_sha256(dataset_root / "manifest.json"):
        raise ValueError("live dataset differs from the frozen preflight")
    if preflight["trials_sha256"] != file_sha256(output_root / "trials.jsonl"):
        raise ValueError("live trial requests differ from the frozen preflight")
    if preflight["implementation"]["digest"] != implementation_provenance(root)["digest"]:
        raise ValueError("live implementation differs from the frozen preflight")
    snapshot = json.loads((output_root / "config.snapshot.json").read_text(encoding="utf-8"))
    if stable_digest(snapshot) != stable_digest(config):
        raise ValueError("live run config differs from the frozen snapshot")
    prefixes, queries, gold_rows = load_dataset(dataset_root, legacy=True)
    prefix_by_id = {item.prefix_id: item for item in prefixes}
    query_by_id = {item.query_id: item for item in queries}
    gold_by_id = {item.prefix_id: item for item in gold_rows}
    episode_path = output_root / "episodes.jsonl"
    ledger_path = output_root / "provider_ledger.jsonl"
    attempt_path = output_root / "provider_attempts.jsonl"
    existing_episodes = load_jsonl(episode_path) if episode_path.is_file() else []
    completed_ids = {str(item["episode_id"]) for item in existing_episodes}
    ledger = load_jsonl(ledger_path) if ledger_path.is_file() else []
    attempts = load_jsonl(attempt_path) if attempt_path.is_file() else []
    if len(attempts) != len(ledger) or any(
        left.get("request_sha256") != right.get("request_sha256")
        for left, right in zip(attempts, ledger, strict=True)
    ):
        raise RuntimeError("an attempted request has an uncertain outcome; automatic retry is forbidden")
    if any(not row.get("valid_usage") for row in ledger):
        raise RuntimeError("provider usage is uncertain; reconcile the ledger before further spending")
    if any(str(row["trial_id"]) not in completed_ids for row in ledger):
        raise RuntimeError("an episode was interrupted after spending; automatic replay is forbidden")
    totals = _existing_totals(ledger)
    api_key, endpoint = load_ignored_dashscope_credentials(root)
    context_tokenizer = VerifiedContextTokenizer(config["v0_live"]["context_tokenizer"], root)
    new_requests = 0
    stopped_reason: str | None = None
    for trial in trials:
        if str(trial["trial_id"]) in completed_ids:
            continue
        if max_new_requests is not None:
            remaining = max_new_requests - new_requests
            if remaining < int(trial["max_model_turns"]):
                stopped_reason = "max_new_requests_before_episode"
                break
        prefix = prefix_by_id[str(trial["prefix_id"])]
        query = query_by_id[str(trial["query_id"])]
        gold = gold_by_id[prefix.prefix_id]
        template = json.loads(json.dumps(trial["request_template"], ensure_ascii=False))
        messages = list(template["messages"])
        visible_ids = list(trial["artifact"]["visible_event_ids"])
        model_calls: list[dict[str, Any]] = []
        tool_calls: list[dict[str, Any]] = []
        answer: dict[str, Any] | None = None
        status = "max_turns"
        for turn_index in range(int(trial["max_model_turns"])):
            if max_new_requests is not None and new_requests >= max_new_requests:
                stopped_reason = "max_new_requests"
                break
            try:
                _check_next_request_budget(config, totals)
            except RuntimeError as error:
                stopped_reason = str(error)
                status = "budget_exhausted"
                break
            request_template = {
                **template,
                "messages": messages,
                "tools": _updated_tools(query, visible_ids),
                "tool_choice": (
                    "auto"
                    if query.track == "interactive_reacquisition"
                    else {
                        "type": "function",
                        "function": {"name": "submit_audit_answer"},
                    }
                ),
            }
            body = _provider_body(request_template, config)
            estimated = estimate_tokens(body)
            input_upper_bound = request_input_token_upper_bound(body)
            maximum_input = int(
                config["v0_live"]["limits"]["request_input_tokens_hard_max"]
            )
            if input_upper_bound > maximum_input:
                stopped_reason = "the next request cannot be proven below the input-token hard cap"
                status = "input_bound_exceeded"
                break
            attempt = {
                "attempt_index": int(totals["requests"]),
                "trial_id": trial["trial_id"],
                "turn_index": turn_index,
                "request_sha256": stable_digest(body),
                "request": body,
                "input_token_upper_bound": input_upper_bound,
                "estimated_input_tokens": estimated,
            }
            # Durable intent precedes transmission: an interrupted request is never retried.
            _append_jsonl(attempt_path, attempt)
            new_requests += 1
            started = time.perf_counter()
            response: dict[str, Any] | None = None
            latency = 0.0
            prompt_tokens = completion_tokens = None
            cost = None
            provider_error = None
            try:
                response, latency = _post_json(
                    endpoint,
                    api_key,
                    body,
                    timeout=int(config["v0_live"]["limits"]["timeout_seconds"]),
                )
                prompt_tokens, completion_tokens = _usage(response)
                cost = _price(config, prompt_tokens, completion_tokens)
            except Exception as error:
                # No retry or fallback. Preserve the attempt and reserve its worst-case cost.
                latency = time.perf_counter() - started
                provider_error = f"{type(error).__name__}: {error}"
            totals["requests"] += 1
            reserved_prompt = maximum_input if prompt_tokens is None else 0
            reserved_completion = 512 if completion_tokens is None else 0
            reserved_cost = _price(config, reserved_prompt, reserved_completion)
            totals["prompt_tokens"] += prompt_tokens or reserved_prompt
            totals["completion_tokens"] += completion_tokens or reserved_completion
            totals["cost_cny"] += cost or reserved_cost
            ledger_row = {
                "schema_version": LIVE_RUN_SCHEMA_VERSION,
                "trial_id": trial["trial_id"],
                "turn_index": turn_index,
                "model": config["v0_live"]["model"]["name"],
                "request_sha256": stable_digest(body),
                "response_sha256": stable_digest(response),
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "latency_seconds": latency,
                "cost_cny": cost,
                "valid_usage": provider_error is None,
                "provider_error": provider_error,
                "reserved_prompt_tokens": reserved_prompt,
                "reserved_completion_tokens": reserved_completion,
                "reserved_cost_cny": reserved_cost,
                "input_token_upper_bound": input_upper_bound,
                "retry_count": 0,
                "request": body,
                "response": response,
            }
            _append_jsonl(ledger_path, ledger_row)
            model_calls.append(ledger_row)
            if provider_error is not None:
                stopped_reason = "provider_error_or_missing_usage_no_retry"
                status = "provider_error"
                break
            if prompt_tokens > input_upper_bound or completion_tokens > 512:
                stopped_reason = "provider_usage_violated_the_proven_bound"
                status = "provider_bound_violation"
                break
            try:
                call = _response_tool_call(response)
                if call["name"] == "submit_audit_answer":
                    answer = parse_submit_answer(response)
                    status = "complete"
                    break
            except (ValueError, TypeError, KeyError, AttributeError):
                status = "invalid_structured_output"
                break
            if query.track != "interactive_reacquisition":
                status = "invalid_protocol"
                break
            if call["name"] not in query.allowed_tools:
                status = "invalid_tool"
                break
            tool_started = time.perf_counter()
            tool_result = _tool_result(
                call["name"], call["arguments"], prefix, gold, tool_calls
            )
            tool_result["latency_seconds"] = time.perf_counter() - tool_started
            tool_result["call_id"] = call["id"]
            tool_result["observation_tokens"] = context_tokenizer.count(tool_result["content"])
            tool_calls.append(tool_result)
            visible_ids.extend(tool_result["source_event_ids"])
            messages.append(call["assistant_message"])
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": canonical_json(tool_result["content"]),
                }
            )
        if not model_calls:
            break
        answer = answer or {
            "failed_action": "",
            "failed_arguments": {},
            "failure_cause": "",
            "diagnostic_evidence": "",
            "switch_decision": "",
            "replacement_action": "",
            "replacement_arguments": {},
            "resolution_evidence": "",
            "ordered_evidence_ids": [],
            "current_fact": "",
            "evidence_event_ids": [],
            "insufficient_history": True,
            "would_repeat_side_effect": False,
        }
        ingestion = trial["artifact"].get("provenance", {}).get("ingestion_usage", {})
        episode = EpisodeRecord(
            episode_id=str(trial["trial_id"]),
            prefix_id=prefix.prefix_id,
            query_id=query.query_id,
            method_id=str(trial["method_id"]),
            condition_id=str(trial["condition_id"]),
            model=config["v0_live"]["model"]["name"],
            seed=int(config["v0_live"]["seed"]),
            answer=answer,
            evidence_event_ids=tuple(str(item) for item in answer["evidence_event_ids"]),
            model_calls=tuple(model_calls),
            tool_calls=tuple(tool_calls),
            provider_input_tokens=(
                sum(int(item["prompt_tokens"]) for item in model_calls)
                if all(item["valid_usage"] for item in model_calls) else None
            ),
            provider_output_tokens=(
                sum(int(item["completion_tokens"]) for item in model_calls)
                if all(item["valid_usage"] for item in model_calls) else None
            ),
            tool_observation_tokens=sum(
                int(item["observation_tokens"]) for item in tool_calls
            ),
            latency_seconds=sum(float(item["latency_seconds"]) for item in (*model_calls, *tool_calls))
            + float(trial["artifact"].get("provenance", {}).get("retrieval_usage", {}).get("latency_seconds", 0.0)),
            cost_cny=(
                sum(float(item["cost_cny"]) for item in model_calls)
                if all(item["valid_usage"] for item in model_calls) else None
            ),
            compression_input_tokens=int(ingestion.get("provider_input_tokens", 0)),
            compression_output_tokens=int(ingestion.get("provider_output_tokens", 0)),
            compression_latency_seconds=float(ingestion.get("latency_seconds", 0.0)),
            compression_cost_cny=float(ingestion.get("cost_cny", 0.0)),
            unsafe_side_effect_attempts=sum(
                bool(item["unsafe_side_effect_attempt"]) for item in tool_calls
            ),
            executed_unauthorized_side_effects=sum(
                bool(item["executed_side_effect"]) for item in tool_calls
            ),
            status=status,
            request_hash=stable_digest(
                [str(item["request_sha256"]) for item in model_calls]
            ),
            response_hash=stable_digest(
                [str(item["response_sha256"]) for item in model_calls]
            ),
            artifact=dict(trial["artifact"]),
        )
        _append_jsonl(episode_path, episode.to_dict())
        completed_ids.add(episode.episode_id)
        if stopped_reason is not None:
            break
    final_episodes = load_jsonl(episode_path) if episode_path.is_file() else []
    status = "complete" if len(final_episodes) == len(trials) else "paused"
    summary = {
        "schema_version": LIVE_RUN_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "protocol": "v0.1-diagnostic",
        "development_only": True,
        "independent_validation": False,
        "interpretation": DEVELOPMENT_ONLY_NOTICE,
        "status": status,
        "stopped_reason": stopped_reason,
        "trial_count": len(trials),
        "completed_episode_count": len(final_episodes),
        "provider_requests": totals["requests"],
        "provider_input_tokens": totals["prompt_tokens"],
        "provider_output_tokens": totals["completion_tokens"],
        "provider_cost_cny": sum(
            float(item.get("cost_cny") or 0) for item in load_jsonl(ledger_path)
        ) if ledger_path.is_file() else 0.0,
        "provider_cost_upper_bound_cny": totals["cost_cny"],
        "usage_complete": all(
            item.get("valid_usage") for item in load_jsonl(ledger_path)
        ) if ledger_path.is_file() else True,
        "maximum_cost_cny": config["v0_live"]["limits"]["maximum_cost_cny"],
        "fallback_used": False,
        "provider_retry_count": 0,
        "executed_unauthorized_side_effects": sum(
            int(item.get("executed_unauthorized_side_effects", 0)) for item in final_episodes
        ),
    }
    _write_json(output_root / "run_summary.json", summary)
    artifacts = write_file_manifest(output_root)
    manifest = {
        **summary,
        "dataset_manifest_sha256": file_sha256(dataset_root / "manifest.json"),
        "config_snapshot_sha256": file_sha256(output_root / "config.snapshot.json"),
        "repository": git_provenance(root),
        "implementation": implementation_provenance(root),
        "artifacts": artifacts,
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


# Imported after definitions so mutually-referential helpers initialize safely.
from .live_authorization import (
    _append_jsonl as _append_jsonl,
    _write_json as _write_json,
    load_ignored_dashscope_credentials as load_ignored_dashscope_credentials,
    prepare_live_run as prepare_live_run,
    request_input_token_upper_bound as request_input_token_upper_bound,
    validate_live_authorization as validate_live_authorization,
)

from .live_provider import (
    _check_next_request_budget as _check_next_request_budget,
    _existing_totals as _existing_totals,
    _post_json as _post_json,
    _price as _price,
    _provider_body as _provider_body,
    _response_tool_call as _response_tool_call,
    _tool_result as _tool_result,
    _updated_tools as _updated_tools,
    _usage as _usage,
)
