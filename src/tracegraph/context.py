"""Interchangeable context managers for baselines, ablations, and Full Ours."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from .capture import estimate_tokens
from .graph import TraceGraph
from .lifecycle import LifecycleEngine
from .schema import (
    EdgeType,
    LifecycleState,
    Node,
    NodeType,
    RelevanceState,
    RetentionObligation,
    StorageState,
    ValidityState,
)


@dataclass(slots=True)
class ContextItem:
    node_id: str
    node_type: NodeType
    content: Any
    token_count: int
    reason: str
    source_node_ids: tuple[str, ...] = ()
    raw_ref: str | None = None
    preserves_sources: bool = True

    @classmethod
    def from_node(cls, node: Node, reason: str) -> "ContextItem":
        sources = tuple(node.metadata.get("source_node_ids", ())) or (node.node_id,)
        return cls(
            node_id=node.node_id,
            node_type=node.node_type,
            content=node.content,
            token_count=node.token_count or estimate_tokens(node.content),
            reason=reason,
            source_node_ids=sources,
            raw_ref=node.raw_ref,
            preserves_sources=(
                node.node_type != NodeType.SUMMARY
                or bool(node.metadata.get("coverage_verified", False))
            ),
        )


@dataclass(slots=True)
class ContextView:
    manager: str
    items: list[ContextItem]
    original_tokens: int
    budget: int | None
    excluded_node_ids: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def selected_tokens(self) -> int:
        return sum(item.token_count for item in self.items)

    @property
    def compression_ratio(self) -> float:
        if self.original_tokens <= 0:
            return 0.0
        return max(0.0, 1.0 - self.selected_tokens / self.original_tokens)

    @property
    def covered_node_ids(self) -> set[str]:
        covered: set[str] = set()
        for item in self.items:
            if item.preserves_sources:
                covered.update(item.source_node_ids)
        return covered

    def to_dict(self) -> dict[str, Any]:
        return {
            "manager": self.manager,
            "original_tokens": self.original_tokens,
            "selected_tokens": self.selected_tokens,
            "compression_ratio": self.compression_ratio,
            "budget": self.budget,
            "excluded_node_ids": self.excluded_node_ids,
            "metadata": self.metadata,
            "items": [
                {
                    "node_id": item.node_id,
                    "node_type": item.node_type.value,
                    "content": item.content,
                    "token_count": item.token_count,
                    "reason": item.reason,
                    "source_node_ids": list(item.source_node_ids),
                    "raw_ref": item.raw_ref,
                    "preserves_sources": item.preserves_sources,
                }
                for item in self.items
            ],
        }


class ContextManager(ABC):
    name = "abstract"

    @abstractmethod
    def select(self, graph: TraceGraph, *, budget: int | None = None) -> ContextView:
        raise NotImplementedError

    @staticmethod
    def _nodes(graph: TraceGraph) -> list[Node]:
        return graph.find_nodes()

    def _view(
        self,
        graph: TraceGraph,
        items: Iterable[ContextItem],
        *,
        budget: int | None,
        metadata: dict[str, Any] | None = None,
    ) -> ContextView:
        item_list = sorted(
            list(items),
            key=lambda item: (
                graph.nodes[item.node_id].step_id if item.node_id in graph.nodes else 10**12,
                item.node_id,
            ),
        )
        selected = {item.node_id for item in item_list}
        all_nodes = self._nodes(graph)
        return ContextView(
            manager=self.name,
            items=item_list,
            original_tokens=sum(
                node.token_count or estimate_tokens(node.content) for node in all_nodes
            ),
            budget=budget,
            excluded_node_ids=[node.node_id for node in all_nodes if node.node_id not in selected],
            metadata=metadata or {},
        )


def _fits(current: int, item: ContextItem, budget: int | None) -> bool:
    return budget is None or current + item.token_count <= budget


def _truncate_summary(content: Any, max_tokens: int = 32) -> str:
    text = (
        content
        if isinstance(content, str)
        else json.dumps(content, ensure_ascii=False, default=str)
    )
    max_chars = max(16, max_tokens * 4)
    return text if len(text) <= max_chars else text[: max_chars - 1] + "…"


class FullTrajectoryManager(ContextManager):
    name = "full_trajectory"

    def select(self, graph: TraceGraph, *, budget: int | None = None) -> ContextView:
        items = [ContextItem.from_node(node, "full_history") for node in self._nodes(graph)]
        return self._view(
            graph,
            items,
            budget=budget,
            metadata={"budget_ignored": budget is not None},
        )


class LastKManager(ContextManager):
    name = "last_k"

    def __init__(self, k: int = 8) -> None:
        if k <= 0:
            raise ValueError("k must be positive")
        self.k = k

    def select(self, graph: TraceGraph, *, budget: int | None = None) -> ContextView:
        nodes = self._nodes(graph)
        selected = nodes[-self.k :]
        items = [ContextItem.from_node(node, f"last_{self.k}") for node in selected]
        return self._view(graph, items, budget=budget, metadata={"k": self.k})


class TokenPruningManager(ContextManager):
    name = "token_length_pruning"

    def __init__(self, default_budget: int = 2048) -> None:
        self.default_budget = default_budget

    def select(self, graph: TraceGraph, *, budget: int | None = None) -> ContextView:
        limit = self.default_budget if budget is None else budget
        items: list[ContextItem] = []
        total = 0
        for node in reversed(self._nodes(graph)):
            item = ContextItem.from_node(node, "newest_until_budget")
            if _fits(total, item, limit):
                items.append(item)
                total += item.token_count
        return self._view(graph, items, budget=limit)


class SummaryOnlyManager(ContextManager):
    name = "summary_only"

    def __init__(self, summary_tokens_per_node: int = 24) -> None:
        self.summary_tokens_per_node = summary_tokens_per_node

    def select(self, graph: TraceGraph, *, budget: int | None = None) -> ContextView:
        items: list[ContextItem] = []
        total = 0
        for node in self._nodes(graph):
            summary = _truncate_summary(node.content, self.summary_tokens_per_node)
            item = ContextItem(
                node_id=node.node_id,
                node_type=NodeType.SUMMARY,
                content=summary,
                token_count=estimate_tokens(summary),
                reason="deterministic_summary_proxy",
                source_node_ids=(node.node_id,),
                raw_ref=node.raw_ref,
                preserves_sources=False,
            )
            if _fits(total, item, budget):
                items.append(item)
                total += item.token_count
        return self._view(
            graph,
            items,
            budget=budget,
            metadata={"proxy": True, "requires_live_summarizer_for_main_results": True},
        )


class LLMOnlyPruningManager(ContextManager):
    name = "llm_only_pruning"

    def __init__(self, scorer: Callable[[Node, TraceGraph], float] | None = None) -> None:
        self.scorer = scorer

    def _score(self, node: Node, graph: TraceGraph) -> float:
        if self.scorer is not None:
            return float(self.scorer(node, graph))
        score = node.step_id / max(
            1, max((item.step_id for item in graph.nodes.values()), default=1)
        )
        if node.node_type in {NodeType.ERROR, NodeType.CONSTRAINT, NodeType.DECISION}:
            score += 1.0
        if node.lifecycle == LifecycleState.CRITICAL_EVIDENCE:
            score += 1.0
        return score

    def select(self, graph: TraceGraph, *, budget: int | None = None) -> ContextView:
        ranked = sorted(self._nodes(graph), key=lambda node: self._score(node, graph), reverse=True)
        items: list[ContextItem] = []
        total = 0
        for node in ranked:
            item = ContextItem.from_node(node, "semantic_score")
            if _fits(total, item, budget):
                items.append(item)
                total += item.token_count
        return self._view(
            graph,
            items,
            budget=budget,
            metadata={"proxy": self.scorer is None, "live_scorer": self.scorer is not None},
        )


class AgentDietStyleManager(ContextManager):
    name = "agentdiet_style"

    def select(self, graph: TraceGraph, *, budget: int | None = None) -> ContextView:
        seen: set[str] = set()
        kept: list[ContextItem] = []
        total = 0
        for node in reversed(self._nodes(graph)):
            fingerprint = json.dumps(node.content, ensure_ascii=False, sort_keys=True, default=str)
            waste = node.lifecycle in {LifecycleState.SUPERSEDED, LifecycleState.ARCHIVED}
            if fingerprint in seen or waste:
                continue
            seen.add(fingerprint)
            item = ContextItem.from_node(node, "nonredundant_nonexpired")
            if _fits(total, item, budget):
                kept.append(item)
                total += item.token_count
        return self._view(graph, kept, budget=budget, metadata={"style_proxy": True})


class ACONStyleManager(ContextManager):
    name = "acon_style"

    def select(self, graph: TraceGraph, *, budget: int | None = None) -> ContextView:
        items: list[ContextItem] = []
        total = 0
        for node in self._nodes(graph):
            if node.node_type in {NodeType.OBSERVATION, NodeType.ERROR, NodeType.TOOL_CALL}:
                content = _truncate_summary(node.content, 20)
                item = ContextItem(
                    node_id=node.node_id,
                    node_type=NodeType.SUMMARY,
                    content=content,
                    token_count=estimate_tokens(content),
                    reason="compressed_observation_or_history",
                    source_node_ids=(node.node_id,),
                    raw_ref=node.raw_ref,
                    preserves_sources=False,
                )
            else:
                item = ContextItem.from_node(node, "uncompressed_control_record")
            if _fits(total, item, budget):
                items.append(item)
                total += item.token_count
        return self._view(graph, items, budget=budget, metadata={"style_proxy": True})


class GraphLifecycleManager(ContextManager):
    name = "full_ours"

    def __init__(
        self,
        *,
        use_graph_edges: bool = True,
        use_lifecycle: bool = True,
        retain_failures: bool = True,
        retain_constraints: bool = True,
    ) -> None:
        self.use_graph_edges = use_graph_edges
        self.use_lifecycle = use_lifecycle
        self.retain_failures = retain_failures
        self.retain_constraints = retain_constraints
        self.engine = LifecycleEngine()

    def _reasons(self, graph: TraceGraph, node: Node) -> list[str]:
        reasons: list[str] = []
        profile = node.lifecycle_profile
        is_negative = profile.validity in {
            ValidityState.NEGATIVE_UNRESOLVED,
            ValidityState.NEGATIVE_RESOLVED,
        }
        # Ablations must disable the target signal completely; otherwise the
        # generic Active/blocks rules would silently reintroduce it.
        if node.node_type == NodeType.CONSTRAINT and not self.retain_constraints:
            return reasons
        if (node.node_type == NodeType.ERROR or is_negative) and not self.retain_failures:
            return reasons
        if node.node_type in {NodeType.GOAL, NodeType.SUBGOAL} and node.active:
            reasons.append("active_goal")
        if self.retain_constraints and node.node_type == NodeType.CONSTRAINT and node.active:
            reasons.append("active_constraint")
        if self.retain_failures and profile.validity == ValidityState.NEGATIVE_UNRESOLVED:
            reasons.append(
                "unresolved_failure"
                if node.node_type == NodeType.ERROR
                else "unresolved_negative_evidence"
            )
        if RetentionObligation.AUDIT_REQUIRED in profile.obligations or node.side_effect:
            reasons.append("audit_required")
        if self.use_lifecycle:
            if profile.relevance == RelevanceState.ACTIVE:
                reasons.append("lifecycle:active")
            if RetentionObligation.CRITICAL_EVIDENCE in profile.obligations:
                reasons.append("obligation:critical_evidence")
            if (
                self.retain_constraints
                and RetentionObligation.ACTIVE_CONSTRAINT in profile.obligations
            ):
                reasons.append("obligation:active_constraint")
            if (
                self.retain_failures
                and RetentionObligation.RETAIN_UNTIL_ACTION_COMPLETE in profile.obligations
            ):
                reasons.append("obligation:retain_until_action_complete")
        if self.use_graph_edges:
            final_ids = self.engine.final_decision_ids(graph)
            if any(
                edge.target in final_ids for edge in graph.outgoing(node.node_id, EdgeType.SUPPORTS)
            ):
                reasons.append("supports_final_decision")
            if graph.outgoing(node.node_id, EdgeType.BLOCKS):
                reasons.append("blocks_action")
        return reasons

    def select(self, graph: TraceGraph, *, budget: int | None = None) -> ContextView:
        if self.use_lifecycle:
            self.engine.apply(graph)
        nodes = self._nodes(graph)
        mandatory: list[ContextItem] = []
        optional: list[tuple[int, ContextItem]] = []
        for node in nodes:
            reasons = self._reasons(graph, node)
            if reasons:
                mandatory.append(ContextItem.from_node(node, ",".join(reasons)))
                continue
            profile = node.lifecycle_profile
            if profile.validity in {
                ValidityState.NEGATIVE_RESOLVED,
                ValidityState.SUPERSEDED,
            } or node.lifecycle in {
                LifecycleState.RESOLVED_FAILURE,
                LifecycleState.SUPERSEDED,
            }:
                summary = _truncate_summary(node.content, 16)
                item = ContextItem(
                    node_id=node.node_id,
                    node_type=NodeType.SUMMARY,
                    content=summary,
                    token_count=estimate_tokens(summary),
                    reason=f"compressed:{node.lifecycle.value}",
                    source_node_ids=(node.node_id,),
                    raw_ref=node.raw_ref,
                    preserves_sources=False,
                )
                optional.append((node.step_id + 10_000, item))
            elif (
                profile.storage == StorageState.ARCHIVED
                or node.lifecycle == LifecycleState.ARCHIVED
            ):
                if node.raw_ref:
                    handle = ContextItem(
                        node_id=node.node_id,
                        node_type=NodeType.ARCHIVE_HANDLE,
                        content=node.raw_ref,
                        token_count=estimate_tokens(node.raw_ref),
                        reason="recoverable_archive_handle",
                        source_node_ids=(node.node_id,),
                        raw_ref=node.raw_ref,
                        preserves_sources=False,
                    )
                    optional.append((node.step_id, handle))
            else:
                optional.append((node.step_id, ContextItem.from_node(node, "recent_optional")))

        items = list(mandatory)
        total = sum(item.token_count for item in items)
        for _, item in sorted(optional, key=lambda pair: pair[0], reverse=True):
            if _fits(total, item, budget):
                items.append(item)
                total += item.token_count
        return self._view(
            graph,
            items,
            budget=budget,
            metadata={
                "use_graph_edges": self.use_graph_edges,
                "use_lifecycle": self.use_lifecycle,
                "retain_failures": self.retain_failures,
                "retain_constraints": self.retain_constraints,
                "lifecycle_profile_version": "lifecycle_profile_v2",
                "mandatory_tokens": sum(item.token_count for item in mandatory),
                "over_budget_due_to_hard_constraints": budget is not None
                and sum(item.token_count for item in mandatory) > budget,
            },
        )


class NoGraphEdgesManager(GraphLifecycleManager):
    name = "ours_without_graph_edges"

    def __init__(self) -> None:
        super().__init__(use_graph_edges=False)


class NoLifecycleManager(GraphLifecycleManager):
    name = "ours_without_lifecycle_states"

    def __init__(self) -> None:
        super().__init__(use_lifecycle=False)


class NoFailureRetentionManager(GraphLifecycleManager):
    name = "ours_without_failure_retention"

    def __init__(self) -> None:
        super().__init__(retain_failures=False)


class NoConstraintRetentionManager(GraphLifecycleManager):
    name = "ours_without_constraint_retention"

    def __init__(self) -> None:
        super().__init__(retain_constraints=False)


def build_context_managers(*, last_k: int = 8) -> dict[str, ContextManager]:
    """Return every baseline and ablation named in the research report."""

    managers: list[ContextManager] = [
        FullTrajectoryManager(),
        LastKManager(k=last_k),
        TokenPruningManager(),
        SummaryOnlyManager(),
        LLMOnlyPruningManager(),
        AgentDietStyleManager(),
        ACONStyleManager(),
        NoGraphEdgesManager(),
        NoLifecycleManager(),
        NoFailureRetentionManager(),
        NoConstraintRetentionManager(),
        GraphLifecycleManager(),
    ]
    return {manager.name: manager for manager in managers}
