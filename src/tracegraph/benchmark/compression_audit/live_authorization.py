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
        "protocol": "v0.1-diagnostic",
        "development_only": True,
        "independent_validation": False,
        "interpretation": DEVELOPMENT_ONLY_NOTICE,
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


# Imported after definitions so mutually-referential helpers initialize safely.
from .live_provider import (
    _provider_body as _provider_body,
)
