"""Compatibility exports for :mod:`tracegraph.interventions`.

New code should import from the subsystem package named below.
"""

from __future__ import annotations

# ruff: noqa: F401

from .evaluation.interventions import (
    InterventionConfig as InterventionConfig,
    InterventionSpec as InterventionSpec,
    P1_CONDITIONS as P1_CONDITIONS,
    P1_INTERVENTION_KINDS as P1_INTERVENTION_KINDS,
    _aggregate as _aggregate,
    _attach_message_ordinals as _attach_message_ordinals,
    _diagnostic_payload as _diagnostic_payload,
    _failure_visible as _failure_visible,
    _initial_trace as _initial_trace,
    _input_accounting as _input_accounting,
    _mean as _mean,
    _paired_comparisons as _paired_comparisons,
    _record_result as _record_result,
    _result_message as _result_message,
    _run_one as _run_one,
    _sum_accounting as _sum_accounting,
    _tool_message as _tool_message,
    _write_csv as _write_csv,
    _write_json as _write_json,
    _write_jsonl as _write_jsonl,
    build_intervention_specs as build_intervention_specs,
    run_p1_interventions as run_p1_interventions,
)
