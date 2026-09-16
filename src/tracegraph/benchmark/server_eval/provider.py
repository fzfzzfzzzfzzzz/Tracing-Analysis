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
            if not embedding:
                body.update(temperature=0, seed=self.suite["seed"],
                        max_tokens=model["max_output_tokens"], stream=False)
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
            output_upper = 0 if embedding else 2048
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
        model = self.ledger.suite["judge_model_id"] if kind == "judge" else self.model_id
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
