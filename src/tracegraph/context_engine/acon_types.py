"""Official ACON bridge subsystem."""

from __future__ import annotations

# ruff: noqa: F401

import hashlib
import importlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence
from tracegraph.capture import estimate_tokens



class AconAdapterError(RuntimeError):
    """Raised when the official adapter cannot preserve its declared contract."""


class ObservationOptimizerProtocol(Protocol):
    def check_summarization_needed(self, observation: str) -> bool: ...

    def process(
        self,
        task: str,
        observation: str,
        history: str,
        raw_history: list[dict[str, str]],
        opt_args: dict[str, Any],
        **kwargs: Any,
    ) -> str: ...


class HistoryOptimizerProtocol(Protocol):
    def check_summarization_needed(
        self,
        history_text: str,
        prev_history_summary: str | None = None,
    ) -> bool: ...

    def process(
        self,
        task: str,
        history: str,
        prev_history_summary: str | None = None,
        raw_history: list[dict[str, str]] | None = None,
        opt_args: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str: ...


@dataclass(frozen=True, slots=True)
class AconCallRecord:
    kind: str
    source_index: int | None
    input_tokens_estimated: int
    output_tokens_estimated: int
    latency_seconds: float
    provider_calls: tuple[dict[str, Any], ...] = ()
    fallback_used: bool = False
    error_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_index": self.source_index,
            "input_tokens_estimated": self.input_tokens_estimated,
            "output_tokens_estimated": self.output_tokens_estimated,
            "latency_seconds": self.latency_seconds,
            "provider_calls": list(self.provider_calls),
            "fallback_used": self.fallback_used,
            "error_type": self.error_type,
        }


@dataclass(frozen=True, slots=True)
class AconContextPlan:
    included_indices: tuple[int, ...]
    content_overrides: dict[int, str]
    task_index: int
    summarized_until: int
    history_summary: str | None
    call_records: tuple[AconCallRecord, ...]
    provenance: dict[str, Any]
    accounting_complete: bool
    runtime_main_result_eligible: bool

    def metadata(self) -> dict[str, Any]:
        provider_input = 0
        provider_output = 0
        cost = 0.0
        latency = 0.0
        exact_usage_calls = 0
        for call in self.call_records:
            latency += call.latency_seconds
            for provider_call in call.provider_calls:
                input_tokens = provider_call.get("input_tokens")
                output_tokens = provider_call.get("output_tokens")
                if isinstance(input_tokens, int) and isinstance(output_tokens, int):
                    provider_input += input_tokens
                    provider_output += output_tokens
                    exact_usage_calls += 1
                value = provider_call.get("cost_usd")
                if isinstance(value, (int, float)):
                    cost += float(value)
        return {
            "adapter": "official_acon_external",
            "provenance": self.provenance,
            "runtime_main_result_eligible": self.runtime_main_result_eligible,
            "task_index": self.task_index,
            "summarized_until": self.summarized_until,
            "history_summary_present": self.history_summary is not None,
            "included_message_indices": list(self.included_indices),
            "call_count": len(self.call_records),
            "fallback_count": sum(call.fallback_used for call in self.call_records),
            "accounting_complete": self.accounting_complete,
            "compressor_provider_input_tokens": provider_input,
            "compressor_provider_output_tokens": provider_output,
            "compressor_provider_exact_usage_calls": exact_usage_calls,
            "compressor_cost_usd": cost,
            "compressor_latency_seconds": latency,
        }


def _canonical_message(message: dict[str, Any]) -> dict[str, Any]:
    """Keep semantically relevant tau3 fields and remove volatile telemetry."""

    result: dict[str, Any] = {
        "role": str(message.get("role", "")),
        "content": message.get("content"),
    }
    for key in ("id", "requestor", "error"):
        if key in message and message[key] is not None:
            result[key] = message[key]
    calls = message.get("tool_calls") or []
    if calls:
        result["tool_calls"] = [
            {
                "id": call.get("id"),
                "name": call.get("name"),
                "arguments": call.get("arguments") or {},
            }
            for call in calls
        ]
    return result


def canonical_message_json(message: dict[str, Any]) -> str:
    """Return the stable, lossless-for-agent-semantics message representation."""

    return json.dumps(
        _canonical_message(message),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _optimizer_history(messages: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    """Normalize tau3 tool results into the two roles consumed by ACON V1."""

    result: list[dict[str, str]] = []
    for message in messages:
        role = str(message.get("role", ""))
        if role == "system":
            continue
        normalized_role = "assistant" if role == "assistant" else "user"
        result.append(
            {
                "role": normalized_role,
                "content": canonical_message_json(message),
            }
        )
    return result


def _history_text(messages: Sequence[dict[str, Any]]) -> str:
    lines: list[str] = []
    for message in _optimizer_history(messages):
        lines.append(f"{message['role'].upper()}:\n{message['content']}")
    return "\n\n".join(lines)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_acon_source(
    source_root: Path,
    manifest: dict[str, str],
    *,
    snapshot_sha: str,
    source_repo: str,
) -> dict[str, Any]:
    """Verify every pinned official file before importing third-party code."""

    root = source_root.resolve()
    if len(snapshot_sha) != 40 or any(char not in "0123456789abcdef" for char in snapshot_sha):
        raise AconAdapterError("ACON snapshot SHA must be a lowercase 40-character Git SHA")
    if not manifest:
        raise AconAdapterError("ACON source manifest cannot be empty")
    verified: dict[str, str] = {}
    for relative, expected in sorted(manifest.items()):
        candidate = (root / relative).resolve()
        if not candidate.is_relative_to(root):
            raise AconAdapterError(f"ACON manifest path escapes source root: {relative}")
        if not candidate.is_file():
            raise AconAdapterError(f"missing pinned ACON source file: {relative}")
        actual = _sha256_file(candidate)
        if actual != expected:
            raise AconAdapterError(f"ACON source hash mismatch: {relative}")
        verified[relative] = actual
    return {
        "source_repo": source_repo,
        "source_snapshot_sha": snapshot_sha,
        "license": "MIT",
        "source_manifest_verified": True,
        "source_file_hashes": verified,
    }
