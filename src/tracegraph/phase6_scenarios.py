"""Compatibility exports for :mod:`tracegraph.phase6_scenarios`.

New code should import from the subsystem package named below.
"""

from __future__ import annotations

# ruff: noqa: F401

from .phase6.scenarios import (
    DEFAULT_BASE_SEED as DEFAULT_BASE_SEED,
    FORK_TYPES as FORK_TYPES,
    SCENARIO_FAMILIES as SCENARIO_FAMILIES,
    SCENARIO_SCHEMA_VERSION as SCENARIO_SCHEMA_VERSION,
    ScenarioFork as ScenarioFork,
    ScenarioPrefix as ScenarioPrefix,
    VARIANTS as VARIANTS,
    _CREATED_AT as _CREATED_AT,
    _ScenarioAssembly as _ScenarioAssembly,
    _build_prefix as _build_prefix,
    _filler as _filler,
    _forks as _forks,
    _tool_schema as _tool_schema,
    generate_trace_lifecycle_suite as generate_trace_lifecycle_suite,
)
