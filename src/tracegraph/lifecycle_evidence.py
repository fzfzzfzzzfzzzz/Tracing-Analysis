"""Deterministic lifecycle-evidence extraction for the Phase 5.1 audit.

The extractor is deliberately outcome-blind with respect to task rewards.  It
uses only the event prefix, structured tool names/arguments/results, and the
frozen registry.  Grade A records may be applied to a cloned graph; Grade B
records are ceiling-only candidates and never mutate a graph or provider
request.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .decision_state import stable_digest
from .graph import TraceGraph
from .schema import Edge, EdgeType, Node, NodeType, SemanticOutcome


_CALL_TYPES = {NodeType.TOOL_CALL, NodeType.MCP_CALL}
_NEGATIVE_OUTCOMES = {
    SemanticOutcome.NEGATIVE.value,
    SemanticOutcome.POLICY_DENIED.value,
    SemanticOutcome.TEST_FAILED.value,
}


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _json_scalar_type(value: Any) -> str | None:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    return None


def _same_json_scalar(left: Any, right: Any) -> bool:
    left_type = _json_scalar_type(left)
    return left_type is not None and left_type == _json_scalar_type(right) and left == right


def _flatten_scalars(value: Any, prefix: str = "") -> tuple[tuple[str, Any], ...]:
    flattened: list[tuple[str, Any]] = []
    if isinstance(value, Mapping):
        for key in sorted(value, key=str):
            path = f"{prefix}.{key}" if prefix else str(key)
            flattened.extend(_flatten_scalars(value[key], path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, item in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            flattened.extend(_flatten_scalars(item, path))
    elif _json_scalar_type(value) is not None:
        flattened.append((prefix, value))
    return tuple(flattened)


def _leaf_key(path: str) -> str:
    leaf = path.rsplit(".", 1)[-1]
    return leaf.split("[", 1)[0]


def _visible_nodes(graph: TraceGraph, cutoff_step: int) -> tuple[Node, ...]:
    return tuple(
        sorted(
            (node for node in graph.nodes.values() if node.step_id <= cutoff_step),
            key=lambda item: (
                item.step_id,
                int(item.metadata.get("source_message_ordinal") or 0),
                item.node_id,
            ),
        )
    )


def _prefix_graph_hash(graph: TraceGraph, cutoff_step: int) -> str:
    nodes = _visible_nodes(graph, cutoff_step)
    visible = {node.node_id for node in nodes}
    body = {
        "nodes": [
            {
                key: value
                for key, value in node.to_dict().items()
                if key != "created_at"
            }
            for node in nodes
        ],
        "edges": [
            {
                key: value
                for key, value in edge.to_dict().items()
                if key != "created_at"
            }
            for edge in sorted(graph.edges.values(), key=lambda item: item.edge_id)
            if edge.source in visible and edge.target in visible
        ],
    }
    return stable_digest(body)


def _tool_name(node: Node) -> str:
    content = node.content if isinstance(node.content, Mapping) else {}
    return str(node.metadata.get("tool_name") or content.get("tool_name") or "")


def _arguments(node: Node) -> Mapping[str, Any]:
    content = node.content if isinstance(node.content, Mapping) else {}
    arguments = content.get("arguments")
    return arguments if isinstance(arguments, Mapping) else {}


def _is_successful_result(node: Node) -> bool:
    return bool(
        node.node_type == NodeType.OBSERVATION
        and str(node.metadata.get("status") or "success") == "success"
        and node.metadata.get("semantic_outcome") not in _NEGATIVE_OUTCOMES
    )


def _producer_call(graph: TraceGraph, result: Node, visible: set[str]) -> Node | None:
    producer_ids = sorted(
        edge.source
        for edge in graph.incoming(result.node_id, EdgeType.PRODUCES)
        if edge.source in visible and graph.nodes[edge.source].node_type in _CALL_TYPES
    )
    if len(producer_ids) != 1:
        return None
    return graph.nodes[producer_ids[0]]


def _decision_for_call(graph: TraceGraph, call: Node, visible: set[str]) -> Node | None:
    decision_ids = sorted(
        edge.source
        for edge in graph.incoming(call.node_id, EdgeType.LEADS_TO)
        if edge.source in visible and graph.nodes[edge.source].node_type == NodeType.DECISION
    )
    if len(decision_ids) != 1:
        return None
    return graph.nodes[decision_ids[0]]


def _result_for_call(graph: TraceGraph, call: Node, visible: set[str]) -> Node | None:
    result_ids = sorted(
        edge.target
        for edge in graph.outgoing(call.node_id)
        if edge.edge_type in {EdgeType.PRODUCES, EdgeType.FAILED_WITH}
        and edge.target in visible
    )
    if len(result_ids) != 1:
        return None
    return graph.nodes[result_ids[0]]


def _event_order(node: Node) -> tuple[int, int, str]:
    return (
        node.step_id,
        int(node.metadata.get("source_message_ordinal") or 0),
        node.node_id,
    )


def _is_later(candidate: Node, source: Node) -> bool:
    return _event_order(candidate) > _event_order(source)


@dataclass(frozen=True, slots=True)
class LifecycleEvidenceRecord:
    """One deterministic relation or retention obligation."""

    grade: str
    relation: str
    source_event_id: str
    target_event_id: str
    verifier: str
    confidence: float
    may_generate_hard_dead: bool
    source_path: str = ""
    target_path: str = ""
    value_sha256: str = ""
    producer_tool: str = ""
    consumer_tool: str = ""
    rationale: str = ""

    def __post_init__(self) -> None:
        if self.grade not in {"A", "B"}:
            raise ValueError("lifecycle evidence grade must be A or B")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("lifecycle evidence confidence must be between zero and one")
        if self.may_generate_hard_dead and (self.grade != "A" or self.confidence != 1.0):
            raise ValueError("hard-dead evidence must be Grade A at confidence 1.0")

    @property
    def evidence_id(self) -> str:
        return f"evidence_{stable_digest(self.to_dict(include_id=False))[:24]}"

    def to_dict(self, *, include_id: bool = True) -> dict[str, Any]:
        result = {
            "grade": self.grade,
            "relation": self.relation,
            "source_event_id": self.source_event_id,
            "target_event_id": self.target_event_id,
            "verifier": self.verifier,
            "confidence": self.confidence,
            "may_generate_hard_dead": self.may_generate_hard_dead,
            "source_path": self.source_path,
            "target_path": self.target_path,
            "value_sha256": self.value_sha256,
            "producer_tool": self.producer_tool,
            "consumer_tool": self.consumer_tool,
            "rationale": self.rationale,
        }
        if include_id:
            result["evidence_id"] = self.evidence_id
        return result


@dataclass(frozen=True, slots=True)
class LifecycleEvidenceReport:
    """Prefix-only Grade A lower bound plus Grade B optimistic candidates."""

    cutoff_step: int
    source_graph_hash: str
    config_sha256: str
    records: tuple[LifecycleEvidenceRecord, ...]
    schema_version: str = "lifecycle_evidence_report_v1"

    def __post_init__(self) -> None:
        ordered = tuple(sorted(self.records, key=lambda item: item.evidence_id))
        if len({item.evidence_id for item in ordered}) != len(ordered):
            raise ValueError("duplicate lifecycle evidence record")
        object.__setattr__(self, "records", ordered)

    @property
    def grade_a_records(self) -> tuple[LifecycleEvidenceRecord, ...]:
        return tuple(item for item in self.records if item.grade == "A")

    @property
    def grade_b_records(self) -> tuple[LifecycleEvidenceRecord, ...]:
        return tuple(item for item in self.records if item.grade == "B")

    @property
    def hard_dead_records(self) -> tuple[LifecycleEvidenceRecord, ...]:
        return tuple(item for item in self.records if item.may_generate_hard_dead)

    @property
    def report_hash(self) -> str:
        return stable_digest(self.to_dict(include_hash=False))

    def to_dict(self, *, include_hash: bool = True) -> dict[str, Any]:
        result = {
            "schema_version": self.schema_version,
            "cutoff_step": self.cutoff_step,
            "source_graph_hash": self.source_graph_hash,
            "config_sha256": self.config_sha256,
            "records": [item.to_dict() for item in self.records],
            "counts": {
                "grade_a": len(self.grade_a_records),
                "grade_b": len(self.grade_b_records),
                "hard_dead": len(self.hard_dead_records),
            },
        }
        if include_hash:
            result["report_hash"] = self.report_hash
        return result


def load_evidence_config(path: str | Path) -> dict[str, Any]:
    """Load the frozen registry and reject unsafe or incomplete variants."""

    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("lifecycle evidence configuration must be a JSON object")
    if value.get("schema_version") != "phase51_lifecycle_evidence_config_v1":
        raise ValueError("unsupported lifecycle evidence configuration")
    grade_a = value.get("evidence", {}).get("grade_a", {})
    scalar = grade_a.get("complete_scalar_consumption", {})
    if scalar.get("confidence") != 1.0 or not scalar.get("require_full_result_exhaustiveness"):
        raise ValueError("Grade A scalar consumption must be exhaustive at confidence 1.0")
    grade_b = value.get("evidence", {}).get("grade_b_ceiling_only", {})
    if any(item.get("may_generate_hard_dead") for item in grade_b.values()):
        raise ValueError("Grade B evidence cannot generate hard-dead")
    if not value.get("ceiling_projection", {}).get("never_emit_as_provider_request"):
        raise ValueError("ceiling projection must be forbidden from provider emission")
    if value.get("external_execution", {}).get("provider_generation_authorized"):
        raise ValueError("Phase 5.1 evidence audit cannot authorize provider generation")
    return value


def evidence_config_sha256(config: Mapping[str, Any]) -> str:
    return stable_digest(dict(config))


def _complete_scalar(
    payload: Any,
    *,
    allowed_types: set[str],
    wrapper_pattern: re.Pattern[str],
) -> tuple[str, Any] | None:
    scalar_type = _json_scalar_type(payload)
    if scalar_type in allowed_types:
        return ("$", payload)
    if not isinstance(payload, Mapping) or len(payload) != 1:
        return None
    key, value = next(iter(payload.items()))
    scalar_type = _json_scalar_type(value)
    if scalar_type not in allowed_types or not wrapper_pattern.fullmatch(str(key)):
        return None
    return (str(key), value)


def _value_sha256(value: Any) -> str:
    return stable_digest(
        {
            "json_type": _json_scalar_type(value),
            "canonical_value": _canonical_json(value),
        }
    )


def _matching_argument_paths(arguments: Mapping[str, Any], value: Any) -> tuple[str, ...]:
    return tuple(
        path
        for path, candidate in _flatten_scalars(arguments)
        if _same_json_scalar(candidate, value)
    )


def _identifier_values(
    payload: Any,
    identifier_pattern: re.Pattern[str],
) -> tuple[tuple[str, Any], ...]:
    return tuple(
        (path, value)
        for path, value in _flatten_scalars(payload)
        if identifier_pattern.search(_leaf_key(path).lower())
        and value not in (None, "")
        and not isinstance(value, bool)
    )


def extract_lifecycle_evidence(
    graph: TraceGraph,
    *,
    cutoff_step: int,
    config: Mapping[str, Any],
) -> LifecycleEvidenceReport:
    """Extract prefix-only evidence without reading rewards or future nodes."""

    nodes = _visible_nodes(graph, cutoff_step)
    visible = {node.node_id for node in nodes}
    calls = tuple(node for node in nodes if node.node_type in _CALL_TYPES)
    results = tuple(node for node in nodes if node.node_type == NodeType.OBSERVATION)
    evidence = config["evidence"]
    scalar_config = evidence["grade_a"]["complete_scalar_consumption"]
    receipt_config = evidence["grade_a"]["side_effect_receipt"]
    entity_config = evidence["grade_b_ceiling_only"]["exact_entity_flow_candidate"]
    mutation_config = evidence["grade_b_ceiling_only"]["mutation_invalidation_candidate"]
    allowed_tools = {str(item).lower() for item in scalar_config["producer_tool_allowlist"]}
    allowed_types = set(map(str, scalar_config["allowed_scalar_types"]))
    wrapper_pattern = re.compile(str(scalar_config["allowed_singleton_wrapper_key_regex"]))
    identifier_pattern = re.compile(str(entity_config["identifier_key_regex"]))
    write_pattern = re.compile(str(mutation_config["write_tool_prefix_regex"]))
    records: dict[str, LifecycleEvidenceRecord] = {}

    def add(record: LifecycleEvidenceRecord) -> None:
        records[record.evidence_id] = record

    for result in results:
        producer = _producer_call(graph, result, visible)
        if producer is None or not _is_successful_result(result):
            continue
        producer_tool = _tool_name(producer).lower()

        if producer.side_effect and receipt_config.get("enabled"):
            add(
                LifecycleEvidenceRecord(
                    grade="A",
                    relation="side_effect_receipt",
                    source_event_id=producer.node_id,
                    target_event_id=result.node_id,
                    verifier=str(receipt_config["verifier"]),
                    confidence=float(receipt_config["confidence"]),
                    may_generate_hard_dead=False,
                    producer_tool=producer_tool,
                    rationale="successful_side_effect_receipt_is_a_retention_obligation",
                )
            )

        if (
            scalar_config.get("enabled")
            and not producer.side_effect
            and producer_tool in allowed_tools
        ):
            complete = _complete_scalar(
                result.content,
                allowed_types=allowed_types,
                wrapper_pattern=wrapper_pattern,
            )
            if complete is not None:
                source_path, scalar = complete
                for consumer in calls:
                    if not _is_later(consumer, result):
                        continue
                    matching_paths = _matching_argument_paths(_arguments(consumer), scalar)
                    if not matching_paths:
                        continue
                    decision = _decision_for_call(graph, consumer, visible)
                    if decision is None or not _is_later(decision, result):
                        continue
                    add(
                        LifecycleEvidenceRecord(
                            grade="A",
                            relation="complete_scalar_consumption",
                            source_event_id=result.node_id,
                            target_event_id=decision.node_id,
                            verifier=str(scalar_config["verifier"]),
                            confidence=float(scalar_config["confidence"]),
                            may_generate_hard_dead=True,
                            source_path=source_path,
                            target_path=matching_paths[0],
                            value_sha256=_value_sha256(scalar),
                            producer_tool=producer_tool,
                            consumer_tool=_tool_name(consumer).lower(),
                            rationale="entire_scalar_result_is_exactly_represented_in_later_arguments",
                        )
                    )
                    break

        if not entity_config.get("enabled"):
            continue
        source_identifiers = _identifier_values(result.content, identifier_pattern)
        if not source_identifiers:
            continue
        for consumer in calls:
            if not _is_later(consumer, result):
                continue
            consumer_arguments = _arguments(consumer)
            shared: tuple[str, Any, str] | None = None
            for source_path, value in source_identifiers:
                matching_paths = _matching_argument_paths(consumer_arguments, value)
                if matching_paths:
                    shared = (source_path, value, matching_paths[0])
                    break
            if shared is None:
                continue
            source_path, value, target_path = shared
            decision = _decision_for_call(graph, consumer, visible)
            target = decision or consumer
            add(
                LifecycleEvidenceRecord(
                    grade="B",
                    relation="exact_entity_flow_candidate",
                    source_event_id=result.node_id,
                    target_event_id=target.node_id,
                    verifier=str(entity_config["verifier"]),
                    confidence=1.0,
                    may_generate_hard_dead=False,
                    source_path=source_path,
                    target_path=target_path,
                    value_sha256=_value_sha256(value),
                    producer_tool=producer_tool,
                    consumer_tool=_tool_name(consumer).lower(),
                    rationale="exact_entity_overlap_does_not_prove_full_result_consumption",
                )
            )

            consumer_tool = _tool_name(consumer).lower()
            consumer_result = _result_for_call(graph, consumer, visible)
            if (
                mutation_config.get("enabled")
                and (consumer.side_effect or write_pattern.search(consumer_tool))
                and consumer_result is not None
                and _is_successful_result(consumer_result)
            ):
                add(
                    LifecycleEvidenceRecord(
                        grade="B",
                        relation="mutation_invalidation_candidate",
                        source_event_id=result.node_id,
                        target_event_id=consumer_result.node_id,
                        verifier=str(mutation_config["verifier"]),
                        confidence=1.0,
                        may_generate_hard_dead=False,
                        source_path=source_path,
                        target_path=target_path,
                        value_sha256=_value_sha256(value),
                        producer_tool=producer_tool,
                        consumer_tool=consumer_tool,
                        rationale="mutation_overlap_lacks_effect_scope_version_or_refresh_dominance",
                    )
                )

    return LifecycleEvidenceReport(
        cutoff_step=cutoff_step,
        source_graph_hash=_prefix_graph_hash(graph, cutoff_step),
        config_sha256=evidence_config_sha256(config),
        records=tuple(records.values()),
    )


def apply_grade_a_overlay(
    graph: TraceGraph,
    report: LifecycleEvidenceReport,
) -> TraceGraph:
    """Clone ``graph`` and add only verified Grade A hard-dead relations."""

    if report.source_graph_hash != _prefix_graph_hash(graph, report.cutoff_step):
        raise ValueError("lifecycle evidence report does not belong to this graph prefix")
    overlay = TraceGraph.from_dict(graph.to_dict())
    existing = {
        (edge.source, edge.target, edge.edge_type)
        for edge in overlay.edges.values()
    }
    for record in report.hard_dead_records:
        if record.relation != "complete_scalar_consumption":
            raise ValueError(f"unsupported Grade A hard-dead relation: {record.relation}")
        signature = (
            record.source_event_id,
            record.target_event_id,
            EdgeType.PROVIDES_INPUT,
        )
        if signature in existing:
            continue
        source = overlay.nodes[record.source_event_id]
        target = overlay.nodes[record.target_event_id]
        if source.step_id > report.cutoff_step or target.step_id > report.cutoff_step:
            raise ValueError("Grade A overlay edge crosses the frozen cutoff")
        overlay.add_edge(
            Edge(
                source=record.source_event_id,
                target=record.target_event_id,
                edge_type=EdgeType.PROVIDES_INPUT,
                confidence=1.0,
                metadata={
                    "evidence_id": record.evidence_id,
                    "evidence_grade": "A",
                    "verifier": record.verifier,
                    "value_sha256": record.value_sha256,
                    "phase": "phase5_1",
                },
                edge_id=f"edge_{record.evidence_id}",
                created_at=target.created_at,
            )
        )
        existing.add(signature)
    return overlay
