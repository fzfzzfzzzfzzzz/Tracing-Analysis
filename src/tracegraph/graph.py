"""Incrementally updated runtime capability-trace graph."""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from .schema import Edge, EdgeType, LifecycleState, Node, NodeType, new_id


class GraphValidationError(ValueError):
    """Raised when a graph violates a structural invariant."""


_EDGE_SIGNATURES: dict[EdgeType, tuple[set[NodeType], set[NodeType]]] = {
    EdgeType.PRODUCES: ({NodeType.TOOL_CALL, NodeType.MCP_CALL}, {NodeType.OBSERVATION}),
    EdgeType.FAILED_WITH: ({NodeType.TOOL_CALL, NodeType.MCP_CALL}, {NodeType.ERROR}),
    EdgeType.USES: (
        {NodeType.TOOL_CALL, NodeType.MCP_CALL, NodeType.DECISION},
        {NodeType.OBSERVATION, NodeType.ERROR, NodeType.SUMMARY, NodeType.ARCHIVE_HANDLE},
    ),
    EdgeType.SUPPORTS: (
        {NodeType.OBSERVATION, NodeType.SUMMARY, NodeType.CONSTRAINT},
        {NodeType.DECISION},
    ),
    EdgeType.BLOCKS: (
        {NodeType.ERROR, NodeType.CONSTRAINT},
        {NodeType.TOOL_CALL, NodeType.MCP_CALL, NodeType.DECISION},
    ),
    EdgeType.RESOLVES: ({NodeType.OBSERVATION, NodeType.DECISION}, {NodeType.ERROR}),
    EdgeType.SUPERSEDES: ({NodeType.OBSERVATION}, {NodeType.OBSERVATION}),
    EdgeType.COMPRESSES: (
        {NodeType.SUMMARY},
        {NodeType.OBSERVATION, NodeType.ERROR, NodeType.CONSTRAINT},
    ),
    EdgeType.RETRIES: (
        {NodeType.TOOL_CALL, NodeType.MCP_CALL},
        {NodeType.TOOL_CALL, NodeType.MCP_CALL},
    ),
    EdgeType.LEADS_TO: (
        {NodeType.DECISION},
        {NodeType.TOOL_CALL, NodeType.MCP_CALL},
    ),
}


