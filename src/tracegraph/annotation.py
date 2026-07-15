"""Blind lifecycle annotation export and inter-annotator agreement."""

from __future__ import annotations

import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from .graph import TraceGraph
from .schema import LifecycleState


LIFECYCLE_LABELS = tuple(state.value for state in LifecycleState)
ANNOTATION_FIELDS = (
    "annotation_id",
    "node_type",
    "step_id",
    "content",
    "incoming_context",
    "outgoing_context",
    "annotator_label",
    "confidence",
    "notes",
)


def _text(value: Any, limit: int = 2400) -> str:
    rendered = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    )
    rendered = rendered.replace("\x00", "")
    return rendered if len(rendered) <= limit else rendered[: limit - 1] + "…"


def _neighbor_context(graph: TraceGraph, node_id: str, *, incoming: bool) -> str:
    edges = graph.incoming(node_id) if incoming else graph.outgoing(node_id)
    values = []
    for edge in sorted(edges, key=lambda item: (item.edge_type.value, item.source, item.target)):
        neighbor_id = edge.source if incoming else edge.target
        neighbor = graph.nodes[neighbor_id]
        values.append(
            {
                "edge": edge.edge_type.value,
                "node_type": neighbor.node_type.value,
                "step_id": neighbor.step_id,
                "content": _text(neighbor.content, 500),
            }
        )
    return _text(values)


def build_annotation_items(
    graphs: list[TraceGraph], *, sample_size: int, seed: int
) -> list[dict[str, Any]]:
    """Deterministically sample across predicted states while hiding labels."""

    if sample_size <= 0:
        raise ValueError("sample_size must be positive")
    grouped: dict[str, list[tuple[TraceGraph, Any]]] = defaultdict(list)
    for graph in graphs:
        for node in graph.find_nodes():
            grouped[node.lifecycle.value].append((graph, node))
    if not grouped:
        raise ValueError("no graph nodes available for annotation")
    rng = random.Random(seed)
    for candidates in grouped.values():
        candidates.sort(key=lambda item: (item[0].session_id, item[1].node_id))
        rng.shuffle(candidates)

    selected: list[tuple[TraceGraph, Any]] = []
    labels = sorted(grouped)
    while len(selected) < sample_size:
        made_progress = False
        for label in labels:
            if grouped[label] and len(selected) < sample_size:
                selected.append(grouped[label].pop())
                made_progress = True
        if not made_progress:
            break

    items = []
    seen_ids: set[str] = set()
    for graph, node in selected:
        digest = hashlib.sha256(
            f"{graph.session_id}\0{node.node_id}".encode("utf-8")
        ).hexdigest()[:20]
        if digest in seen_ids:
            raise ValueError("annotation id collision")
        seen_ids.add(digest)
        items.append(
            {
                "annotation_id": digest,
                "session_id": graph.session_id,
                "node_id": node.node_id,
                "node_type": node.node_type.value,
                "step_id": node.step_id,
                "content": _text(node.content),
                "incoming_context": _neighbor_context(graph, node.node_id, incoming=True),
                "outgoing_context": _neighbor_context(graph, node.node_id, incoming=False),
                "predicted_lifecycle": node.lifecycle.value,
            }
        )
    return items


def _write_annotation_csv(path: Path, items: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ANNOTATION_FIELDS)
        writer.writeheader()
        for item in items:
            writer.writerow(
                {
                    **{key: item.get(key, "") for key in ANNOTATION_FIELDS},
                    "annotator_label": "",
                    "confidence": "",
                    "notes": "",
                }
            )


def export_annotation_package(
    graphs: list[TraceGraph], *, output_dir: Path, sample_size: int, seed: int
) -> dict[str, Any]:
    items = build_annotation_items(graphs, sample_size=sample_size, seed=seed)
    output_dir.mkdir(parents=True, exist_ok=True)
    order_a = list(items)
    order_b = list(items)
    random.Random(seed + 1).shuffle(order_a)
    random.Random(seed + 2).shuffle(order_b)
    _write_annotation_csv(output_dir / "annotator_a.csv", order_a)
    _write_annotation_csv(output_dir / "annotator_b.csv", order_b)
    key = {
        "schema_version": "1.0",
        "seed": seed,
        "sample_size_requested": sample_size,
        "sample_size_actual": len(items),
        "allowed_labels": list(LIFECYCLE_LABELS),
        "blind_annotation": True,
        "warning": "Do not give annotation_key.json to annotators before labels are frozen.",
        "predicted_state_counts": dict(
            sorted(Counter(item["predicted_lifecycle"] for item in items).items())
        ),
        "items": [
            {
                "annotation_id": item["annotation_id"],
                "session_id": item["session_id"],
                "node_id": item["node_id"],
                "predicted_lifecycle": item["predicted_lifecycle"],
            }
            for item in items
        ],
    }
    (output_dir / "annotation_key.json").write_text(
        json.dumps(key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return key


def _labels(path: Path) -> dict[str, str]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    labels: dict[str, str] = {}
    for row in rows:
        annotation_id = str(row.get("annotation_id") or "")
        label = str(row.get("annotator_label") or "").strip()
        if not annotation_id or annotation_id in labels:
            raise ValueError(f"missing or duplicate annotation_id in {path}")
        if label not in LIFECYCLE_LABELS:
            raise ValueError(f"invalid or blank label for {annotation_id}: {label!r}")
        labels[annotation_id] = label
    return labels


def score_annotations(
    annotator_a: Path, annotator_b: Path, *, expected_ids: set[str] | None = None
) -> dict[str, Any]:
    labels_a = _labels(annotator_a)
    labels_b = _labels(annotator_b)
    if set(labels_a) != set(labels_b):
        raise ValueError("annotator files contain different annotation ids")
    if expected_ids is not None and set(labels_a) != expected_ids:
        raise ValueError("annotator files do not match annotation key")
    ids = sorted(labels_a)
    observed = sum(labels_a[item] == labels_b[item] for item in ids) / len(ids)
    counts_a = Counter(labels_a.values())
    counts_b = Counter(labels_b.values())
    expected = sum(
        (counts_a[label] / len(ids)) * (counts_b[label] / len(ids))
        for label in LIFECYCLE_LABELS
    )
    kappa = 1.0 if expected == 1.0 and observed == 1.0 else (observed - expected) / (1 - expected)
    confusion = {
        label_a: {
            label_b: sum(
                labels_a[item] == label_a and labels_b[item] == label_b for item in ids
            )
            for label_b in LIFECYCLE_LABELS
        }
        for label_a in LIFECYCLE_LABELS
    }
    disagreements = [
        {
            "annotation_id": item,
            "annotator_a": labels_a[item],
            "annotator_b": labels_b[item],
            "adjudicated_label": "",
            "adjudication_notes": "",
        }
        for item in ids
        if labels_a[item] != labels_b[item]
    ]
    return {
        "n": len(ids),
        "observed_agreement": observed,
        "expected_agreement": expected,
        "cohen_kappa": kappa,
        "label_counts_a": dict(sorted(counts_a.items())),
        "label_counts_b": dict(sorted(counts_b.items())),
        "confusion_matrix": confusion,
        "disagreement_count": len(disagreements),
        "disagreements": disagreements,
    }


def write_annotation_score(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "agreement.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    fields = (
        "annotation_id",
        "annotator_a",
        "annotator_b",
        "adjudicated_label",
        "adjudication_notes",
    )
    with (output_dir / "adjudication.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(report["disagreements"])
