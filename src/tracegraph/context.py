"""Compatibility exports for :mod:`tracegraph.context`.

New code should import from the subsystem package named below.
"""

from __future__ import annotations

# ruff: noqa: F401

from .context_engine.context import (
    ACONStyleManager as ACONStyleManager,
    AgentDietStyleManager as AgentDietStyleManager,
    ContextItem as ContextItem,
    ContextManager as ContextManager,
    ContextView as ContextView,
    FullTrajectoryManager as FullTrajectoryManager,
    GraphLifecycleManager as GraphLifecycleManager,
    LLMOnlyPruningManager as LLMOnlyPruningManager,
    LastKManager as LastKManager,
    NoConstraintRetentionManager as NoConstraintRetentionManager,
    NoFailureRetentionManager as NoFailureRetentionManager,
    NoGraphEdgesManager as NoGraphEdgesManager,
    NoLifecycleManager as NoLifecycleManager,
    RawHardFailureRetentionManager as RawHardFailureRetentionManager,
    SummaryOnlyManager as SummaryOnlyManager,
    TokenPruningManager as TokenPruningManager,
    _fits as _fits,
    _truncate_summary as _truncate_summary,
    _truncate_to_token_limit as _truncate_to_token_limit,
    build_context_managers as build_context_managers,
)