class TraceGraph:
    """A thread-safe, in-memory trace graph with JSON persistence."""

    schema_version = "1.0"

    def __init__(self, session_id: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        self.session_id = session_id or new_id("session")
        self.metadata = metadata or {}
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, Edge] = {}
        self._lock = RLock()

    def add_node(self, node: Node) -> Node:
        with self._lock:
            if node.node_id in self.nodes:
                raise GraphValidationError(f"duplicate node id: {node.node_id}")
            if node.step_id < 0:
                raise GraphValidationError("step_id must be non-negative")
            self.nodes[node.node_id] = node
        return node

    def create_node(
        self,
        node_type: NodeType,
        content: Any,
        step_id: int,
        **kwargs: Any,
    ) -> Node:
        return self.add_node(Node(node_type=node_type, content=content, step_id=step_id, **kwargs))

    def add_edge(self, edge: Edge, *, validate_signature: bool = True) -> Edge:
        with self._lock:
            if edge.edge_id in self.edges:
                raise GraphValidationError(f"duplicate edge id: {edge.edge_id}")
            if edge.source not in self.nodes or edge.target not in self.nodes:
                raise GraphValidationError("edge endpoints must exist before the edge is added")
            if validate_signature:
                allowed_sources, allowed_targets = _EDGE_SIGNATURES[edge.edge_type]
                source_type = self.nodes[edge.source].node_type
                target_type = self.nodes[edge.target].node_type
                if source_type not in allowed_sources or target_type not in allowed_targets:
                    raise GraphValidationError(
                        f"invalid {edge.edge_type.value} signature: "
                        f"{source_type.value} -> {target_type.value}"
                    )
            self.edges[edge.edge_id] = edge
        return edge

    def connect(
        self,
        source: str,
        target: str,
        edge_type: EdgeType,
        *,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> Edge:
        return self.add_edge(
            Edge(
                source=source,
                target=target,
                edge_type=edge_type,
                confidence=confidence,
                metadata=metadata or {},
            )
        )

    def incoming(self, node_id: str, edge_type: EdgeType | None = None) -> list[Edge]:
        return [
            edge
            for edge in self.edges.values()
            if edge.target == node_id and (edge_type is None or edge.edge_type == edge_type)
        ]

    def outgoing(self, node_id: str, edge_type: EdgeType | None = None) -> list[Edge]:
        return [
            edge
            for edge in self.edges.values()
            if edge.source == node_id and (edge_type is None or edge.edge_type == edge_type)
        ]

    def neighbors(self, node_id: str, *, reverse: bool = False) -> list[str]:
        edges = self.incoming(node_id) if reverse else self.outgoing(node_id)
        return [edge.source if reverse else edge.target for edge in edges]

    def has_path(
        self,
        source: str,
        target: str,
        edge_types: set[EdgeType] | None = None,
        *,
        excluded_nodes: set[str] | None = None,
    ) -> bool:
        if source == target:
            return True
        excluded = excluded_nodes or set()
        if source in excluded or target in excluded:
            return False
        queue: deque[str] = deque([source])
        visited = {source}
        while queue:
            current = queue.popleft()
            for edge in self.outgoing(current):
                if edge_types is not None and edge.edge_type not in edge_types:
                    continue
                if edge.target in excluded or edge.target in visited:
                    continue
                if edge.target == target:
                    return True
                visited.add(edge.target)
                queue.append(edge.target)
        return False

    def set_lifecycle(self, node_id: str, state: LifecycleState, *, active: bool | None = None) -> None:
        with self._lock:
            node = self.nodes[node_id]
            node.lifecycle = state
            if active is not None:
                node.active = active

    def find_nodes(
        self,
        *,
        node_types: set[NodeType] | None = None,
        lifecycle: set[LifecycleState] | None = None,
        active: bool | None = None,
    ) -> list[Node]:
        result = []
        for node in self.nodes.values():
            if node_types is not None and node.node_type not in node_types:
                continue
            if lifecycle is not None and node.lifecycle not in lifecycle:
                continue
            if active is not None and node.active != active:
                continue
            result.append(node)
        return sorted(result, key=lambda item: (item.step_id, item.created_at, item.node_id))

    def validate(self) -> list[str]:
        errors: list[str] = []
        for edge in self.edges.values():
            if edge.source not in self.nodes:
                errors.append(f"edge {edge.edge_id} has missing source {edge.source}")
                continue
            if edge.target not in self.nodes:
                errors.append(f"edge {edge.edge_id} has missing target {edge.target}")
                continue
            sources, targets = _EDGE_SIGNATURES[edge.edge_type]
            if self.nodes[edge.source].node_type not in sources:
                errors.append(f"edge {edge.edge_id} has invalid source type")
            if self.nodes[edge.target].node_type not in targets:
                errors.append(f"edge {edge.edge_id} has invalid target type")
        for node in self.nodes.values():
            if node.side_effect and not node.raw_ref:
                errors.append(f"side-effect node {node.node_id} is missing raw_ref")
            if node.lifecycle == LifecycleState.ARCHIVED and not node.raw_ref:
                errors.append(f"archived node {node.node_id} is missing raw_ref")
        return errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "metadata": self.metadata,
            "nodes": [node.to_dict() for node in self.find_nodes()],
            "edges": [edge.to_dict() for edge in self.edges.values()],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TraceGraph":
        graph = cls(session_id=data["session_id"], metadata=dict(data.get("metadata", {})))
        for item in data.get("nodes", []):
            graph.add_node(Node.from_dict(item))
        for item in data.get("edges", []):
            graph.add_edge(Edge.from_dict(item))
        return graph

    def save(self, path: str | Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(target)

    @classmethod
    def load(cls, path: str | Path) -> "TraceGraph":
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))

    def extend(self, nodes: Iterable[Node], edges: Iterable[Edge] = ()) -> None:
        for node in nodes:
            self.add_node(node)
        for edge in edges:
            self.add_edge(edge)

