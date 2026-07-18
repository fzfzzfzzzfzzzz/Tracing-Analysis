"""Deterministic semantic outcome and retry-identity helpers.

These rules deliberately use only trace-local structure.  They provide a
reproducible lower layer that can later be augmented by a calibrated semantic
classifier without making the core experiment depend on an extra LLM call.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .schema import SemanticOutcome, ToolStatus


_POLICY_DENIAL_PATTERNS = (
    "policy denied",
    "not authorized",
    "not allowed",
    "permission denied",
    "authorization required",
)
_TEST_FAILURE_PATTERNS = (
    "test failed",
    "tests failed",
    "assertion failed",
    "failing test",
)
_NEGATIVE_PATTERNS = (
    "confirmation required",
    "requires confirmation",
    "not found",
    "no such",
    "unavailable",
    "out of stock",
    "insufficient",
    "cannot ",
    "can't ",
    "unable to",
    "timed out",
    "timeout",
    "failed",
    "failure",
    "error:",
)
_POSITIVE_STATUS_VALUES = {
    "ok",
    "success",
    "succeeded",
    "complete",
    "completed",
    "confirmed",
    "cancelled",
    "canceled",
    "created",
    "updated",
    "refunded",
    "returned",
}
_NEGATIVE_STATUS_VALUES = {
    "error",
    "failed",
    "failure",
    "denied",
    "rejected",
    "not_found",
    "unavailable",
    "invalid",
    "timeout",
    "timed_out",
}


def _normalized_text(value: Any) -> str:
    if isinstance(value, str):
        return " ".join(value.lower().split())
    try:
        return " ".join(
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            .lower()
            .split()
        )
    except (TypeError, ValueError):
        return str(value).lower()


def infer_semantic_outcome(payload: Any, status: ToolStatus) -> SemanticOutcome:
    """Classify the meaning of a tool result separately from RPC execution."""

    if status in {ToolStatus.FAILED, ToolStatus.TIMEOUT}:
        return SemanticOutcome.NEGATIVE
    if status == ToolStatus.PARTIAL_SUCCESS:
        return SemanticOutcome.INCONCLUSIVE

    if isinstance(payload, dict):
        if payload.get("error") not in (None, False, "", 0, []):
            return SemanticOutcome.NEGATIVE
        if payload.get("success") is False or payload.get("ok") is False:
            return SemanticOutcome.NEGATIVE
        for key in ("status", "state", "outcome"):
            value = payload.get(key)
            if value is None:
                continue
            normalized = str(value).strip().lower().replace(" ", "_")
            if normalized in _NEGATIVE_STATUS_VALUES:
                return SemanticOutcome.NEGATIVE
            if normalized in _POSITIVE_STATUS_VALUES:
                return SemanticOutcome.POSITIVE

    text = _normalized_text(payload)
    if any(pattern in text for pattern in _POLICY_DENIAL_PATTERNS):
        return SemanticOutcome.POLICY_DENIED
    if any(pattern in text for pattern in _TEST_FAILURE_PATTERNS):
        return SemanticOutcome.TEST_FAILED
    if any(pattern in text for pattern in _NEGATIVE_PATTERNS):
        return SemanticOutcome.NEGATIVE
    if text.replace(" ", "_") in _POSITIVE_STATUS_VALUES:
        return SemanticOutcome.POSITIVE
    if isinstance(payload, dict) and (
        payload.get("success") is True or payload.get("ok") is True
    ):
        return SemanticOutcome.POSITIVE
    return SemanticOutcome.INCONCLUSIVE


_CONTROL_ARGUMENT_KEYS = {
    "confirm",
    "confirmation",
    "confirmed",
    "confirmation_status",
    "dry_run",
    "force",
    "page",
    "page_size",
    "limit",
    "offset",
}
_IDENTIFIER_PATTERN = re.compile(
    r"(^id$|_id$|^ids$|_ids$|number$|reference$|confirmation_code$|email$|username$)"
)


def _flatten_arguments(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        flattened: list[tuple[str, Any]] = []
        for key in sorted(value):
            path = f"{prefix}.{key}" if prefix else str(key)
            flattened.extend(_flatten_arguments(value[key], path))
        return flattened
    if isinstance(value, list):
        return [(prefix, value)]
    return [(prefix, value)]


_MISSING = object()


def _empty_argument_value(value: Any) -> bool:
    """Return whether a value carries no entity/operation information.

    Booleans and numeric zero are intentionally not empty: both are common,
    meaningful control values in tool APIs.
    """

    return value is None or value == "" or value == [] or value == {}


def is_argument_completion_retry(
    previous_arguments: dict[str, Any],
    current_arguments: dict[str, Any],
) -> bool:
    """Conservatively detect a retry that only fills missing arguments.

    This covers live traces such as ``zip=""`` followed by ``zip="32286"``
    without treating arbitrary calls to the same tool as one operation.  At
    least one non-empty anchor must remain identical, and every other change
    must fill a previously absent or empty field.
    """

    previous = dict(_flatten_arguments(previous_arguments))
    current = dict(_flatten_arguments(current_arguments))
    stable_anchor = False
    completed_field = False
    for path in sorted(set(previous) | set(current)):
        before = previous.get(path, _MISSING)
        after = current.get(path, _MISSING)
        if before is not _MISSING and after is not _MISSING and before == after:
            if not _empty_argument_value(before):
                stable_anchor = True
            continue
        if (
            (before is _MISSING or _empty_argument_value(before))
            and after is not _MISSING
            and not _empty_argument_value(after)
        ):
            completed_field = True
            continue
        return False
    return stable_anchor and completed_field


def operation_key(tool_name: str, arguments: dict[str, Any]) -> str:
    """Build a stable operation identity that tolerates retry-control changes.

    Entity identifiers take precedence.  If none exist, all non-control
    arguments are retained, which keeps matching conservative.
    """

    flattened = _flatten_arguments(arguments)
    top_level_identifiers = [
        (str(key), value)
        for key, value in sorted(arguments.items())
        if _IDENTIFIER_PATTERN.search(str(key).lower())
    ]
    nested_identifiers = [
        (path, value)
        for path, value in flattened
        if _IDENTIFIER_PATTERN.search(path.rsplit(".", 1)[-1].lower())
    ]
    selected = top_level_identifiers or nested_identifiers or [
        (path, value)
        for path, value in flattened
        if path.rsplit(".", 1)[-1].lower() not in _CONTROL_ARGUMENT_KEYS
    ]
    payload = {
        "tool_name": tool_name.lower(),
        "identity": selected,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
