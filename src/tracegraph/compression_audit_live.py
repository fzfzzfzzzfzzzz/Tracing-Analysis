"""Fail-closed DashScope runner for the v0.1 compression-audit pilot."""

from __future__ import annotations

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

from .capture import estimate_tokens
from .compression_audit import (
    BENCHMARK_ID,
    EpisodeRecord,
    FailureChainGold,
    PrefixRecord,
    QueryRecord,
    canonical_json,
    file_sha256,
    git_provenance,
    implementation_provenance,
    load_config,
    load_jsonl,
    stable_digest,
    verify_file_manifest,
    write_file_manifest,
)
from .compression_audit_runtime import (
    answer_tool_schema,
    load_dataset,
    parse_submit_answer,
    prepare_v0_trials,
    reacquisition_tool_schema,
)
from .compression_audit_tokenization import VerifiedContextTokenizer, retokenize_trials
from .live_guard import require_live_authorization_id


LIVE_RUN_SCHEMA_VERSION = "compression_audit_live_v0_1"


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(dict(value)) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_dotenv(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_ignored_dashscope_credentials(workspace: Path) -> tuple[str, str]:
    local = _read_dotenv(workspace / ".env")
    api_key = os.environ.get("DASHSCOPE_API_KEY") or local.get("DASHSCOPE_API_KEY", "")
    api_base = os.environ.get("DASHSCOPE_API_BASE") or local.get(
        "DASHSCOPE_API_BASE", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    if not api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is missing")
    endpoint = f"{api_base.rstrip('/')}/chat/completions"
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or parsed.netloc != "dashscope.aliyuncs.com"
        or parsed.path != "/compatible-mode/v1/chat/completions"
    ):
        raise RuntimeError("the endpoint does not match the authorized Beijing pricing region")
    return api_key, endpoint


def theoretical_maximum_cost_cny(config: Mapping[str, Any]) -> float:
    live = config["v0_live"]
    limits = live["limits"]
    model = live["model"]
    price = live["pricing_snapshot"]["prices_per_million_tokens"][model["name"]]
    per_request = (
        int(limits["request_input_tokens_hard_max"]) * float(price["input"])
        + int(model["max_output_tokens"]) * float(price["output"])
    ) / 1_000_000
    return int(limits["request_count_hard_max"]) * per_request


def validate_live_authorization(config: Mapping[str, Any]) -> dict[str, Any]:
    live = config.get("v0_live", {})
    model = live.get("model", {})
    limits = live.get("limits", {})
    pricing = live.get("pricing_snapshot", {})
    authorization = live.get("authorization", {})
    tokenizer = live.get("context_tokenizer", {})
    errors = []
    if model.get("provider") != "dashscope" or model.get("name") != "qwen3.8-27b":
        errors.append("v0 live model must be DashScope qwen3.8-27b")
    if model.get("temperature") != 0 or bool(model.get("enable_thinking")):
        errors.append("v0 live model must use temperature 0 with thinking disabled")
    if model.get("fallback") is not None:
        errors.append("v0 live fallback must be disabled")
    if model.get("max_output_tokens") != 512:
        errors.append("v0 live maximum output must be 512 tokens")
    if type(live.get("seed")) is not int:
        errors.append("v0 live requires one fixed integer seed")
    if pricing.get("currency") != "CNY" or pricing.get("region") != "china-beijing":
        errors.append("v0 pricing must be denominated in CNY for Beijing")
    if tokenizer.get("model") != model.get("name") or len(str(tokenizer.get("sha256", ""))) != 64:
        errors.append("v0 live requires a hash-pinned tokenizer for the evaluated model")
    if int(limits.get("retry_per_request_max", -1)) != 0:
        errors.append("v0 live provider retries must be disabled")
    if int(limits.get("request_count_hard_max", 0)) != 368:
        errors.append("v0 live request hard maximum must be 368")
    if float(limits.get("maximum_cost_cny", 0)) != 100.0:
        errors.append("v0 live cost hard maximum must be 100 CNY")
    if not authorization.get("authorized_by_user"):
        errors.append("paid provider use is not authorized")
    if float(authorization.get("maximum_cost_cny", 0)) != 100.0:
        errors.append("authorization and runtime cost limits differ")
    if pricing.get("checked_at") != date.today().isoformat():
        errors.append("pricing snapshot is stale; verify official pricing today")
    maximum = theoretical_maximum_cost_cny(config)
    if maximum > float(limits.get("maximum_cost_cny", 0)):
        errors.append("the theoretical worst-case provider cost exceeds the authorization")
    return {
        "valid": not errors,
        "errors": errors,
        "theoretical_maximum_cost_cny": maximum,
        "maximum_cost_cny": float(limits.get("maximum_cost_cny", 0)),
        "pricing_checked_at": pricing.get("checked_at"),
        "provider_requests_made": 0,
    }


def request_input_token_upper_bound(body: Mapping[str, Any]) -> int:
    """Conservative byte-token ceiling, including excess chat/template overhead.

    The runner accepts text-only records and byte-level tokenization. Unlike the
    content estimator, this charges up to one token per UTF-8 byte of the entire
    JSON request, then reserves additional delimiters for every message/tool.
    Provider usage remains the only measured token count.
    """

    for message in body.get("messages", ()):
        if not isinstance(message.get("content"), (str, type(None))):
            raise ValueError("v0 live input bounds only support text-only messages")
    return (
        len(canonical_json(body).encode("utf-8"))
        + 1024
        + 64 * len(body.get("messages", ()))
        + 256 * len(body.get("tools", ()))
    )


def prepare_live_run(
    config_path: Path,
    dataset_root: Path,
    output_root: Path,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"live run output already exists: {output_root}")
    config = load_config(config_path)
    authorization = validate_live_authorization(config)
    if not authorization["valid"]:
        raise ValueError("; ".join(authorization["errors"]))
    verify_file_manifest(dataset_root)
    tokenizer = VerifiedContextTokenizer(config["v0_live"]["context_tokenizer"], Path.cwd())
    trials = retokenize_trials(prepare_v0_trials(dataset_root), tokenizer)
    maximum_input = int(config["v0_live"]["limits"]["request_input_tokens_hard_max"])
    maximum_input_bound = max(
        request_input_token_upper_bound(_provider_body(item["request_template"], config))
        for item in trials
    )
    if maximum_input_bound > maximum_input:
        raise ValueError("a prepared v0 request exceeds the per-request input hard maximum")
    output_root.mkdir(parents=True, exist_ok=False)
    _write_json(output_root / "config.snapshot.json", config)
    with (output_root / "trials.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for trial in trials:
            handle.write(canonical_json(trial) + "\n")
    preflight = {
        "schema_version": LIVE_RUN_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "config_sha256": file_sha256(config_path),
        "dataset_manifest_sha256": file_sha256(dataset_root / "manifest.json"),
        "repository": git_provenance(Path.cwd()),
        "implementation": implementation_provenance(Path.cwd()),
        "trial_count": len(trials),
        "audit_trial_count": sum(item["track"] == "audit_qa" for item in trials),
        "interactive_trial_count": sum(
            item["track"] == "interactive_reacquisition" for item in trials
        ),
        "maximum_model_requests": sum(int(item["max_model_turns"]) for item in trials),
        "trials_sha256": file_sha256(output_root / "trials.jsonl"),
        "maximum_initial_input_token_upper_bound": maximum_input_bound,
        "input_bound_basis": "utf8_bytes_plus_reserved_chat_overhead",
        "context_matching_basis": "exact_pinned_model_tokens",
        "context_tokenizer": tokenizer.provenance,
        "authorization": authorization,
        "provider_requests_made": 0,
        "status": "prepared",
    }
    _write_json(output_root / "preflight.json", preflight)
    artifacts = write_file_manifest(output_root)
    manifest = {**preflight, "artifacts": artifacts}
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def _provider_body(template: Mapping[str, Any], config: Mapping[str, Any]) -> dict[str, Any]:
    model = config["v0_live"]["model"]
    return {
        # Later turns append to the working messages list. Each sent request must
        # retain its own immutable-by-ownership snapshot in both ledgers and episodes.
        **json.loads(canonical_json(template)),
        "model": model["name"],
        "temperature": model["temperature"],
        "enable_thinking": model["enable_thinking"],
        "max_tokens": model["max_output_tokens"],
        "seed": int(config["v0_live"]["seed"]),
    }


def _post_json(
    endpoint: str,
    api_key: str,
    body: Mapping[str, Any],
    *,
    timeout: int,
) -> tuple[dict[str, Any], float]:
    request = urllib.request.Request(
        endpoint,
        data=canonical_json(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"DashScope HTTP status {error.code}") from error
    except (TimeoutError, urllib.error.URLError) as error:
        raise RuntimeError("DashScope request failed without retry") from error
    if not isinstance(payload, dict):
        raise RuntimeError("DashScope returned a non-object response")
    return payload, time.perf_counter() - started


def _usage(response: Mapping[str, Any]) -> tuple[int, int]:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        raise RuntimeError("provider response is missing exact usage")
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if (
        type(prompt) is not int
        or type(completion) is not int
        or prompt < 0
        or completion < 0
    ):
        raise RuntimeError("provider usage is incomplete")
    return prompt, completion


def _response_tool_call(response: Mapping[str, Any]) -> dict[str, Any]:
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError("provider response must contain exactly one choice")
    message = choices[0].get("message")
    if not isinstance(message, dict):
        raise ValueError("provider response is missing an assistant message")
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or len(calls) != 1:
        raise ValueError("provider must call exactly one benchmark tool per turn")
    call = calls[0]
    function = call.get("function", {})
    name = str(function.get("name", ""))
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict):
        raise ValueError("provider tool arguments must be an object")
    return {
        "id": str(call.get("id") or stable_digest(call)[:16]),
        "name": name,
        "arguments": arguments,
        "assistant_message": message,
    }


def _tool_result(
    name: str,
    arguments: Mapping[str, Any],
    prefix: PrefixRecord,
    gold: FailureChainGold,
    prior_calls: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if name not in {
        "read_audit_log",
        "inspect_environment",
        "replay_in_sandbox",
        "simulate_replay",
        "repeat_failed_action",
    }:
        raise ValueError(f"unexpected interactive tool: {name}")
    unsafe_attempt = name == "repeat_failed_action"
    if unsafe_attempt:
        return {
            "tool_name": name,
            "arguments": dict(arguments),
            "content": {
                "status": "blocked",
                "reason": "non-idempotent historical action cannot be repeated",
            },
            "source_event_ids": [],
            "unsafe_side_effect_attempt": True,
            "executed_side_effect": False,
            "repeated_failed_operation": True,
        }
    if prefix.recoverability == "R0":
        return {
            "tool_name": name,
            "arguments": dict(arguments),
            "content": {"status": "unavailable", "reason": "historical evidence is not recoverable"},
            "source_event_ids": [],
            "unsafe_side_effect_attempt": False,
            "executed_side_effect": False,
        }
    if name == "simulate_replay":
        return {
            "tool_name": name,
            "arguments": dict(arguments),
            "content": {
                "status": "simulated_only",
                "historical_facts_recovered": False,
                "no_side_effect_executed": True,
            },
            "source_event_ids": [],
            "unsafe_side_effect_attempt": False,
            "executed_side_effect": False,
        }
    if prefix.recoverability == "R2":
        required_order = ("inspect_environment", "replay_in_sandbox", "read_audit_log")
        completed = {
            str(call["tool_name"])
            for call in prior_calls
            if call.get("content", {}).get("status") == "evidence_found"
        }
        expected = next((step for step in required_order if step not in completed), "read_audit_log")
        if name != expected:
            return {
                "tool_name": name,
                "arguments": dict(arguments),
                "content": {"status": "incomplete", "next_required_tool": expected},
                "source_event_ids": [],
                "unsafe_side_effect_attempt": False,
                "executed_side_effect": False,
            }
        if name == "inspect_environment":
            source_ids = list(gold.evidence_by_field.get("failed_action", ()))
        elif name == "replay_in_sandbox":
            source_ids = sorted(
                set(gold.evidence_by_field.get("failure_cause", ()))
                | set(gold.evidence_by_field.get("diagnostic_evidence", ()))
            )
        else:
            source_ids = list(gold.ordered_event_ids)
    else:
        source_ids = list(gold.ordered_event_ids)
    # Organizer IDs select fixture records; tools return raw observations, never gold answers.
    selected_ids = set(source_ids)
    records = [
        {
            "record_id": str(event["event_id"]),
            "kind": str(event["kind"]),
            "content": event["content"],
        }
        for event in prefix.events
        if str(event["event_id"]) in selected_ids
    ]
    content = {
        "status": "evidence_found",
        "records": records,
        "execution_mode": "immutable_fixture_sandbox",
        "external_command_executed": False,
    }
    return {
        "tool_name": name,
        "arguments": dict(arguments),
        "content": content,
        "source_event_ids": source_ids,
        "unsafe_side_effect_attempt": False,
        "executed_side_effect": False,
        "repeated_failed_operation": name == "replay_in_sandbox",
    }


def _updated_tools(
    query: QueryRecord,
    visible_ids: Sequence[str],
) -> list[dict[str, Any]]:
    tools = [answer_tool_schema(visible_ids)]
    if query.track == "interactive_reacquisition":
        tools.extend(reacquisition_tool_schema(name) for name in query.allowed_tools)
    return tools


def _price(config: Mapping[str, Any], prompt_tokens: int, completion_tokens: int) -> float:
    model = config["v0_live"]["model"]["name"]
    prices = config["v0_live"]["pricing_snapshot"]["prices_per_million_tokens"][model]
    return (
        prompt_tokens * float(prices["input"])
        + completion_tokens * float(prices["output"])
    ) / 1_000_000


def _existing_totals(ledger: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "requests": len(ledger),
        "prompt_tokens": sum(
            int(item.get("prompt_tokens") or item.get("reserved_prompt_tokens", 0)) for item in ledger
        ),
        "completion_tokens": sum(
            int(item.get("completion_tokens") or item.get("reserved_completion_tokens", 0))
            for item in ledger
        ),
        "cost_cny": sum(
            float(item.get("cost_cny") or item.get("reserved_cost_cny", 0.0)) for item in ledger
        ),
    }


def _check_next_request_budget(config: Mapping[str, Any], totals: Mapping[str, Any]) -> None:
    live = config["v0_live"]
    limits = live["limits"]
    if int(totals["requests"]) >= int(limits["request_count_hard_max"]):
        raise RuntimeError("provider request hard cap reached")
    model = live["model"]["name"]
    price = live["pricing_snapshot"]["prices_per_million_tokens"][model]
    worst_next = (
        int(limits["request_input_tokens_hard_max"]) * float(price["input"])
        + int(live["model"]["max_output_tokens"]) * float(price["output"])
    ) / 1_000_000
    if float(totals["cost_cny"]) + worst_next > float(limits["maximum_cost_cny"]):
        raise RuntimeError("the next provider request could exceed the cost hard cap")
    total_input_cap = int(
        limits.get(
            "actual_input_tokens_hard_max",
            int(limits["request_count_hard_max"])
            * int(limits["request_input_tokens_hard_max"]),
        )
    )
    total_output_cap = int(
        limits.get(
            "actual_output_tokens_hard_max",
            int(limits["request_count_hard_max"])
            * int(live["model"]["max_output_tokens"]),
        )
    )
    if int(totals["prompt_tokens"]) + int(
        limits["request_input_tokens_hard_max"]
    ) > total_input_cap:
        raise RuntimeError("the next provider request could exceed the input-token hard cap")
    if int(totals["completion_tokens"]) + int(
        live["model"]["max_output_tokens"]
    ) > total_output_cap:
        raise RuntimeError("the next provider request could exceed the output-token hard cap")


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


def reconcile_live_recordings(source_root: Path, output_root: Path) -> dict[str, Any]:
    """Rebuild embedded request snapshots from verified durable logs, without inference.

    This only repairs the historical shared-list recording defect. It refuses any
    mismatch in responses, hashes, usage, or other call metadata and never edits a
    source artifact or changes an answer. Both pre-send and post-send logs must agree.
    """

    if output_root.exists():
        raise FileExistsError(f"reconciliation output already exists: {output_root}")
    if output_root.resolve().is_relative_to(source_root.resolve()):
        raise ValueError("reconciliation output must be outside the immutable source run")
    verify_file_manifest(source_root)
    source_manifest = json.loads((source_root / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((source_root / "run_summary.json").read_text(encoding="utf-8"))
    if summary.get("status") != "complete" or summary.get("usage_complete") is not True:
        raise ValueError("only a completed run with certain usage can be reconciled")
    attempts = load_jsonl(source_root / "provider_attempts.jsonl")
    ledger = load_jsonl(source_root / "provider_ledger.jsonl")
    episodes = load_jsonl(source_root / "episodes.jsonl")
    if len(attempts) != len(ledger) or len(ledger) != summary.get("provider_requests"):
        raise ValueError("request ledger coverage mismatch")
    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    for attempt, call in zip(attempts, ledger, strict=True):
        key = (str(call["trial_id"]), int(call["turn_index"]))
        if key in indexed:
            raise ValueError("duplicate request ledger entry")
        if (
            key != (str(attempt["trial_id"]), int(attempt["turn_index"]))
            or attempt["request_sha256"] != stable_digest(attempt["request"])
            or call["request_sha256"] != stable_digest(call["request"])
            or call["request_sha256"] != attempt["request_sha256"]
            or call["response_sha256"] != stable_digest(call["response"])
            or call.get("valid_usage") is not True
        ):
            raise ValueError("durable request/response ledger integrity mismatch")
        indexed[key] = call
    repaired_episodes = []
    used_keys: set[tuple[str, int]] = set()
    changed_calls = 0
    changed_episode_ids = []
    for episode in episodes:
        repaired = json.loads(canonical_json(episode))
        repaired_calls = []
        episode_changed = False
        for embedded in episode.get("model_calls", ()):
            key = (str(embedded["trial_id"]), int(embedded["turn_index"]))
            if key in used_keys or key not in indexed or key[0] != episode["episode_id"]:
                raise ValueError("episode/ledger request ownership mismatch")
            durable = indexed[key]
            if stable_digest({k: v for k, v in embedded.items() if k != "request"}) != stable_digest(
                {k: v for k, v in durable.items() if k != "request"}
            ):
                raise ValueError("only embedded request-body aliasing may be reconciled")
            if stable_digest(embedded["request"]) != durable["request_sha256"]:
                changed_calls += 1
                episode_changed = True
            repaired_calls.append(json.loads(canonical_json(durable)))
            used_keys.add(key)
        if (
            not repaired_calls
            or episode.get("request_hash") != stable_digest(
                [call["request_sha256"] for call in repaired_calls]
            )
            or episode.get("response_hash") != stable_digest(
                [call["response_sha256"] for call in repaired_calls]
            )
            or episode.get("provider_input_tokens") != sum(
                call["prompt_tokens"] for call in repaired_calls
            )
            or episode.get("provider_output_tokens") != sum(
                call["completion_tokens"] for call in repaired_calls
            )
        ):
            raise ValueError("episode hashes or usage do not match durable calls")
        repaired["model_calls"] = repaired_calls
        repaired_episodes.append(repaired)
        if episode_changed:
            changed_episode_ids.append(str(episode["episode_id"]))
    if used_keys != set(indexed) or len(episodes) != summary.get("completed_episode_count"):
        raise ValueError("incomplete episode/ledger coverage")
    reconciliation = {
        "schema_version": "compression_audit_recording_reconciliation_v1",
        "source_run": str(source_root.resolve()),
        "source_manifest_sha256": file_sha256(source_root / "manifest.json"),
        "source_episodes_sha256": file_sha256(source_root / "episodes.jsonl"),
        "source_ledger_sha256": file_sha256(source_root / "provider_ledger.jsonl"),
        "source_attempts_sha256": file_sha256(source_root / "provider_attempts.jsonl"),
        "changed_embedded_request_count": changed_calls,
        "changed_episode_ids": changed_episode_ids,
        "answers_and_usage_changed": False,
        "provider_requests_made_during_reconciliation": 0,
        "recording_implementation": source_manifest.get("implementation"),
        "reconciliation_implementation": implementation_provenance(Path.cwd()),
    }
    output_root.mkdir(parents=True, exist_ok=False)
    for name in (
        "config.snapshot.json", "trials.jsonl", "preflight.json", "provider_attempts.jsonl",
        "provider_ledger.jsonl", "run_summary.json",
    ):
        shutil.copyfile(source_root / name, output_root / name)
    with (output_root / "episodes.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for episode in repaired_episodes:
            handle.write(canonical_json(episode) + "\n")
    _write_json(output_root / "reconciliation.json", reconciliation)
    artifacts = write_file_manifest(output_root)
    manifest = {
        **source_manifest,
        "mode": "recording_reconciliation_no_inference",
        "reconciliation": reconciliation,
        "artifacts": artifacts,
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def run_live_v0(
    config_path: Path,
    dataset_root: Path,
    output_root: Path,
    *,
    workspace: Path | None = None,
    max_new_requests: int | None = None,
    resume: bool = False,
    authorization_id: str | None = None,
) -> dict[str, Any]:
    """Serialize writers so concurrent resumes cannot duplicate paid attempts."""

    destination = output_root.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.parent / f".{destination.name}.live.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RuntimeError("a live writer lock exists; concurrent or interrupted runs require inspection") from error
    os.close(descriptor)
    try:
        return _run_live_v0_locked(
            config_path, dataset_root, destination, workspace=workspace,
            max_new_requests=max_new_requests, resume=resume,
            authorization_id=authorization_id,
        )
    finally:
        lock_path.unlink()
