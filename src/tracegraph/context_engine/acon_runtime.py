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

from .acon_types import (
    AconAdapterError as AconAdapterError,
    AconCallRecord as AconCallRecord,
    AconContextPlan as AconContextPlan,
    HistoryOptimizerProtocol as HistoryOptimizerProtocol,
    ObservationOptimizerProtocol as ObservationOptimizerProtocol,
    _history_text as _history_text,
    _optimizer_history as _optimizer_history,
)

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
