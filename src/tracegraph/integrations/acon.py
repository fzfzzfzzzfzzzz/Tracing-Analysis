"""Compatibility exports for the ACON context-engine adapter."""

from __future__ import annotations

# ruff: noqa: F401

from ..context_engine.acon import (
    AconAdapterError as AconAdapterError,
    AconCallRecord as AconCallRecord,
    AconContextPlan as AconContextPlan,
    AconRuntimeAdapter as AconRuntimeAdapter,
    HistoryOptimizerProtocol as HistoryOptimizerProtocol,
    ObservationOptimizerProtocol as ObservationOptimizerProtocol,
    TauCompressorLLM as TauCompressorLLM,
    _canonical_message as _canonical_message,
    _history_text as _history_text,
    _load_json as _load_json,
    _optimizer_history as _optimizer_history,
    _sha256_file as _sha256_file,
    canonical_message_json as canonical_message_json,
    load_official_acon_adapter as load_official_acon_adapter,
    verify_acon_source as verify_acon_source,
)
