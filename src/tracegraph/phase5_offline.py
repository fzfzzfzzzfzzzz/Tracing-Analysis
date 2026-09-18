"""Compatibility exports for :mod:`tracegraph.phase5_offline`.

New code should import from the subsystem package named below.
"""

from __future__ import annotations

# ruff: noqa: F401

from .context_engine.phase5 import (
    DEVELOPMENT_MANIFEST_VERSION as DEVELOPMENT_MANIFEST_VERSION,
    F5_G1_THRESHOLDS as F5_G1_THRESHOLDS,
    TOOL_SCHEMA_ARTIFACT_VERSION as TOOL_SCHEMA_ARTIFACT_VERSION,
    _archive_tree as _archive_tree,
    _complete_tool_spans as _complete_tool_spans,
    _explicit_terminal_relation_count as _explicit_terminal_relation_count,
    _ordinal as _ordinal,
    _source_map as _source_map,
    _text as _text,
    adjudicate_f5_g1 as adjudicate_f5_g1,
    assert_no_outcome_fields as assert_no_outcome_fields,
    build_development_manifest as build_development_manifest,
    build_strict_prefix as build_strict_prefix,
    file_sha256 as file_sha256,
    policy_text as policy_text,
    prefix_messages as prefix_messages,
    strict_predecision_nodes as strict_predecision_nodes,
    structural_features as structural_features,
    tool_schema_artifact as tool_schema_artifact,
)
