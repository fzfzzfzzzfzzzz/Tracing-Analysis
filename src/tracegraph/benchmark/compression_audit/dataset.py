"""Public facade for the split ``tracegraph.compression_audit`` module."""

from __future__ import annotations

# ruff: noqa: F401

from .constants import (
    BENCHMARK_ID as BENCHMARK_ID,
    CONFIG_SCHEMA_VERSION as CONFIG_SCHEMA_VERSION,
    CONTEXT_VARIANTS as CONTEXT_VARIANTS,
    DOMAINS as DOMAINS,
    FAILURE_ROLES as FAILURE_ROLES,
    FAILURE_TEMPLATES as FAILURE_TEMPLATES,
    IMPLEMENTATION_PATHS as IMPLEMENTATION_PATHS,
    MANIFEST_SCHEMA_VERSION as MANIFEST_SCHEMA_VERSION,
    QUERY_TYPES as QUERY_TYPES,
    RECOVERABILITY_LEVELS as RECOVERABILITY_LEVELS,
    SCHEMA_VERSION as SCHEMA_VERSION,
    SPLITS as SPLITS,
    TRACKS as TRACKS,
    VALIDATION_SCHEMA_VERSION as VALIDATION_SCHEMA_VERSION,
)

from .io import (
    _assert_output_available as _assert_output_available,
    _nonempty as _nonempty,
    _tuple_strings as _tuple_strings,
    _write_json as _write_json,
    _write_jsonl as _write_jsonl,
    canonical_json as canonical_json,
    file_sha256 as file_sha256,
    git_provenance as git_provenance,
    implementation_provenance as implementation_provenance,
    load_jsonl as load_jsonl,
    stable_digest as stable_digest,
)

from .models import (
    ContextBundle as ContextBundle,
    EpisodeRecord as EpisodeRecord,
    FailureChainGold as FailureChainGold,
    MemoryArtifact as MemoryArtifact,
    MemoryState as MemoryState,
    PrefixRecord as PrefixRecord,
    QueryRecord as QueryRecord,
)

from .controlled import (
    _controlled_prefix as _controlled_prefix,
    _event as _event,
    _split_for_family as _split_for_family,
    _tool_schema as _tool_schema,
    build_queries as build_queries,
    generate_controlled_dataset as generate_controlled_dataset,
)

from .legacy_data import (
    _legacy_action as _legacy_action,
    _legacy_event_role as _legacy_event_role,
    _legacy_recoverability as _legacy_recoverability,
    convert_legacy_diagnostic as convert_legacy_diagnostic,
)

from .build import (
    _annotation_template as _annotation_template,
    _copy_legacy as _copy_legacy,
    _copy_real_annotation_inputs as _copy_real_annotation_inputs,
    _expected_real_revisions as _expected_real_revisions,
    _write_real_annotation_scaffold as _write_real_annotation_scaffold,
    artifact_manifest as artifact_manifest,
    build_benchmark as build_benchmark,
    import_adjudicated_real as import_adjudicated_real,
    load_config as load_config,
    verify_file_manifest as verify_file_manifest,
    write_file_manifest as write_file_manifest,
)

from .validation_helpers import (
    _cohen_kappa as _cohen_kappa,
    _event_f1 as _event_f1,
    _query_leaks_gold as _query_leaks_gold,
    _tool_pair_errors as _tool_pair_errors,
)

from .real_validation import (
    validate_real_annotations as validate_real_annotations,
)

from .validation import (
    validate_benchmark as validate_benchmark,
)
