"""Public facade for the split ``tracegraph.stage1`` module."""

from __future__ import annotations

# ruff: noqa: F401

from .stage1_constants import (
    _INFRASTRUCTURE_TERMINATION as _INFRASTRUCTURE_TERMINATION,
    _NORMAL_TERMINATIONS as _NORMAL_TERMINATIONS,
)

from .stage1_records import (
    _failure_reasons as _failure_reasons,
    _gate as _gate,
    _group_metrics as _group_metrics,
    _median as _median,
    _provider_usage_sum as _provider_usage_sum,
    _rate as _rate,
    _read_json as _read_json,
    _reward as _reward,
    _reward_diagnostics as _reward_diagnostics,
    _tool_call_count as _tool_call_count,
    _trace_record as _trace_record,
)

from .stage1_report import (
    analyze_stage1_plan as analyze_stage1_plan,
    materialize_trace_archives as materialize_trace_archives,
    materialize_trace_graphs as materialize_trace_graphs,
    write_stage1_report as write_stage1_report,
)
