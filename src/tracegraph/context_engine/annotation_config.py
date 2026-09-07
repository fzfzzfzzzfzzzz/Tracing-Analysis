"""Definitions moved from ``tracegraph.lifecycle_annotation``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import math
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from ..capture import estimate_tokens
from ..graph import TraceGraph
from ..liveness import EventSpan
from ..phase5_offline import policy_text
from ..schema import EdgeType, Node, NodeType
from ..trajectory_artifacts import sha256_json

from .annotation_constants import (
    _FORBIDDEN_INPUT_KEYS as _FORBIDDEN_INPUT_KEYS,
)



def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_phase52_config(path: str | Path) -> dict[str, Any]:
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if config.get("schema_version") != "phase52_lifecycle_modeling_config_v1":
        raise ValueError("unsupported Phase 5.2 configuration")
    model = config.get("model", {})
    api_model = str(model.get("api_model", ""))
    if (
        model.get("temperature") != 0.0
        or model.get("thinking") != "disabled"
        or model.get("max_output_tokens") != 4096
        or not model.get("fallback_forbidden")
    ):
        raise ValueError("Phase 5.2 model protocol drift")
    if api_model == "glm-4.7-flash":
        if model.get("report_identity") != "zai/glm-4.7-flash" or not model.get(
            "paid_use_forbidden"
        ):
            raise ValueError("Phase 5.2 free model protocol drift")
    elif api_model == "glm-5.2":
        if (
            model.get("report_identity") != "zai/glm-5.2"
            or model.get("paid_use_forbidden") is not False
            or model.get("paid_use_authorized_by_user") is not True
            or model.get("condition_id") != "e1_glm52_pseudolabel_v1"
        ):
            raise ValueError("Phase 5.2 paid GLM-5.2 protocol drift")
    elif api_model == "qwen3.7-plus":
        condition_id = model.get("condition_id")
        label_protocol = config.get("annotation", {}).get(
            "label_protocol", "direct_disposition_v1"
        )
        if (
            model.get("provider") != "dashscope"
            or model.get("report_identity")
            != "aliyun-bailian/qwen3.7-plus"
            or model.get("enable_thinking") is not False
            or model.get("paid_use_forbidden") is not False
            or model.get("paid_use_authorized_by_user") is not True
        ):
            raise ValueError("Phase 5.2 paid Qwen3.7-Plus protocol drift")
        if condition_id == "e2_qwen37plus_pseudolabel_v1":
            if label_protocol != "direct_disposition_v1":
                raise ValueError("Phase 5.2 Qwen e2 label protocol drift")
        elif condition_id == "e3_qwen37plus_relation_first_v2":
            if (
                label_protocol != "relation_first_v1"
                or config.get("annotation", {}).get("repair_retry_mode")
                != "validation_feedback"
            ):
                raise ValueError("Phase 5.2 Qwen e3 label protocol drift")
        elif condition_id == "e3_qwen37plus_relation_first_v3":
            if (
                label_protocol != "relation_first_boolean_v2"
                or config.get("annotation", {}).get("repair_retry_mode")
                != "validation_feedback"
            ):
                raise ValueError("Phase 5.2 Qwen e3 boolean protocol drift")
        else:
            raise ValueError("Phase 5.2 paid Qwen condition drift")
    else:
        raise ValueError("Phase 5.2 model protocol drift")
    governance = config.get("governance", {})
    required_false = (
        "machine_labels_are_human_gold",
        "machine_labels_may_generate_hard_dead",
        "predictions_may_mutate_event_graph",
        "offline_projection_may_be_sent",
        "external_behavior_experiment_authorized",
        "scheme_b_authorized",
        "historical_93_is_lifecycle_gate",
    )
    if any(governance.get(key) is not False for key in required_false):
        raise ValueError("unsafe Phase 5.2 governance configuration")
    specs = config.get("tool_effect_specs", ())
    names = [str(item.get("tool_name")) for item in specs]
    if len(specs) != 15 or len(names) != len(set(names)):
        raise ValueError("Phase 5.2 requires exactly 15 unique ToolEffectSpecs")
    return config


def config_sha256(config: Mapping[str, Any]) -> str:
    return sha256_json(dict(config))


def assert_prefix_only_payload(
    value: Any,
    *,
    allowed_event_ids: set[str] | None = None,
    allowed_span_ids: set[str] | None = None,
) -> None:
    """Reject outcome leakage and dangling IDs before any network operation."""

    observed_events: set[str] = set()
    observed_spans: set[str] = set()

    def visit(item: Any, path: str) -> None:
        if isinstance(item, Mapping):
            for raw_key, child in item.items():
                key = str(raw_key).strip().lower()
                if key in _FORBIDDEN_INPUT_KEYS:
                    raise ValueError(f"forbidden annotation input key at {path}.{raw_key}")
                if key in {"event_id", "source_event_id", "target_event_id"}:
                    observed_events.add(str(child))
                if key == "span_id":
                    observed_spans.add(str(child))
                visit(child, f"{path}.{raw_key}")
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            for index, child in enumerate(item):
                visit(child, f"{path}[{index}]")

    visit(value, "request")
    if allowed_event_ids is not None and not observed_events.issubset(allowed_event_ids):
        unknown = sorted(observed_events.difference(allowed_event_ids))
        raise ValueError(f"unknown event id in annotation request: {unknown[:3]}")
    if allowed_span_ids is not None and observed_spans != allowed_span_ids:
        raise ValueError("annotation request span IDs do not match the frozen mapping")


def prepare_validation_feedback_request(
    request: Mapping[str, Any], *, validation_error: str
) -> dict[str, Any]:
    """Create a deterministic prefix-only correction request after validation failure."""

    error = validation_error.strip()
    if not error:
        raise ValueError("validation feedback cannot be empty")
    repaired = json.loads(canonical_json(request))
    messages = repaired.get("messages")
    if not isinstance(messages, list):
        raise ValueError("annotation request messages must be an array")
    messages.append(
        {
            "role": "user",
            "content": (
                "VALIDATION_REPAIR: The previous function arguments were rejected by "
                f"deterministic validation. Error: {error}. Return the complete JSON "
                "function arguments again for every supplied span. Correct only the "
                "schema violation, use exactly the allowed enum values, and do not add "
                "or omit labels."
            ),
        }
    )
    assert_prefix_only_payload(repaired)
    return repaired
