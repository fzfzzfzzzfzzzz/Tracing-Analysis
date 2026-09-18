"""Definitions moved from ``tracegraph.phase6_live``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence
from ..capture import estimate_tokens
from ..decision_state import stable_digest

from .live_constants import (
    CONFIRMATION_VARIANT_SCHEDULE as CONFIRMATION_VARIANT_SCHEDULE,
    FORK_TYPES as FORK_TYPES,
    LIVE_METHOD_IDS as LIVE_METHOD_IDS,
    LIVE_SCHEMA_VERSION as LIVE_SCHEMA_VERSION,
    PILOT_VARIANT_SCHEDULE as PILOT_VARIANT_SCHEDULE,
    PROMPT_PROTOCOL_V1 as PROMPT_PROTOCOL_V1,
    PROMPT_PROTOCOL_V2 as PROMPT_PROTOCOL_V2,
    SCORING_PROTOCOL_V1 as SCORING_PROTOCOL_V1,
    SCORING_PROTOCOL_V2 as SCORING_PROTOCOL_V2,
)



def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def choose_pilot_prefix_ids(prefix_ids: Sequence[str]) -> tuple[str, ...]:
    available = set(prefix_ids)
    selected = tuple(
        f"{family}:{variant}"
        for family, variants in PILOT_VARIANT_SCHEDULE.items()
        for variant in variants
    )
    missing = sorted(set(selected).difference(available))
    if missing:
        raise ValueError(f"pilot prefixes are missing: {missing}")
    return selected


def choose_confirmation_prefix_ids(prefix_ids: Sequence[str]) -> tuple[str, ...]:
    """Choose the 12 prefixes not sent during the first live-model run."""

    available = set(prefix_ids)
    selected = tuple(
        f"{family}:{variant}"
        for family, variants in CONFIRMATION_VARIANT_SCHEDULE.items()
        for variant in variants
    )
    missing = sorted(set(selected).difference(available))
    if missing:
        raise ValueError(f"confirmation prefixes are missing: {missing}")
    if set(selected).intersection(choose_pilot_prefix_ids(prefix_ids)):
        raise ValueError("pilot and confirmation prefix samples overlap")
    return selected


def validate_live_config(config: Mapping[str, Any]) -> None:
    if config.get("schema_version") != LIVE_SCHEMA_VERSION:
        raise ValueError("unsupported Phase 6 live pilot config")
    if tuple(config.get("methods", ())) != LIVE_METHOD_IDS:
        raise ValueError("Phase 6 live methods differ from the frozen six-method matrix")
    if tuple(config.get("fork_types", ())) != FORK_TYPES:
        raise ValueError("Phase 6 live fork types differ from the frozen matrix")
    if len(config.get("sample_prefix_ids", ())) != 12:
        raise ValueError("Phase 6 live pilot requires exactly 12 prefixes")
    if config.get("prompt_protocol", PROMPT_PROTOCOL_V1) not in {
        PROMPT_PROTOCOL_V1,
        PROMPT_PROTOCOL_V2,
    }:
        raise ValueError("unsupported Phase 6 answer prompt protocol")
    if config.get("scoring_protocol", SCORING_PROTOCOL_V1) not in {
        SCORING_PROTOCOL_V1,
        SCORING_PROTOCOL_V2,
    }:
        raise ValueError("unsupported Phase 6 scoring protocol")
    model = config.get("model", {})
    if model.get("primary") != "qwen3.8-27b" or model.get("fallback") != "qwen-plus":
        raise ValueError("model selection differs from the user's authorization")
    if model.get("temperature") != 0 or bool(model.get("enable_thinking")):
        raise ValueError("live pilot requires temperature=0 and thinking disabled")
    limits = config.get("limits", {})
    if float(limits.get("maximum_cost_cny", -1)) != 100.0:
        raise ValueError("the authorized maximum cost must be exactly 100 CNY")
    if int(limits.get("trial_count", -1)) != 216:
        raise ValueError("the frozen Phase 6 live pilot requires 216 trials")
    if int(limits.get("retry_per_request_max", -1)) != 0:
        raise ValueError("provider retries are disabled for this pilot")
    authorization = config.get("authorization", {})
    if not authorization.get("authorized_by_user"):
        raise ValueError("paid provider use has not been authorized")
    if float(authorization.get("maximum_cost_cny", -1)) != 100.0:
        raise ValueError("authorization and runtime cost limits differ")


def validate_input_hashes(root: Path, expected: Mapping[str, Any]) -> None:
    for name, digest in expected.items():
        path = root / str(name)
        if not path.is_file() or file_sha256(path) != str(digest).lower():
            raise ValueError(f"Phase 6 input file hash mismatch: {name}")


def theoretical_maximum_cost_cny(config: Mapping[str, Any]) -> float:
    limits = config["limits"]
    model = config["model"]
    prices = config["pricing_snapshot"]["prices_per_million_tokens"]
    maximum_price = max(
        (
            float(prices[name]["input"]),
            float(prices[name]["output"]),
        )
        for name in (model["primary"], model["fallback"])
    )
    requests = int(limits["request_count_hard_max"])
    per_request = (
        int(limits["request_input_tokens_hard_max"]) * maximum_price[0]
        + int(model["max_output_tokens"]) * maximum_price[1]
    ) / 1_000_000
    return requests * per_request
