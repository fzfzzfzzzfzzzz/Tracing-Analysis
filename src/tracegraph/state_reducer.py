"""Prefix-safe deterministic reduction from EventGraph to DecisionStateGraph."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from .decision_state import (
    DecisionStateGraph,
    StateAtom,
    StateAtomType,
    StateEdge,
    StateEdgeType,
    canonical_json,
)
from .graph import TraceGraph
from .policy_rules import PolicyRule, compile_policy_rule
from .schema import EdgeType, Node, NodeType, SemanticOutcome


_NEGATIVE_OUTCOMES = {
    SemanticOutcome.NEGATIVE.value,
    SemanticOutcome.POLICY_DENIED.value,
    SemanticOutcome.TEST_FAILED.value,
}


def _schema_name(schema: Mapping[str, Any]) -> str:
    function = schema.get("function")
    if isinstance(function, Mapping):
        return str(function.get("name") or schema.get("name") or "")
    return str(schema.get("name") or "")


def _required_slots(schema: Mapping[str, Any]) -> tuple[str, ...]:
    function = schema.get("function")
    body = function if isinstance(function, Mapping) else schema
    parameters = body.get("parameters") if isinstance(body, Mapping) else None
    if not isinstance(parameters, Mapping):
        return ()
    required = parameters.get("required") or ()
    return tuple(sorted(set(map(str, required))))


def _operation_value(node: Node, status: str) -> dict[str, Any]:
    content = node.content if isinstance(node.content, Mapping) else {}
    arguments = content.get("arguments") if isinstance(content, Mapping) else {}
    return {
        "operation_key": str(node.metadata.get("operation_key") or node.node_id),
        "tool_name": str(node.metadata.get("tool_name") or content.get("tool_name") or ""),
        "arguments": dict(arguments) if isinstance(arguments, Mapping) else {},
        "status": status,
        "side_effect": bool(node.side_effect),
        "call_id": node.metadata.get("call_id") or content.get("call_id"),
    }


def _visible_edges(graph: TraceGraph, visible: set[str]) -> tuple[Any, ...]:
    return tuple(
        edge
        for edge in graph.edges.values()
        if edge.source in visible and edge.target in visible
    )


def _result_for_call(graph: TraceGraph, call_id: str, visible: set[str]) -> Node | None:
    candidates = []
    for edge in graph.outgoing(call_id):
        if edge.target not in visible:
            continue
        if edge.edge_type in {EdgeType.PRODUCES, EdgeType.FAILED_WITH}:
            candidates.append(graph.nodes[edge.target])
    return max(candidates, key=lambda node: (node.step_id, node.node_id), default=None)


def _raw_refs(nodes: Iterable[Node]) -> tuple[str, ...]:
    return tuple(sorted({node.raw_ref for node in nodes if node.raw_ref}))


def _metadata(**values: Any) -> tuple[tuple[str, Any], ...]:
    return tuple(sorted(values.items()))


def reduce_event_graph(
    event_graph: TraceGraph,
    cutoff_step: int | None = None,
    *,
    tool_schemas: Sequence[Mapping[str, Any]] = (),
    policy_rules: Sequence[PolicyRule | Mapping[str, Any] | str] = (),
) -> DecisionStateGraph:
    """Recompute state from a neutral prefix, never copying future lifecycle state.

    ``cutoff_step`` is inclusive.  Re-running this function on a graph with an
    arbitrary future suffix therefore produces the same state hash as running
    it on a physically truncated graph.
    """

    maximum = max((node.step_id for node in event_graph.nodes.values()), default=0)
    cutoff = maximum if cutoff_step is None else int(cutoff_step)
    nodes = sorted(
        (node for node in event_graph.nodes.values() if node.step_id <= cutoff),
        key=lambda node: (node.step_id, node.node_id),
    )
    visible = {node.node_id for node in nodes}
    edges = _visible_edges(event_graph, visible)
    atoms: list[StateAtom] = []
    state_edges: list[StateEdge] = []

    goals = [node for node in nodes if node.node_type == NodeType.GOAL]
    if goals:
        goal = goals[-1]
        atoms.append(
            StateAtom.create(
                StateAtomType.ACTIVE_GOAL,
                "active_goal",
                goal.content,
                (goal.node_id,),
                hard=True,
                raw_refs=_raw_refs((goal,)),
                metadata=_metadata(step_id=goal.step_id),
            )
        )
    subgoals = [node for node in nodes if node.node_type == NodeType.SUBGOAL]
    if subgoals:
        subgoal = subgoals[-1]
        atoms.append(
            StateAtom.create(
                StateAtomType.OPEN_SUBGOAL,
                "open_subgoal",
                subgoal.content,
                (subgoal.node_id,),
                hard=True,
                raw_refs=_raw_refs((subgoal,)),
                metadata=_metadata(step_id=subgoal.step_id),
            )
        )

    schemas = {_schema_name(schema): schema for schema in tool_schemas if _schema_name(schema)}
    calls = [
        node
        for node in nodes
        if node.node_type in {NodeType.TOOL_CALL, NodeType.MCP_CALL}
    ]
    operation_atoms: dict[str, StateAtom] = {}
    slots_by_key: dict[tuple[str, str], list[tuple[Node, Any]]] = defaultdict(list)
    result_atoms: dict[str, StateAtom] = {}

    for call in calls:
        result = _result_for_call(event_graph, call.node_id, visible)
        semantic = result.metadata.get("semantic_outcome") if result else None
        is_negative = bool(
            result
            and (result.node_type == NodeType.ERROR or semantic in _NEGATIVE_OUTCOMES)
        )
        if result is None:
            atom_type = StateAtomType.PENDING_OPERATION
            status = "pending"
        else:
            atom_type = StateAtomType.COMPLETED_OPERATION
            status = "failed" if is_negative else "completed"
        provenance = (call.node_id,) + ((result.node_id,) if result else ())
        operation = StateAtom.create(
            atom_type,
            f"operation:{call.metadata.get('operation_key') or call.node_id}",
            _operation_value(call, status),
            provenance,
            hard=result is None or is_negative or call.side_effect,
            raw_refs=_raw_refs(item for item in (call, result) if item is not None),
            metadata=_metadata(step_id=call.step_id, blocking=is_negative),
        )
        atoms.append(operation)
        operation_atoms[call.node_id] = operation

        arguments = operation.value["arguments"]
        for name, value in sorted(arguments.items()):
            slots_by_key[(operation.value["tool_name"], str(name))].append((call, value))

        missing = set(_required_slots(schemas.get(operation.value["tool_name"], {})))
        missing.difference_update(name for name, value in arguments.items() if value not in (None, ""))
        for name in sorted(missing):
            unknown = StateAtom.create(
                StateAtomType.UNKNOWN_SLOT,
                f"slot:{operation.value['tool_name']}:{name}",
                {"tool_name": operation.value["tool_name"], "slot": name, "reason": "required"},
                (call.node_id,),
                hard=True,
                raw_refs=_raw_refs((call,)),
                metadata=_metadata(step_id=call.step_id),
            )
            atoms.append(unknown)
            state_edges.append(
                StateEdge.create(
                    unknown.atom_id,
                    operation.atom_id,
                    StateEdgeType.REQUIRED_FOR,
                    (call.node_id,),
                )
            )

        if call.side_effect and result is None:
            confirmation = StateAtom.create(
                StateAtomType.CONFIRMATION_REQUIREMENT,
                f"confirmation:{operation.key}",
                {
                    "operation_key": operation.value["operation_key"],
                    "tool_name": operation.value["tool_name"],
                    "confirmed": False,
                },
                (call.node_id,),
                hard=True,
                raw_refs=_raw_refs((call,)),
                metadata=_metadata(step_id=call.step_id),
            )
            atoms.append(confirmation)
            state_edges.append(
                StateEdge.create(
                    confirmation.atom_id,
                    operation.atom_id,
                    StateEdgeType.REQUIRED_FOR,
                    (call.node_id,),
                )
            )

        if result is not None:
            fact_type = StateAtomType.KNOWN_FACT
            if is_negative:
                fact_type = StateAtomType.CONFLICTING_FACT
            fact = StateAtom.create(
                fact_type,
                f"result:{operation.value['operation_key']}",
                {
                    "tool_name": operation.value["tool_name"],
                    "operation_key": operation.value["operation_key"],
                    "outcome": semantic or ("negative" if is_negative else "positive"),
                    "result": result.content,
                },
                (call.node_id, result.node_id),
                hard=is_negative or call.side_effect,
                raw_refs=_raw_refs((call, result)),
                metadata=_metadata(step_id=result.step_id, blocking=is_negative),
            )
            atoms.append(fact)
            result_atoms[result.node_id] = fact
            state_edges.append(
                StateEdge.create(
                    fact.atom_id,
                    operation.atom_id,
                    StateEdgeType.BLOCKS if is_negative else StateEdgeType.SUPPORTS,
                    (call.node_id, result.node_id),
                )
            )
            delta = StateAtom.create(
                StateAtomType.STATE_DELTA,
                f"delta:{result.node_id}",
                {
                    "operation_key": operation.value["operation_key"],
                    "before": "pending",
                    "after": status,
                },
                (call.node_id, result.node_id),
                verified=True,
                raw_refs=_raw_refs((call, result)),
                metadata=_metadata(step_id=result.step_id),
            )
            atoms.append(delta)
            state_edges.append(
                StateEdge.create(
                    delta.atom_id,
                    operation.atom_id,
                    StateEdgeType.RESOLVES,
                    (call.node_id, result.node_id),
                )
            )
            if call.side_effect and not is_negative:
                receipt = StateAtom.create(
                    StateAtomType.SIDE_EFFECT_RECEIPT,
                    f"receipt:{operation.value['operation_key']}",
                    {
                        "tool_name": operation.value["tool_name"],
                        "operation_key": operation.value["operation_key"],
                        "result": result.content,
                    },
                    (call.node_id, result.node_id),
                    hard=True,
                    raw_refs=_raw_refs((call, result)),
                    metadata=_metadata(step_id=result.step_id),
                )
                atoms.append(receipt)
                state_edges.append(
                    StateEdge.create(
                        receipt.atom_id,
                        operation.atom_id,
                        StateEdgeType.SATISFIES,
                        (call.node_id, result.node_id),
                    )
                )

    for (tool_name, name), observations in sorted(slots_by_key.items()):
        latest_step = max(node.step_id for node, _ in observations)
        current_values = {
            canonical_json(value): value
            for node, value in observations
            if node.step_id == latest_step
        }
        conflict = len(current_values) > 1
        slot_atoms: list[StateAtom] = []
        for node, value in observations:
            if node.step_id < latest_step:
                atom_type = StateAtomType.SUPERSEDED_FACT
                status = "superseded"
            elif conflict:
                atom_type = StateAtomType.CONFLICTING_FACT
                status = "conflicting"
            else:
                atom_type = StateAtomType.SLOT_VALUE
                status = "current"
            atom = StateAtom.create(
                atom_type,
                f"slot:{tool_name}:{name}",
                {"tool_name": tool_name, "slot": name, "value": value},
                (node.node_id,),
                hard=atom_type in {StateAtomType.CONFLICTING_FACT},
                status=status,
                raw_refs=_raw_refs((node,)),
                metadata=_metadata(step_id=node.step_id),
            )
            atoms.append(atom)
            slot_atoms.append(atom)
            operation = operation_atoms[node.node_id]
            state_edges.append(
                StateEdge.create(
                    atom.atom_id,
                    operation.atom_id,
                    StateEdgeType.FILLS,
                    (node.node_id,),
                )
            )
        current = [atom for atom in slot_atoms if atom.status != "superseded"]
        superseded = [atom for atom in slot_atoms if atom.status == "superseded"]
        for old in superseded:
            for new in current:
                state_edges.append(
                    StateEdge.create(
                        new.atom_id,
                        old.atom_id,
                        StateEdgeType.SUPERSEDES,
                        old.source_event_ids + new.source_event_ids,
                    )
                )
        if conflict:
            for index, left in enumerate(current):
                for right in current[index + 1 :]:
                    state_edges.append(
                        StateEdge.create(
                            left.atom_id,
                            right.atom_id,
                            StateEdgeType.CONFLICTS_WITH,
                            left.source_event_ids + right.source_event_ids,
                        )
                    )

    constraints = [node for node in nodes if node.node_type == NodeType.CONSTRAINT]
    explicit_rules: list[PolicyRule] = []
    for item in policy_rules:
        explicit_rules.append(item if isinstance(item, PolicyRule) else compile_policy_rule(item))
    for constraint in constraints:
        explicit_rules.append(
            compile_policy_rule(constraint.content, source_event_ids=(constraint.node_id,))
        )
    for rule in sorted(explicit_rules, key=lambda item: item.rule_id):
        sources = tuple(source for source in rule.source_event_ids if source in visible)
        if not sources:
            # External structured rules need an EventGraph anchor.  Without it
            # they remain compiler configuration and cannot become a hard fact.
            continue
        global_rule = not rule.trigger_tools
        atoms.append(
            StateAtom.create(
                StateAtomType.GLOBAL_POLICY_RULE
                if global_rule
                else StateAtomType.APPLICABLE_POLICY_RULE,
                f"policy:{rule.rule_id}",
                rule.to_dict(),
                sources,
                hard=True,
                raw_refs=_raw_refs(event_graph.nodes[source] for source in sources),
                metadata=_metadata(step_id=max(event_graph.nodes[source].step_id for source in sources)),
            )
        )

    # Translate visible EventGraph relations only when both endpoints already
    # produced decision-facing atoms.  No lifecycle profile is copied.
    event_to_atoms: dict[str, list[StateAtom]] = defaultdict(list)
    for atom in atoms:
        for source in atom.source_event_ids:
            event_to_atoms[source].append(atom)
    relation_map = {
        EdgeType.RESOLVED_BY: StateEdgeType.RESOLVES,
        EdgeType.SUPERSEDED_BY: StateEdgeType.SUPERSEDES,
        EdgeType.BLOCKS: StateEdgeType.BLOCKS,
        EdgeType.SUPPORTS: StateEdgeType.SUPPORTS,
    }
    existing = {(edge.source, edge.target, edge.edge_type) for edge in state_edges}
    for edge in edges:
        relation = relation_map.get(edge.edge_type)
        if relation is None:
            continue
        for source_atom in event_to_atoms.get(edge.source, ()):
            for target_atom in event_to_atoms.get(edge.target, ()):
                signature = (source_atom.atom_id, target_atom.atom_id, relation)
                if source_atom.atom_id == target_atom.atom_id or signature in existing:
                    continue
                state_edges.append(
                    StateEdge.create(
                        source_atom.atom_id,
                        target_atom.atom_id,
                        relation,
                        (edge.source, edge.target),
                        confidence=edge.confidence,
                    )
                )
                existing.add(signature)

    # Deduplicate atoms that arise from equivalent policy inputs.
    unique_atoms = {atom.atom_id: atom for atom in atoms}
    unique_edges = {edge.edge_id: edge for edge in state_edges}
    return DecisionStateGraph(
        event_graph_session_id=event_graph.session_id,
        cutoff_step=cutoff,
        atoms=tuple(unique_atoms.values()),
        edges=tuple(unique_edges.values()),
    )
