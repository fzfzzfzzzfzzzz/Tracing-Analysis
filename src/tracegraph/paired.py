"""Compatibility exports for :mod:`tracegraph.paired`.

New code should import from the subsystem package named below.
"""

from __future__ import annotations

# ruff: noqa: F401

from .evaluation.paired import (
    _INFRASTRUCTURE_TERMINATIONS as _INFRASTRUCTURE_TERMINATIONS,
    _NORMAL_TERMINATIONS as _NORMAL_TERMINATIONS,
    _condition_metrics as _condition_metrics,
    _exact_mcnemar_p as _exact_mcnemar_p,
    _holm_adjust as _holm_adjust,
    _mean as _mean,
    _median as _median,
    _paired_bootstrap as _paired_bootstrap,
    _pass_hat_k as _pass_hat_k,
    _pass_hat_metrics as _pass_hat_metrics,
    _provider_usage_count as _provider_usage_count,
    _provider_usage_sum as _provider_usage_sum,
    _rate as _rate,
    _read_json as _read_json,
    _reward as _reward,
    _tool_call_count as _tool_call_count,
    _trace_record as _trace_record,
    analyze_live_matrix as analyze_live_matrix,
    write_live_matrix_report as write_live_matrix_report,
)
