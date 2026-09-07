"""Constants moved from ``tracegraph.liveness``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from ..decision_query import DecisionQuery
from ..decision_state import DecisionStateGraph, StateAtom, StateAtomType, StateEdgeType, stable_digest
from ..graph import TraceGraph
from ..policy_rules import PolicyRule
from ..schema import EdgeType, Node, NodeType, SemanticOutcome
from ..state_reducer import reduce_event_graph

ArchiveReader = Callable[[str], Any]

_TOOL_NODE_TYPES = {
    NodeType.TOOL_CALL,
    NodeType.MCP_CALL,
    NodeType.OBSERVATION,
    NodeType.ERROR,
}

_CALL_NODE_TYPES = {NodeType.TOOL_CALL, NodeType.MCP_CALL}

_RESULT_EDGE_TYPES = {EdgeType.PRODUCES, EdgeType.FAILED_WITH}

_NEGATIVE_OUTCOMES = {
    SemanticOutcome.NEGATIVE.value,
    SemanticOutcome.POLICY_DENIED.value,
    SemanticOutcome.TEST_FAILED.value,
}

_ROOT_TYPES = {
    StateAtomType.ACTIVE_GOAL,
    StateAtomType.OPEN_SUBGOAL,
    StateAtomType.PENDING_OPERATION,
    StateAtomType.UNKNOWN_SLOT,
    StateAtomType.CONFIRMATION_REQUIREMENT,
    StateAtomType.APPLICABLE_POLICY_RULE,
    StateAtomType.GLOBAL_POLICY_RULE,
    StateAtomType.CRITICAL_EVIDENCE,
    StateAtomType.CONFLICTING_FACT,
    StateAtomType.NEGATIVE_GUARD,
    StateAtomType.SIDE_EFFECT_RECEIPT,
}

_KNOWN_TERMINAL_ATOM_STATUSES = {
    "superseded",
    "resolved",
    "invalidated",
    "consumed",
}

_REVERSE_DEPENDENCY_EDGES = {
    StateEdgeType.REQUIRED_FOR,
    StateEdgeType.FILLS,
    StateEdgeType.SUPPORTS,
    StateEdgeType.BLOCKS,
    StateEdgeType.SATISFIES,
    StateEdgeType.VIOLATES,
    StateEdgeType.DERIVED_FROM,
    StateEdgeType.RESOLVES,
    StateEdgeType.ALTERNATIVE_FOR,
}
