"""Runtime bridge from tau3 message histories to the official ACON optimizers.

The official implementation is loaded from an external, hash-verified source
snapshot.  It is deliberately not vendored in this repository.  This module
contains only the adapter, deterministic serialization, provenance checks, and
provider-usage accounting needed to run an auditable baseline.
"""

from __future__ import annotations

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


class TauCompressorLLM:
    """Official-optimizer LLM interface backed by the tau3 provider utility."""

    def __init__(
        self,
        *,
        model: str,
        call_name: str,
        llm_args: dict[str, Any] | None = None,
    ) -> None:
        self.model = model
        self.call_name = call_name
        self.llm_args = dict(llm_args or {})
        self.system_message = ""
        self._records: list[dict[str, Any]] = []

    def generate(self, prompt: str | list[dict[str, Any]], **kwargs: Any) -> str:
        from tau2.data_model.message import AssistantMessage, SystemMessage, UserMessage
        from tau2.utils.llm_utils import generate

        messages: list[Any] = []
        if self.system_message:
            messages.append(SystemMessage(role="system", content=self.system_message))
        if isinstance(prompt, str):
            messages.append(UserMessage(role="user", content=prompt))
        else:
            for item in prompt:
                role = str(item.get("role", "user"))
                content = str(item.get("content", ""))
                if role == "assistant":
                    messages.append(AssistantMessage(role="assistant", content=content))
                elif role == "system":
                    messages.append(SystemMessage(role="system", content=content))
                else:
                    messages.append(UserMessage(role="user", content=content))

        call_args = dict(self.llm_args)
        call_args.update(kwargs)
        response = generate(
            model=self.model,
            messages=messages,
            tools=None,
            call_name=self.call_name,
            **call_args,
        )
        if not isinstance(response.content, str) or not response.content.strip():
            raise AconAdapterError("compressor returned empty text")
        usage = response.usage or {}
        self._records.append(
            {
                "model": self.model,
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "cost_usd": response.cost,
                "latency_seconds": response.generation_time_seconds,
                "provider_usage_present": bool(response.usage),
            }
        )
        return response.content

    def drain_records(self) -> list[dict[str, Any]]:
        records = self._records
        self._records = []
        return records


