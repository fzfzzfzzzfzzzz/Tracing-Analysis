"""TraceGraph public API."""

from importlib.metadata import PackageNotFoundError, version

from .context_engine import (
    ContextPlan,
    ContextPolicy,
    GraphConstrainedPolicy,
    MemorySnapshot,
    PolicyRegistry,
)
from .legacy import load_legacy_artifact

from .archive import ArchiveStore
from .capture import ToolExecutor
from .compiler import CompilerConfig, compile as compile_decision_state
from .context_engine.context import (
    ContextManager,
    ContextView,
    GraphLifecycleManager,
    RawHardFailureRetentionManager,
)
from .failure_cards import build_failure_cards
from .decision_query import DecisionQuery, build_decision_query
from .decision_state import (
    DecisionStateGraph,
    StateAtom,
    StateAtomType,
    StateEdge,
    StateEdgeType,
)
from .graph import TraceGraph
from .evaluation.interventions import (
    InterventionConfig,
    InterventionSpec,
    build_intervention_specs,
    run_p1_interventions,
)
from .lifecycle import LifecycleEngine
from .lifecycle_context import (
    ContextView as LifecycleContextView,
    ProjectionStrategy,
    project_context,
)
from .context_engine.liveness import (
    DecisionLifecycleGraph,
    EventLifecycleRecord,
    EventSpan,
    LivenessRoot,
    LivenessRoots,
    LiveSubgraph,
    analyze_liveness,
    build_state,
    derive_roots,
)
from .runtime import ContextManagedAgent, ModelTurn, ToolRequest, ToolSpec
from .prompt_bundle import PromptBundle
from .provider_cost import PromptCost, ProviderProtocol
from .representations import RepresentationCandidate, RepresentationType
from .state_reducer import reduce_event_graph
from .schema import (
    Edge,
    EdgeType,
    FailureCard,
    FailureClass,
    FailureExpiryTrigger,
    LifecycleProfile,
    LifecycleState,
    Node,
    NodeType,
    RelevanceState,
    RetentionObligation,
    SemanticOutcome,
    StorageState,
    ToolStatus,
    ValidityState,
)

__all__ = [
    "ArchiveStore",
    "ContextPlan",
    "ContextPolicy",
    "ContextManagedAgent",
    "ContextManager",
    "ContextView",
    "CompilerConfig",
    "DecisionQuery",
    "DecisionStateGraph",
    "Edge",
    "EdgeType",
    "FailureCard",
    "FailureClass",
    "FailureExpiryTrigger",
    "GraphLifecycleManager",
    "GraphConstrainedPolicy",
    "InterventionConfig",
    "InterventionSpec",
    "LifecycleProfile",
    "LifecycleState",
    "LifecycleEngine",
    "LifecycleContextView",
    "DecisionLifecycleGraph",
    "EventLifecycleRecord",
    "EventSpan",
    "LivenessRoot",
    "LivenessRoots",
    "LiveSubgraph",
    "ModelTurn",
    "MemorySnapshot",
    "Node",
    "NodeType",
    "RelevanceState",
    "RetentionObligation",
    "RawHardFailureRetentionManager",
    "PromptBundle",
    "PromptCost",
    "ProjectionStrategy",
    "PolicyRegistry",
    "ProviderProtocol",
    "RepresentationCandidate",
    "RepresentationType",
    "SemanticOutcome",
    "StorageState",
    "StateAtom",
    "StateAtomType",
    "StateEdge",
    "StateEdgeType",
    "ToolExecutor",
    "ToolRequest",
    "ToolSpec",
    "ToolStatus",
    "TraceGraph",
    "ValidityState",
    "build_failure_cards",
    "build_state",
    "build_decision_query",
    "build_intervention_specs",
    "run_p1_interventions",
    "compile_decision_state",
    "derive_roots",
    "analyze_liveness",
    "project_context",
    "reduce_event_graph",
    "load_legacy_artifact",
]

try:
    __version__ = version("tracegraph")
except PackageNotFoundError:  # Source trees that have not been installed yet.
    __version__ = "0+unknown"
