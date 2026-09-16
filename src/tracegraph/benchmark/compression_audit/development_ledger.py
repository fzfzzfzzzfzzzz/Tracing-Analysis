"""Durable, fail-closed provider accounting for the development pilot."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ...live_guard import require_live_authorization_id
from .development_experiment import load_counter
from .io import load_jsonl, stable_digest
from .live_authorization import (_append_jsonl, load_ignored_dashscope_credentials,
                                 request_input_token_upper_bound)
from .live_provider import _post_json, _usage


def validate_live(config: dict, preflight: dict, authorization_id: str | None,
                  workspace: Path) -> None:
    require_live_authorization_id(config["authorization"], authorization_id)
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    if (config["authorization"].get("authorized_by_user") is not True
            or config["authorization"].get("date") != today):
        raise ValueError("fresh same-day user authorization required")
    price = config["pricing_snapshot"]
    if (price.get("date") != today or price.get("region") != config["region"]
            or not str(price.get("source", "")).startswith("https://help.aliyun.com/")):
        raise ValueError("same-day official regional price snapshot required")
    if any(type(price.get(key)) not in (int, float) or price[key] <= 0
           for key in ("input_per_million", "output_per_million")):
        raise ValueError("invalid token prices")
    _, provenance = load_counter(config, workspace)
    if not provenance["exact"] or not preflight["tokenizer"]["exact"]:
        raise ValueError("verified model tokenizer required before spending")
    if "initial_request_input_bound_exceeded" in preflight.get("blockers", []):
        raise ValueError("initial request exceeds frozen input limit")
    if (preflight["cost_upper_bound_cny"] > config["cost_cap_cny"]
            or preflight["request_upper_bound"] > config["request_count_hard_max"]):
        raise ValueError("pilot exceeds frozen cost or request limit")


class ProviderLedger:
    def __init__(self, output: Path, config: dict, *, workspace: Path,
                 completed_jobs: set[str], transport: Any = None) -> None:
        self.output, self.config = output, config
        self.transport = transport
        self.rows = load_jsonl(output / "provider_ledger.jsonl") if (
            output / "provider_ledger.jsonl").exists() else []
        attempts = load_jsonl(output / "provider_attempts.jsonl") if (
            output / "provider_attempts.jsonl").exists() else []
        if (len(attempts) != len(self.rows) or any(
                a["request_sha256"] != r["request_sha256"] or not r["valid_usage"]
                or stable_digest(a["request"]) != a["request_sha256"]
                or stable_digest(r["request"]) != r["request_sha256"]
                or stable_digest(r["response"]) != r["response_sha256"]
                for a, r in zip(attempts, self.rows))):
            raise RuntimeError("uncertain or altered provider ledger; automatic retry forbidden")
        if any(r["job_id"] not in completed_jobs for r in self.rows):
            raise RuntimeError("interrupted paid job requires reconciliation; automatic replay forbidden")
        self.api_key, self.endpoint = ("test", "test") if transport else (
            load_ignored_dashscope_credentials(workspace))

    def price(self, inputs: int, outputs: int) -> float:
        price = self.config["pricing_snapshot"]
        return (inputs * price["input_per_million"] + outputs * price["output_per_million"]) / 1e6

    def call(self, template: dict, *, job_id: str, kind: str) -> dict:
        body = json.loads(json.dumps(template))
        body.update(model=self.config["judge_model"] if kind == "judge" else self.config["model"],
                    temperature=0, enable_thinking=False, max_tokens=2048,
                    seed=self.config["seed"])
        upper = request_input_token_upper_bound(body)
        if upper > self.config["request_input_tokens_hard_max"]:
            raise RuntimeError("next request exceeds proven input bound")
        if len(self.rows) >= self.config["request_count_hard_max"]:
            raise RuntimeError("request limit reached")
        spent = sum(row["cost_cny"] for row in self.rows)
        reserve = self.price(upper, self.config["max_output_tokens"])
        if spent + reserve > self.config["cost_cap_cny"]:
            raise RuntimeError("next request exceeds cost cap")
        attempt = {"job_id": job_id, "kind": kind, "request": body,
                   "request_sha256": stable_digest(body), "input_upper_bound": upper,
                   "attempt_index": len(self.rows)}
        _append_jsonl(self.output / "provider_attempts.jsonl", attempt)
        response, latency, inputs, outputs = None, None, None, None
        error = None
        try:
            response, latency = (self.transport or _post_json)(
                self.endpoint, self.api_key, body, timeout=self.config["timeout_seconds"])
            inputs, outputs = _usage(response)
            if inputs > upper or outputs > self.config["max_output_tokens"]:
                raise RuntimeError("provider usage exceeds frozen bound")
        except Exception as caught:
            # Preserve raw response even when usage is missing. Never expose credentials.
            error = type(caught).__name__
        row = {**attempt, "response": response, "response_sha256": stable_digest(response),
            "valid_usage": error is None, "provider_error": error,
            "prompt_tokens": inputs, "completion_tokens": outputs,
            "latency_seconds": latency, "provider_request_id": (response or {}).get("id"),
            "returned_model": (response or {}).get("model"),
            "cost_cny": self.price(inputs, outputs) if inputs is not None and outputs is not None
                        else reserve,
            "reserved_cost": error is not None, "provider_retry": False}
        _append_jsonl(self.output / "provider_ledger.jsonl", row)
        self.rows.append(row)
        if error:
            raise RuntimeError("provider outcome/usage uncertain; stop without retry")
        return response
