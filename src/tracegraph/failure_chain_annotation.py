"""Blind, chain-level construct validation for phase-three Failure Cards."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping

from .failure_cards import build_failure_cards, classify_failure
from .graph import TraceGraph
from .schema import EdgeType, Node, NodeType, SemanticOutcome


LABEL_FIELDS: dict[str, tuple[str, ...]] = {
    "same_operation_scope": ("yes", "no", "not_applicable", "unclear"),
    "relation": (
        "resolved",
        "superseded",
        "alternative_completed",
        "corrected_syntax",
        "still_active",
        "other",
        "unclear",
    ),
    "failure_class": (
        "actionable",
        "terminal",
        "policy_denied",
        "malformed",
        "stale",
        "unclear",
    ),
    "expiry_trigger": (
        "resolved",
        "superseded",
        "alternative_completed",
        "user_abandoned",
        "constraint_changed",
        "final_accepted",
        "corrected_syntax",
        "terminal",
        "stale",
        "ttl_expired",
        "still_active",
        "other",
        "unclear",
    ),
    "card_covers_next_step": ("yes", "no", "not_applicable", "unclear"),
}

ANNOTATION_METADATA_FIELDS = (
    "annotation_provenance",
    "annotator_identity",
    "annotation_version",
    "independence_warning",
)

ANNOTATION_FIELDS = (
    "annotation_id",
    "source_kind",
    "domain",
    "task_id",
    "failure_step",
    "tool_name",
    "failed_call",
    "failure_result",
    "later_chain_context",
    "local_trace_window",
    *LABEL_FIELDS,
    *ANNOTATION_METADATA_FIELDS,
    "confidence",
    "notes",
)

_NEGATIVE_OUTCOMES = {
    SemanticOutcome.NEGATIVE.value,
    SemanticOutcome.POLICY_DENIED.value,
    SemanticOutcome.TEST_FAILED.value,
}


def _text(value: Any, limit: int = 3200) -> str:
    rendered = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    )
    rendered = rendered.replace("\x00", "")
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def _negative(node: Node) -> bool:
    return node.node_type == NodeType.ERROR or node.metadata.get(
        "semantic_outcome"
    ) in _NEGATIVE_OUTCOMES


def _producer(graph: TraceGraph, failure: Node) -> Node | None:
    edges = graph.incoming(failure.node_id, EdgeType.FAILED_WITH)
    edges += graph.incoming(failure.node_id, EdgeType.PRODUCES)
    return graph.nodes[edges[-1].source] if edges else None


def _result_for_call(graph: TraceGraph, call_id: str) -> Node | None:
    edges = graph.outgoing(call_id, EdgeType.FAILED_WITH)
    edges += graph.outgoing(call_id, EdgeType.PRODUCES)
    return graph.nodes[edges[-1].target] if edges else None


def _later_chain(graph: TraceGraph, failure: Node, producer: Node | None) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for edge_type in (EdgeType.RESOLVED_BY, EdgeType.SUPERSEDED_BY):
        for edge in graph.outgoing(failure.node_id, edge_type):
            target = graph.nodes[edge.target]
            values.append(
                {
                    "edge": edge_type.value,
                    "step": target.step_id,
                    "node_type": target.node_type.value,
                    "content": target.content,
                }
            )
    if producer is not None:
        for edge in graph.outgoing(producer.node_id, EdgeType.RETRIED_BY):
            retry = graph.nodes[edge.target]
            result = _result_for_call(graph, retry.node_id)
            values.append(
                {
                    "edge": EdgeType.RETRIED_BY.value,
                    "match_type": edge.metadata.get("match_type"),
                    "confidence": edge.confidence,
                    "step": retry.step_id,
                    "call": retry.content,
                    "result": result.content if result is not None else None,
                }
            )
    values.sort(key=lambda item: (int(item.get("step", 0)), str(item.get("edge"))))
    return values


def _trace_window(graph: TraceGraph, failure: Node) -> list[dict[str, Any]]:
    nodes = [
        node
        for node in graph.nodes.values()
        if failure.step_id - 2 <= node.step_id <= failure.step_id + 5
    ]
    nodes.sort(key=lambda node: (node.step_id, node.node_id))
    return [
        {
            "step": node.step_id,
            "node_type": node.node_type.value,
            "content": node.content,
        }
        for node in nodes
    ]


def _prediction(
    graph: TraceGraph,
    failure: Node,
    producer: Node | None,
    event_by_node: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    retry_edges = (
        graph.outgoing(producer.node_id, EdgeType.RETRIED_BY) if producer else []
    )
    resolved = bool(graph.outgoing(failure.node_id, EdgeType.RESOLVED_BY))
    superseded = bool(graph.outgoing(failure.node_id, EdgeType.SUPERSEDED_BY))
    event = event_by_node.get(failure.node_id, {})
    expiry = str(event.get("expiry_trigger") or "still_active")
    relation = "resolved" if resolved else "superseded" if superseded else expiry
    if relation not in LABEL_FIELDS["relation"]:
        relation = "still_active" if expiry == "still_active" else "other"
    failure_class = classify_failure(failure).value
    card_coverage = (
        "yes"
        if failure_class in {"actionable", "policy_denied", "malformed"}
        else "not_applicable"
    )
    return {
        "same_operation_scope": "yes" if retry_edges else "not_applicable",
        "relation": relation,
        "failure_class": failure_class,
        "expiry_trigger": expiry if expiry in LABEL_FIELDS["expiry_trigger"] else "other",
        "card_covers_next_step": card_coverage,
    }


def build_failure_chain_items(
    graphs: Iterable[tuple[str, str, TraceGraph]],
) -> list[dict[str, Any]]:
    """Build auditable chain records while keeping predictions outside CSVs."""

    items: list[dict[str, Any]] = []
    for source_kind, source_path, graph in graphs:
        source_file = Path(source_path)
        source_graph_sha256 = (
            hashlib.sha256(source_file.read_bytes()).hexdigest()
            if source_file.is_file()
            else None
        )
        _, events = build_failure_cards(graph, ttl_steps=None, confidence_threshold=0.0)
        event_by_node: dict[str, Mapping[str, Any]] = {}
        for event in events:
            for node_id in event.get("source_node_ids", ()):
                event_by_node[str(node_id)] = event
        for failure in graph.find_nodes(node_types={NodeType.ERROR, NodeType.OBSERVATION}):
            if not _negative(failure):
                continue
            producer = _producer(graph, failure)
            digest = hashlib.sha256(
                f"{source_kind}\0{graph.session_id}\0{failure.node_id}".encode("utf-8")
            ).hexdigest()[:20]
            items.append(
                {
                    "annotation_id": digest,
                    "source_kind": source_kind,
                    "source_path": source_path,
                    "source_graph_sha256": source_graph_sha256,
                    "session_id": graph.session_id,
                    "node_id": failure.node_id,
                    "domain": graph.metadata.get("domain", ""),
                    "task_id": graph.metadata.get("task_id", ""),
                    "manager": graph.metadata.get(
                        "evaluated_context_manager",
                        graph.metadata.get("context_manager", ""),
                    ),
                    "failure_step": failure.step_id,
                    "tool_name": (
                        producer.metadata.get("tool_name")
                        if producer is not None
                        else failure.metadata.get("tool_name", "")
                    ),
                    "failed_call": _text(producer.content if producer else {}, 1800),
                    "failure_result": _text(failure.content, 1800),
                    "later_chain_context": _text(
                        _later_chain(graph, failure, producer), 3200
                    ),
                    "local_trace_window": _text(_trace_window(graph, failure), 5000),
                    "prediction": _prediction(
                        graph, failure, producer, event_by_node
                    ),
                }
            )
    items.sort(key=lambda item: (item["source_kind"], item["annotation_id"]))
    return items


def _stratified_sample(
    items: list[dict[str, Any]], *, sample_size: int, seed: int
) -> list[dict[str, Any]]:
    if sample_size < 0:
        raise ValueError("sample_size must be non-negative")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        key = (
            item["prediction"]["failure_class"],
            item["prediction"]["relation"],
        )
        grouped[key].append(item)
    rng = random.Random(seed)
    for values in grouped.values():
        rng.shuffle(values)
    selected: list[dict[str, Any]] = []
    keys = sorted(grouped)
    while len(selected) < sample_size:
        progressed = False
        for key in keys:
            if grouped[key] and len(selected) < sample_size:
                selected.append(grouped[key].pop())
                progressed = True
        if not progressed:
            break
    return selected


def export_failure_chain_package(
    *,
    controlled_items: list[dict[str, Any]],
    natural_items: list[dict[str, Any]],
    output_dir: Path,
    controlled_sample_size: int = 32,
    natural_sample_size: int = 28,
    seed: int = 4400,
) -> dict[str, Any]:
    controlled = _stratified_sample(
        controlled_items, sample_size=controlled_sample_size, seed=seed
    )
    natural = _stratified_sample(
        natural_items, sample_size=natural_sample_size, seed=seed + 1
    )
    if len(controlled) != controlled_sample_size:
        raise ValueError("not enough controlled failure chains")
    if len(natural) != natural_sample_size:
        raise ValueError("not enough natural failure chains")
    items = controlled + natural
    output_dir.mkdir(parents=True, exist_ok=True)
    for annotator, order_seed in (("a", seed + 2), ("b", seed + 3)):
        ordered = list(items)
        random.Random(order_seed).shuffle(ordered)
        with (output_dir / f"annotator_{annotator}.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=ANNOTATION_FIELDS)
            writer.writeheader()
            for item in ordered:
                writer.writerow(
                    {
                        **{field: item.get(field, "") for field in ANNOTATION_FIELDS},
                        **{field: "" for field in LABEL_FIELDS},
                        **{field: "" for field in ANNOTATION_METADATA_FIELDS},
                        "confidence": "",
                        "notes": "",
                    }
                )
    key = {
        "schema_version": "1.0",
        "generator_version": "phase3_failure_chain_v1",
        "algorithm_version": "failure_card_v3_argument_completion_v1",
        "seed": seed,
        "blind_annotation": True,
        "chain_count": len(items),
        "source_counts": dict(Counter(item["source_kind"] for item in items)),
        "label_fields": {field: list(labels) for field, labels in LABEL_FIELDS.items()},
        "warning": "Do not share annotation_key.json with annotators before labels are frozen.",
        "items": [
            {
                key: item[key]
                for key in (
                    "annotation_id",
                    "source_kind",
                    "source_path",
                    "source_graph_sha256",
                    "session_id",
                    "node_id",
                    "manager",
                    "prediction",
                )
            }
            for item in items
        ],
    }
    (output_dir / "annotation_key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    instructions = [
        "# P2 Failure-chain 双人盲标说明",
        "",
        "两位标注者必须独立完成，冻结标签前不要打开或传阅 `annotation_key.json`。",
        "每行填写 5 个标签字段和 confidence；只有上下文确实不足时才使用 unclear。",
        "",
        "## 判定口径",
        "",
        "- `same_operation_scope`：后续候选调用是否仍在完成同一用户意图、同一对象上的同一操作；仅补齐/修正参数通常为 yes，换对象或换目标为 no，没有后续候选为 not_applicable。",
        "- `relation`：这条失败后来被成功结果解决、被更新失败替代、由替代路径完成，还是仍然有效。",
        "- `failure_class`：actionable 表示仍能指导下一步纠正；terminal 表示该路径不可恢复；policy_denied 表示被策略拒绝；malformed 表示工具名/参数格式错误；stale 表示已过时。",
        "- `expiry_trigger`：Failure Card 应退出受保护上下文的首个充分证据；没有充分证据时填 still_active。",
        "- `card_covers_next_step`：仅凭失败原因、失败参数差异和建议修正，是否足以避免下一次相同无效动作。",
        "",
        "## 允许值",
        "",
    ]
    for field, labels in LABEL_FIELDS.items():
        instructions.append(f"- `{field}`: {', '.join(labels)}")
    instructions.extend(
        [
            "",
            "## 评分与裁决",
            "",
            "两份 CSV 冻结后运行 `scripts/score_phase3_failure_chains.py`。首次评分会在报告旁生成 `adjudication.csv`：一致字段已预填，只需裁决空白分歧字段；随后加 `--adjudication` 重跑。",
            "同目录的 `annotator_a_view.html` / `annotator_b_view.html` 是只读阅读版，不包含机器预测；标签仍写回对应 CSV。",
        ]
    )
    (output_dir / "README.md").write_text(
        "\n".join(instructions) + "\n", encoding="utf-8"
    )
    return key


def _read_annotation_sheet(
    path: Path,
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    result: dict[str, dict[str, str]] = {}
    metadata_values: dict[str, set[str]] = {
        field: set() for field in ANNOTATION_METADATA_FIELDS
    }
    for row in rows:
        annotation_id = str(row.get("annotation_id") or "")
        if not annotation_id or annotation_id in result:
            raise ValueError(f"missing or duplicate annotation_id in {path}")
        labels: dict[str, str] = {}
        for field, allowed in LABEL_FIELDS.items():
            value = str(row.get(field) or "").strip()
            if value not in allowed:
                raise ValueError(f"invalid or blank {field} for {annotation_id}: {value!r}")
            labels[field] = value
        result[annotation_id] = labels
        for field in ANNOTATION_METADATA_FIELDS:
            metadata_values[field].add(str(row.get(field) or "").strip())
    inconsistent = {
        field: sorted(values)
        for field, values in metadata_values.items()
        if len(values) > 1
    }
    if inconsistent:
        raise ValueError(f"inconsistent annotation metadata in {path}: {inconsistent}")
    metadata = {
        field: next(iter(values), "") for field, values in metadata_values.items()
    }
    return result, metadata


def _read_labels(path: Path) -> dict[str, dict[str, str]]:
    labels, _ = _read_annotation_sheet(path)
    return labels


def _kappa(a: list[str], b: list[str]) -> float:
    observed = sum(left == right for left, right in zip(a, b, strict=True)) / len(a)
    counts_a, counts_b = Counter(a), Counter(b)
    labels = set(counts_a) | set(counts_b)
    expected = sum(
        counts_a[label] / len(a) * counts_b[label] / len(a) for label in labels
    )
    return 1.0 if expected == 1.0 and observed == 1.0 else (observed - expected) / (1 - expected)


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def score_failure_chain_annotations(
    annotator_a: Path,
    annotator_b: Path,
    annotation_key: Path,
    *,
    adjudication: Path | None = None,
) -> dict[str, Any]:
    labels_a, metadata_a = _read_annotation_sheet(annotator_a)
    labels_b, metadata_b = _read_annotation_sheet(annotator_b)
    key = json.loads(annotation_key.read_text(encoding="utf-8"))
    predictions = {
        str(item["annotation_id"]): item["prediction"] for item in key["items"]
    }
    if set(labels_a) != set(labels_b) or set(labels_a) != set(predictions):
        raise ValueError("annotator files and annotation key contain different ids")
    adjudicated: dict[str, dict[str, str]] = {}
    if adjudication is not None:
        adjudicated = _read_labels(adjudication)

    kappas = {
        field: _kappa(
            [labels_a[item][field] for item in sorted(labels_a)],
            [labels_b[item][field] for item in sorted(labels_b)],
        )
        for field in LABEL_FIELDS
    }
    gold: dict[str, dict[str, str]] = {}
    unresolved: list[dict[str, str]] = []
    adjudication_rows: dict[str, dict[str, str]] = {}
    for annotation_id in sorted(labels_a):
        gold[annotation_id] = {}
        for field in LABEL_FIELDS:
            if labels_a[annotation_id][field] == labels_b[annotation_id][field]:
                gold[annotation_id][field] = labels_a[annotation_id][field]
            elif annotation_id in adjudicated:
                gold[annotation_id][field] = adjudicated[annotation_id][field]
            else:
                unresolved.append({"annotation_id": annotation_id, "field": field})
                row = adjudication_rows.setdefault(
                    annotation_id,
                    {
                        "annotation_id": annotation_id,
                        "disagreement_fields": "",
                        **{
                            label_field: (
                                labels_a[annotation_id][label_field]
                                if labels_a[annotation_id][label_field]
                                == labels_b[annotation_id][label_field]
                                else ""
                            )
                            for label_field in LABEL_FIELDS
                        },
                    },
                )
                fields = set(filter(None, row["disagreement_fields"].split(";")))
                fields.add(field)
                row["disagreement_fields"] = ";".join(sorted(fields))

    complete = len(labels_a) >= 60 and not unresolved
    provenances = {
        metadata_a.get("annotation_provenance") or "unknown",
        metadata_b.get("annotation_provenance") or "unknown",
    }
    annotation_provenance = provenances.pop() if len(provenances) == 1 else "mixed"
    identities = {
        metadata_a.get("annotator_identity") or "",
        metadata_b.get("annotator_identity") or "",
    }
    human_independent_annotations = (
        annotation_provenance == "human_independent"
        and "" not in identities
        and len(identities) == 2
    )
    evaluated_ids = [item for item in gold if len(gold[item]) == len(LABEL_FIELDS)]
    actionable_tp = actionable_fp = actionable_fn = 0
    expiry_correct = expiry_predicted = 0
    scope_errors = scope_evaluated = 0
    coverage_correct = coverage_evaluated = 0
    for annotation_id in evaluated_ids:
        predicted, actual = predictions[annotation_id], gold[annotation_id]
        predicted_actionable = predicted["failure_class"] == "actionable"
        actual_actionable = actual["failure_class"] == "actionable"
        actionable_tp += int(predicted_actionable and actual_actionable)
        actionable_fp += int(predicted_actionable and not actual_actionable)
        actionable_fn += int(not predicted_actionable and actual_actionable)
        if predicted["expiry_trigger"] != "still_active":
            expiry_predicted += 1
            expiry_correct += int(
                predicted["expiry_trigger"] == actual["expiry_trigger"]
            )
        if actual["same_operation_scope"] in {"yes", "no"}:
            scope_evaluated += 1
            scope_errors += int(
                predicted["same_operation_scope"] != actual["same_operation_scope"]
            )
        if actual["card_covers_next_step"] in {"yes", "no"}:
            coverage_evaluated += 1
            coverage_correct += int(
                predicted["card_covers_next_step"]
                == actual["card_covers_next_step"]
            )

    report = {
        "schema_version": "1.1",
        "chain_count": len(labels_a),
        "complete": complete,
        "annotation_provenance": annotation_provenance,
        "human_independent_annotations": human_independent_annotations,
        "provisional_only": not human_independent_annotations,
        "annotators": {"a": metadata_a, "b": metadata_b},
        "cohen_kappa": min(kappas.values()),
        "field_kappas": kappas,
        "adjudication_applied": adjudication is not None,
        "adjudicated_chain_count": len(adjudicated),
        "unresolved_adjudications": len(unresolved),
        "actionable_precision": _safe_ratio(actionable_tp, actionable_tp + actionable_fp),
        "actionable_recall": _safe_ratio(actionable_tp, actionable_tp + actionable_fn),
        "expiry_precision": _safe_ratio(expiry_correct, expiry_predicted),
        "operation_scope_aggregation_error_rate": _safe_ratio(
            scope_errors, scope_evaluated
        ),
        "card_coverage_accuracy": _safe_ratio(coverage_correct, coverage_evaluated),
        "metric_counts": {
            "evaluated_chains": len(evaluated_ids),
            "expiry_predictions": expiry_predicted,
            "scope_evaluated": scope_evaluated,
            "coverage_evaluated": coverage_evaluated,
        },
        "disagreements": unresolved,
        "adjudication_rows": list(adjudication_rows.values()),
    }
    return report


def write_failure_chain_score(report: Mapping[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(dict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    fields = ("annotation_id", "disagreement_fields", *LABEL_FIELDS)
    adjudication_output = output.with_name("adjudication.csv")
    rows = list(report.get("adjudication_rows", []))
    if rows or not adjudication_output.exists():
        with adjudication_output.open(
            "w", encoding="utf-8-sig", newline=""
        ) as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
