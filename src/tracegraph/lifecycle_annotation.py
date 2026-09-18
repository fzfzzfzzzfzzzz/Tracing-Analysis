"""Compatibility exports for :mod:`tracegraph.lifecycle_annotation`.

New code should import from the subsystem package named below.
"""

from __future__ import annotations

# ruff: noqa: F401

from .context_engine.annotation import (
    AnnotationBudget as AnnotationBudget,
    CURRENT_TARGET_NEEDS as CURRENT_TARGET_NEEDS,
    DISPOSITIONS as DISPOSITIONS,
    OBLIGATIONS as OBLIGATIONS,
    PreparedAnnotationRequest as PreparedAnnotationRequest,
    TERMINAL_REASONS as TERMINAL_REASONS,
    TERMINAL_SAFE_REASONS as TERMINAL_SAFE_REASONS,
    _CALL_TYPES as _CALL_TYPES,
    _FORBIDDEN_INPUT_KEYS as _FORBIDDEN_INPUT_KEYS,
    _RELATION_BOOLEAN_SYSTEM_PROMPT as _RELATION_BOOLEAN_SYSTEM_PROMPT,
    _RELATION_SYSTEM_PROMPT as _RELATION_SYSTEM_PROMPT,
    _SYSTEM_PROMPT as _SYSTEM_PROMPT,
    _arguments as _arguments,
    _current_target as _current_target,
    _digest_rank as _digest_rank,
    _event_view as _event_view,
    _node_order as _node_order,
    _opaque_map as _opaque_map,
    _split_for_task as _split_for_task,
    _tool_name as _tool_name,
    annotation_response_function_schema as annotation_response_function_schema,
    assert_prefix_only_payload as assert_prefix_only_payload,
    canonical_json as canonical_json,
    cohen_kappa_binary as cohen_kappa_binary,
    complete_tool_spans as complete_tool_spans,
    config_sha256 as config_sha256,
    consensus_labels as consensus_labels,
    derive_relation_boolean_disposition as derive_relation_boolean_disposition,
    derive_relation_first_disposition as derive_relation_first_disposition,
    extract_function_arguments as extract_function_arguments,
    file_sha256 as file_sha256,
    load_phase52_config as load_phase52_config,
    prepare_annotation_request as prepare_annotation_request,
    prepare_validation_feedback_request as prepare_validation_feedback_request,
    remaining_attempt_numbers as remaining_attempt_numbers,
    remap_labels_to_original as remap_labels_to_original,
    response_function_schema as response_function_schema,
    response_relation_boolean_function_schema as response_relation_boolean_function_schema,
    response_relation_function_schema as response_relation_function_schema,
    validate_machine_labels as validate_machine_labels,
    validate_relation_boolean_labels as validate_relation_boolean_labels,
    validate_relation_first_labels as validate_relation_first_labels,
)
