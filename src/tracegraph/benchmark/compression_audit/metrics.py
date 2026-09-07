"""Public facade for the split ``tracegraph.compression_audit_metrics`` module."""

from __future__ import annotations

# ruff: noqa: F401

from .metric_constants import (
    GATE_SCHEMA_VERSION as GATE_SCHEMA_VERSION,
    PRIMARY_STRUCTURED_FIELDS as PRIMARY_STRUCTURED_FIELDS,
    REPORT_SCHEMA_VERSION as REPORT_SCHEMA_VERSION,
    SCORE_SCHEMA_VERSION as SCORE_SCHEMA_VERSION,
)

from .episode_scoring import (
    _canonical_mapping as _canonical_mapping,
    _contains_signature as _contains_signature,
    _evidence_for_field as _evidence_for_field,
    _normalize as _normalize,
    _provider_usage_complete as _provider_usage_complete,
    _recovered_source_ids as _recovered_source_ids,
    _request_integrity as _request_integrity,
    _slot_score as _slot_score,
    _token_f1 as _token_f1,
    _tokens as _tokens,
    score_episode as score_episode,
)

from .statistics import (
    _cluster_bootstrap as _cluster_bootstrap,
    _holm_adjust as _holm_adjust,
    _mean as _mean,
    _median as _median,
    _paired_statistics as _paired_statistics,
    _sign_test_pvalue as _sign_test_pvalue,
)

from .counterfactuals import (
    _audit_harm_pairs as _audit_harm_pairs,
    _counterfactual_rows as _counterfactual_rows,
)

from .summaries import (
    _method_summaries as _method_summaries,
    _pareto_front as _pareto_front,
)

from .gates import (
    _v0_gates as _v0_gates,
)

from .report import (
    _write_json as _write_json,
    _write_jsonl as _write_jsonl,
    score_run as score_run,
)
