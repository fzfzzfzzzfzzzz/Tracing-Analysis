"""Compatibility exports for :mod:`tracegraph.compression_audit_metrics`.

New code should import from the subsystem package named below.
"""

from __future__ import annotations

# ruff: noqa: F401

from .benchmark.compression_audit.metrics import (
    GATE_SCHEMA_VERSION as GATE_SCHEMA_VERSION,
    PRIMARY_STRUCTURED_FIELDS as PRIMARY_STRUCTURED_FIELDS,
    REPORT_SCHEMA_VERSION as REPORT_SCHEMA_VERSION,
    SCORE_SCHEMA_VERSION as SCORE_SCHEMA_VERSION,
    _audit_harm_pairs as _audit_harm_pairs,
    _canonical_mapping as _canonical_mapping,
    _cluster_bootstrap as _cluster_bootstrap,
    _contains_signature as _contains_signature,
    _counterfactual_rows as _counterfactual_rows,
    _evidence_for_field as _evidence_for_field,
    _holm_adjust as _holm_adjust,
    _mean as _mean,
    _median as _median,
    _method_summaries as _method_summaries,
    _normalize as _normalize,
    _paired_statistics as _paired_statistics,
    _pareto_front as _pareto_front,
    _provider_usage_complete as _provider_usage_complete,
    _recovered_source_ids as _recovered_source_ids,
    _request_integrity as _request_integrity,
    _sign_test_pvalue as _sign_test_pvalue,
    _slot_score as _slot_score,
    _token_f1 as _token_f1,
    _tokens as _tokens,
    _v0_gates as _v0_gates,
    _write_json as _write_json,
    _write_jsonl as _write_jsonl,
    score_episode as score_episode,
    score_run as score_run,
)
