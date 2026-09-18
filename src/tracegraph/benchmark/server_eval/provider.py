"""Single-flight, durable self-hosted inference accounting. No automatic retry."""

from __future__ import annotations

import json
import math
import os
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from ..compression_audit.io import load_jsonl, stable_digest


def append(path: Path, row: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


class StopRun(RuntimeError):
    """An uncertain or resource-exhausted job cannot be replayed automatically."""


CONSTRUCTION_SUBMISSION_TOOL = "submit_memory_v1"
RETRIEVAL_SUBMISSION_TOOL = "submit_retrieval_v1"


def text_submission_tool(name: str, description: str, *, max_chars: int | None = None) -> dict:
    """Return a typed envelope for method stages that produce free-form text."""

    content_schema = {"type": "string", "minLength": 1}
    if max_chars is not None:
        content_schema["maxLength"] = max_chars
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {"content": content_schema},
            "required": ["content"],
        },
    }}


def construction_submission_tool(*, max_chars: int | None = None) -> dict:
    return text_submission_tool(
        CONSTRUCTION_SUBMISSION_TOOL,
        "Submit the complete final memory text requested by the prompt. "
        "Do not put analysis or commentary outside this tool call.",
        max_chars=max_chars,
    )


def retrieval_submission_tool(*, max_chars: int | None = None) -> dict:
    return text_submission_tool(
        RETRIEVAL_SUBMISSION_TOOL,
        "Submit exactly the retrieval decision, generated code, or retrieved text "
        "requested by the prompt. Do not put commentary outside this tool call.",
        max_chars=max_chars,
    )


def normalize_text_tool_response(
        response: dict, *, tool_name: str, stage: str,
        max_chars: int | None = None) -> dict:
    """Expose typed method text while retaining the unmodified provider ledger row."""

    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise ValueError(f"{stage} response must contain exactly one choice")
    if choices[0].get("finish_reason") == "length":
        raise ValueError("truncated method response")
    message = choices[0].get("message")
    calls = message.get("tool_calls") if isinstance(message, dict) else None
    if not isinstance(calls, list) or len(calls) != 1:
        raise ValueError(f"{stage} model must call exactly one submission tool")
    function = calls[0].get("function")
    if not isinstance(function, dict) or function.get("name") != tool_name:
        raise ValueError(f"{stage} model called an unexpected submission tool")
    arguments = function.get("arguments")
    if isinstance(arguments, str):
        arguments = json.loads(arguments)
    if not isinstance(arguments, dict) or set(arguments) != {"content"}:
        raise ValueError(f"{stage} submission arguments are malformed")
    content = arguments["content"]
    if not isinstance(content, str) or not content.strip():
        raise ValueError(f"{stage} submission is empty")
    if max_chars is not None and len(content) > max_chars:
        raise ValueError(f"{stage} submission exceeds configured character limit")
    normalized = json.loads(json.dumps(response))
    normalized["choices"][0]["message"]["content"] = content
    return normalized


def normalize_construction_tool_response(response: dict) -> dict:
    """Expose typed construction text to unchanged upstream method adapters."""

    return normalize_text_tool_response(
        response, tool_name=CONSTRUCTION_SUBMISSION_TOOL, stage="construction"
    )


def post(endpoint, api_key, body, *, timeout):
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise StopRun("redirected inference endpoint")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    request = urllib.request.Request(endpoint, json.dumps(body).encode(), headers)
    started = time.monotonic()
    with urllib.request.build_opener(NoRedirect).open(request, timeout=timeout) as response:
        data = response.read(16_000_001)
    if len(data) > 16_000_000:
        raise StopRun("response too large")
    return json.loads(data), time.monotonic() - started