@dataclass(slots=True)
class AconRuntimeAdapter:
    """Stateful per-session ACON observation/history hook."""

    observation_optimizer: ObservationOptimizerProtocol | None
    history_optimizer: HistoryOptimizerProtocol | None
    provenance: dict[str, Any]
    preserve_last_k_turns: int = 1
    fallback: str = "error"
    prev_history_summary: str | None = None
    summarized_until: int | None = None
    content_overrides: dict[int, str] = field(default_factory=dict)
    fallback_used: bool = False
    accounting_complete: bool = True

    def __post_init__(self) -> None:
        if self.observation_optimizer is None and self.history_optimizer is None:
            raise ValueError("at least one official ACON optimizer is required")
        if self.preserve_last_k_turns <= 0:
            raise ValueError("preserve_last_k_turns must be positive")
        if self.fallback not in {"error", "raw"}:
            raise ValueError("fallback must be 'error' or 'raw'")

    @staticmethod
    def _task_index(messages: Sequence[dict[str, Any]]) -> int:
        for index, message in enumerate(messages):
            if message.get("role") == "user" and str(message.get("content") or "").strip():
                return index
        raise AconAdapterError("ACON requires a non-empty user task message")

    def _effective_messages(
        self,
        messages: Sequence[dict[str, Any]],
        indices: Sequence[int],
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for index in indices:
            item = dict(messages[index])
            if index in self.content_overrides:
                item["content"] = self.content_overrides[index]
            result.append(item)
        return result

    @staticmethod
    def _drain_provider_records(optimizer: Any) -> tuple[dict[str, Any], ...]:
        llm = getattr(optimizer, "llm", None)
        drain = getattr(llm, "drain_records", None)
        if not callable(drain):
            return ()
        return tuple(dict(item) for item in drain())

    def _failure(
        self,
        *,
        kind: str,
        source_index: int | None,
        input_text: str,
        started: float,
        exc: Exception,
        optimizer: Any,
    ) -> AconCallRecord:
        provider_calls = self._drain_provider_records(optimizer)
        if self.fallback == "error":
            raise AconAdapterError(f"official ACON {kind} optimizer failed") from exc
        self.fallback_used = True
        return AconCallRecord(
            kind=kind,
            source_index=source_index,
            input_tokens_estimated=estimate_tokens(input_text),
            output_tokens_estimated=0,
            latency_seconds=time.perf_counter() - started,
            provider_calls=provider_calls,
            fallback_used=True,
            error_type=type(exc).__name__,
        )

    def _optimize_observation(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        task: str,
        task_index: int,
        source_index: int,
    ) -> AconCallRecord | None:
        optimizer = self.observation_optimizer
        if optimizer is None or source_index == task_index:
            return None
        message = messages[source_index]
        if message.get("role") not in {"tool", "user"}:
            return None
        observation = str(message.get("content") or "")
        if not observation.strip() or not optimizer.check_summarization_needed(observation):
            return None
        prior = self._effective_messages(messages, range(task_index, source_index))
        history = _history_text(prior)
        raw_history = _optimizer_history(prior)
        input_text = "\n".join((task, observation, history))
        started = time.perf_counter()
        try:
            optimized = optimizer.process(
                task=task,
                observation=observation,
                history=history,
                raw_history=raw_history,
                opt_args={},
            )
            if not isinstance(optimized, str) or not optimized.strip():
                raise AconAdapterError("observation optimizer returned empty text")
        except Exception as exc:
            return self._failure(
                kind="observation",
                source_index=source_index,
                input_text=input_text,
                started=started,
                exc=exc,
                optimizer=optimizer,
            )
        self.content_overrides[source_index] = optimized.strip()
        return AconCallRecord(
            kind="observation",
            source_index=source_index,
            input_tokens_estimated=estimate_tokens(input_text),
            output_tokens_estimated=estimate_tokens(optimized),
            latency_seconds=time.perf_counter() - started,
            provider_calls=self._drain_provider_records(optimizer),
        )

    def _preserved_start(
        self,
        messages: Sequence[dict[str, Any]],
        task_index: int,
    ) -> int:
        assistants = [
            index
            for index in range(task_index + 1, len(messages))
            if messages[index].get("role") == "assistant"
        ]
        if len(assistants) < self.preserve_last_k_turns:
            return task_index + 1
        return assistants[-self.preserve_last_k_turns]

    def _optimize_history(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        task: str,
        task_index: int,
    ) -> AconCallRecord | None:
        optimizer = self.history_optimizer
        if optimizer is None:
            return None
        if self.summarized_until is None:
            self.summarized_until = task_index
        preserved_start = self._preserved_start(messages, task_index)
        candidate_start = self.summarized_until + 1
        candidate_end = preserved_start
        if candidate_end <= candidate_start:
            return None
        indices = range(candidate_start, candidate_end)
        candidate = self._effective_messages(messages, indices)
        history = _history_text(candidate)
        if not history.strip():
            return None
        if not optimizer.check_summarization_needed(history, self.prev_history_summary):
            return None
        raw_history = _optimizer_history(candidate)
        input_text = "\n".join((task, self.prev_history_summary or "", history))
        started = time.perf_counter()
        try:
            summary = optimizer.process(
                task=task,
                history=history,
                prev_history_summary=self.prev_history_summary,
                raw_history=raw_history,
                opt_args={},
            )
            if not isinstance(summary, str) or not summary.strip():
                raise AconAdapterError("history optimizer returned empty text")
        except Exception as exc:
            return self._failure(
                kind="history",
                source_index=None,
                input_text=input_text,
                started=started,
                exc=exc,
                optimizer=optimizer,
            )
        self.prev_history_summary = summary.strip()
        self.summarized_until = candidate_end - 1
        return AconCallRecord(
            kind="history",
            source_index=None,
            input_tokens_estimated=estimate_tokens(input_text),
            output_tokens_estimated=estimate_tokens(summary),
            latency_seconds=time.perf_counter() - started,
            provider_calls=self._drain_provider_records(optimizer),
        )

    def prepare(
        self,
        messages: Sequence[dict[str, Any]],
        *,
        new_indices: Sequence[int],
    ) -> AconContextPlan:
        """Run official hooks and return a plan over the original tau3 messages."""

        if not messages:
            raise AconAdapterError("cannot prepare an empty tau3 history")
        if any(index < 0 or index >= len(messages) for index in new_indices):
            raise IndexError("new message index is outside the tau3 history")
        task_index = self._task_index(messages)
        if self.summarized_until is None:
            self.summarized_until = task_index
        task = str(messages[task_index].get("content") or "")
        records: list[AconCallRecord] = []
        for index in new_indices:
            record = self._optimize_observation(
                messages,
                task=task,
                task_index=task_index,
                source_index=index,
            )
            if record is not None:
                records.append(record)
        history_record = self._optimize_history(
            messages,
            task=task,
            task_index=task_index,
        )
        if history_record is not None:
            records.append(history_record)

        for record in records:
            if record.fallback_used:
                continue
            if not record.provider_calls or any(
                not provider_call.get("provider_usage_present")
                or not isinstance(provider_call.get("input_tokens"), int)
                or not isinstance(provider_call.get("output_tokens"), int)
                or not isinstance(provider_call.get("cost_usd"), (int, float))
                or not isinstance(provider_call.get("latency_seconds"), (int, float))
                for provider_call in record.provider_calls
            ):
                self.accounting_complete = False

        overrides = dict(self.content_overrides)
        if self.prev_history_summary:
            overrides[task_index] = (
                task
                + "\n\n<HISTORY_SUMMARY>\n"
                + self.prev_history_summary
                + "\n</HISTORY_SUMMARY>"
            )
        prefix = list(range(0, task_index + 1))
        tail_start = max(task_index + 1, self.summarized_until + 1)
        included = tuple(prefix + list(range(tail_start, len(messages))))
        return AconContextPlan(
            included_indices=included,
            content_overrides=overrides,
            task_index=task_index,
            summarized_until=self.summarized_until,
            history_summary=self.prev_history_summary,
            call_records=tuple(records),
            provenance=dict(self.provenance),
            accounting_complete=self.accounting_complete,
            runtime_main_result_eligible=(
                bool(self.provenance.get("source_manifest_verified"))
                and not self.fallback_used
                and self.accounting_complete
            ),
        )


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AconAdapterError("ACON adapter config must be a JSON object")
    return value


def load_official_acon_adapter(
    *,
    config_path: Path,
    source_root: Path,
    compressor_model_override: str | None = None,
) -> AconRuntimeAdapter:
    """Load hash-pinned official classes and construct a per-session adapter."""

    config = _load_json(config_path)
    source_manifest = config.get("source_manifest")
    if not isinstance(source_manifest, dict):
        raise AconAdapterError("source_manifest is required")
    provenance = verify_acon_source(
        source_root,
        {str(key): str(value) for key, value in source_manifest.items()},
        snapshot_sha=str(config.get("source_snapshot_sha", "")),
        source_repo=str(config.get("source_repo", "")),
    )
    config_bytes = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    provenance["adapter_config_sha256"] = hashlib.sha256(config_bytes).hexdigest()

    package_root = (source_root.resolve() / "src").resolve()
    package_text = str(package_root)
    if package_text not in sys.path:
        sys.path.insert(0, package_text)
    importlib.invalidate_caches()
    for module_name in ("productive_agents", "productive_agents.ctxopt"):
        existing = sys.modules.get(module_name)
        if existing is not None:
            existing_path = Path(getattr(existing, "__file__", "") or "").resolve()
            if not existing_path.is_relative_to(source_root.resolve()):
                raise AconAdapterError(
                    "productive_agents was already loaded from an unverified path"
                )
    obs_module = importlib.import_module("productive_agents.ctxopt.obs_optimizer")
    history_module = importlib.import_module("productive_agents.ctxopt.history_optimizer")
    for module in (obs_module, history_module):
        module_path = Path(module.__file__ or "").resolve()
        if not module_path.is_relative_to(source_root.resolve()):
            raise AconAdapterError("productive_agents was imported from an unverified path")

    compressor_model = compressor_model_override or str(config.get("compressor_model", ""))
    if not compressor_model:
        raise AconAdapterError("compressor_model is required")
    llm_args = config.get("compressor_llm_args") or {}
    if not isinstance(llm_args, dict):
        raise AconAdapterError("compressor_llm_args must be an object")
    provenance["compressor_model"] = compressor_model
    provenance["official_classes"] = {
        "observation": "productive_agents.ctxopt.obs_optimizer.ObservationOptimizer",
        "history": "productive_agents.ctxopt.history_optimizer.HistoryOptimizer",
    }

    prompt_relative = str(config.get("prompt_dir", ""))
    prompt_dir = (source_root.resolve() / prompt_relative).resolve()
    if not prompt_dir.is_relative_to(source_root.resolve()) or not prompt_dir.is_dir():
        raise AconAdapterError("verified ACON prompt_dir is missing or outside source root")

    observation_config = dict(config.get("observation") or {})
    history_config = dict(config.get("history") or {})
    observation_config.update({"model": compressor_model, "obs_prompt_dir": str(prompt_dir)})
    history_config.update({"model": compressor_model, "history_prompt_dir": str(prompt_dir)})

    observation_llm = TauCompressorLLM(
        model=compressor_model,
        call_name="acon_observation_compressor",
        llm_args=llm_args,
    )
    history_llm = TauCompressorLLM(
        model=compressor_model,
        call_name="acon_history_compressor",
        llm_args=llm_args,
    )
    observation_optimizer = obs_module.ObservationOptimizer(
        observation_config,
        debug_mode=False,
        llm=observation_llm,
    )
    history_optimizer = history_module.HistoryOptimizer(
        history_config,
        debug_mode=False,
        llm=history_llm,
    )
    observation_llm.system_message = observation_optimizer.system_message
    history_llm.system_message = history_optimizer.system_message
    provenance["threshold_tokenizers"] = {
        "observation": getattr(getattr(observation_optimizer, "tokenizer", None), "name", None)
        or "approximate_len_div_4",
        "history": getattr(getattr(history_optimizer, "tokenizer", None), "name", None)
        or "approximate_len_div_4",
    }

    return AconRuntimeAdapter(
        observation_optimizer=observation_optimizer,
        history_optimizer=history_optimizer,
        provenance=provenance,
        preserve_last_k_turns=int(config.get("preserve_last_k_turns", 1)),
        fallback=str(config.get("fallback", "error")),
    )