class ServerLedger:
    def __init__(self, output: Path, config: dict, counters: dict, completed_jobs: set,
                 *, transport=None):
        self.output, self.suite, self.counters = output, config, counters
        self.models = {m["id"]: m for m in config["models"]}
        if config.get("embedding"):
            self.models[config["embedding"]["id"]] = config["embedding"]
        self.transport = transport or post
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.poisoned = False
        attempts_path = output / "provider_attempts.jsonl"
        rows_path = output / "provider_ledger.jsonl"
        attempts = load_jsonl(attempts_path) if attempts_path.exists() else []
        self.rows = load_jsonl(rows_path) if rows_path.exists() else []
        self.previous_seconds = sum(r["latency_seconds"] or 0 for r in self.rows)
        if len(attempts) != len(self.rows):
            raise StopRun("unresolved attempt; automatic replay forbidden")
        for attempt, row in zip(attempts, self.rows):
            if (attempt != row["attempt"] or stable_digest(attempt["request"]) != attempt["hash"]
                    or stable_digest(row["response"]) != row["response_hash"]
                    or not row["valid_usage"] or row["job_id"] not in completed_jobs):
                raise StopRun("uncertain, modified, or interrupted job; reconcile without replay")

    def for_model(self, model_id: str):
        return LedgerView(self, model_id)

    def call(self, model_id: str, template: dict, *, job_id: str, kind: str) -> dict:
        with self.lock:
            if self.poisoned:
                raise StopRun("earlier request was uncertain")
            model = self.models[model_id]
            embedding = kind in ("construction_embedding", "retrieval_embedding")
            endpoint = (model.get("base_url") or "").rstrip("/") + (
                "/embeddings" if embedding else "/chat/completions")
            parsed = urlparse(endpoint)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username
                    or parsed.query or parsed.fragment):
                raise StopRun("configure an explicit self-hosted endpoint")
            body = json.loads(json.dumps(template))
            body.update(model=model["served_model"])
            output_upper = 0
            if not embedding:
                output_upper = model.get(
                    "construction_max_output_tokens",
                    model["max_output_tokens"],
                ) if kind == "construction" else model.get(
                    "retrieval_max_output_tokens",
                    model["max_output_tokens"],
                ) if kind == "retrieval" else model["max_output_tokens"]
                body.update(temperature=0, seed=self.suite["seed"],
                        max_tokens=output_upper, stream=False)
            typed_tool = None
            if (not embedding and kind == "construction"
                    and model.get("construction_transport") == "native_tool_call"):
                max_chars = model.get("construction_submission_max_chars")
                typed_tool = (
                    CONSTRUCTION_SUBMISSION_TOOL,
                    construction_submission_tool(max_chars=max_chars),
                    max_chars,
                )
            elif (not embedding and kind == "retrieval"
                    and model.get("retrieval_transport") == "native_tool_call"):
                max_chars = model.get("retrieval_submission_max_chars")
                typed_tool = (
                    RETRIEVAL_SUBMISSION_TOOL,
                    retrieval_submission_tool(max_chars=max_chars),
                    max_chars,
                )
            if typed_tool:
                if "tools" in body or "tool_choice" in body or "response_format" in body:
                    raise StopRun(f"{kind} request already defines an output transport")
                tool_name, tool, _ = typed_tool
                body.update(
                    tools=[tool],
                    tool_choice={"type": "function", "function": {
                        "name": tool_name,
                    }},
                    parallel_tool_calls=False,
                )
            thinking = model.get("enable_thinking", False)
            if model.get("thinking_kinds") is not None:
                thinking = thinking and kind in model["thinking_kinds"]
            if not embedding and thinking and model.get("thinking_sampling"):
                body.update(model["thinking_sampling"])
            if not embedding and model["thinking_transport"] == "chat_template_kwargs":
                body["chat_template_kwargs"] = {"enable_thinking": thinking}
            elif not embedding and model["thinking_transport"] == "enable_thinking":
                body["enable_thinking"] = thinking
            upper = (self.counters[model_id].count(body["input"]) + 16 if embedding
                     else self.counters[model_id].request_count(body))
            limits = self.suite["limits"]
            if (time.monotonic() - self.started + self.previous_seconds > limits["wall_seconds"]
                    or len(self.rows) >= limits["request_limit"]
                    or sum(r["prompt_tokens"] or 0 for r in self.rows) + upper > limits["input_token_limit"]
                    or sum(r["completion_tokens"] or 0 for r in self.rows) + output_upper
                    > limits["output_token_limit"]):
                raise StopRun("suite resource limit reached")
            if upper + output_upper > model["context_window"]:
                raise StopRun("request exceeds configured model context window")
            stage_limit = {"construction": limits["build_calls_per_prefix"],
                           "retrieval": limits["retrieve_calls_per_query"],
                           "construction_embedding": limits.get("embedding_calls_per_prefix", 512),
                           "retrieval_embedding": 1}.get(kind)
            if stage_limit and sum(r["job_id"] == job_id and r["kind"] == kind
                                   for r in self.rows) >= stage_limit:
                raise StopRun("method stage request limit reached")
            attempt = {"index": len(self.rows), "job_id": job_id, "kind": kind,
                       "model_id": model_id, "endpoint": endpoint,
                       "request": body, "hash": stable_digest(body), "input_ceiling": upper}
            append(self.output / "provider_attempts.jsonl", attempt)
            response, latency, inputs, outputs, error = None, None, None, None, None
            try:
                api_key = os.environ.get(model.get("api_key_env") or "", "")
                response, latency = self.transport(endpoint, api_key, body,
                                                   timeout=limits["timeout_seconds"])
                usage = response["usage"]
                inputs, outputs = usage["prompt_tokens"], (0 if embedding else usage["completion_tokens"])
                if (type(inputs) is not int or type(outputs) is not int or inputs < 0 or outputs < 0
                        or inputs > upper or outputs > output_upper):
                    raise ValueError("usage differs from frozen limits/tokenizer")
                if response.get("model") not in model["returned_model_allowlist"]:
                    raise ValueError("server returned an unexpected model identity")
                if embedding:
                    vector = response["data"][0]["embedding"]
                    if (len(response["data"]) != 1 or len(vector) != model["dimension"]
                            or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector)
                            or not any(vector)):
                        raise ValueError("invalid embedding vector")
            except Exception as exc:
                error = type(exc).__name__
            row = {"attempt": attempt, "job_id": job_id, "kind": kind, "model_id": model_id,
                   "response": response, "response_hash": stable_digest(response),
                   "valid_usage": error is None, "prompt_tokens": inputs,
                   "completion_tokens": outputs, "latency_seconds": latency,
                   "returned_model": (response or {}).get("model"), "provider_error": error,
                   "cost_cny": 0.0, "cost_basis": "self_hosted_api_no_token_charge",
                   "hardware_cost_known": False, "automatic_retry": False}
            append(self.output / "provider_ledger.jsonl", row)
            self.rows.append(row)
            if error:
                self.poisoned = True
                raise StopRun("provider outcome or usage uncertain; stopped without retry")
            if typed_tool:
                return normalize_text_tool_response(
                    response, tool_name=typed_tool[0], stage=kind,
                    max_chars=typed_tool[2],
                )
            return response


class LedgerView:
    """The existing answer/repair/fixture loop sees the same small ledger interface."""

    def __init__(self, ledger: ServerLedger, model_id: str):
        self.ledger, self.model_id = ledger, model_id
        self.output = ledger.output
        model = ledger.models[model_id]
        self.config = {"model": model["served_model"],
                       "enable_thinking": model.get("enable_thinking", False),
                       "action_transport": model.get("action_transport", "json_schema")}

    @property
    def rows(self):
        return self.ledger.rows

    def call(self, template, *, job_id, kind):
        model = (self.ledger.suite["judge_model_id"]
                 if kind in ("judge", "judge_format_repair") else self.model_id)
        return self.ledger.call(model, template, job_id=job_id, kind=kind)

    def embed(self, text, *, job_id, kind):
        model = self.ledger.suite["embedding"]["id"]
        response = self.ledger.call(model, {"input": text, "encoding_format": "float"},
                                    job_id=job_id, kind=kind)
        return response["data"][0]["embedding"]


def text_response(response: dict) -> str:
    if response["choices"][0].get("finish_reason") == "length":
        raise ValueError("truncated method response")
    value = response["choices"][0]["message"].get("content")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("empty method response")
    return value
