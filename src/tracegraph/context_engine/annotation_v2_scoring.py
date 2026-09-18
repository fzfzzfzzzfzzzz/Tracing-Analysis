"""Definitions moved from ``tracegraph.failure_chain_annotation_v2``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from ..failure_chain_annotation import build_failure_chain_items
from ..graph import TraceGraph

from .annotation_v2_constants import (
    ANNOTATION_METADATA_FIELDS as ANNOTATION_METADATA_FIELDS,
    V2_LABEL_FIELDS as V2_LABEL_FIELDS,
    V2_SCHEMA_VERSION as V2_SCHEMA_VERSION,
)



def _read_annotation_sheet(
    path: Path, *, require_metadata: bool = True
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    _, rows = _read_csv(path)
    result: dict[str, dict[str, str]] = {}
    metadata_values: dict[str, set[str]] = {field: set() for field in ANNOTATION_METADATA_FIELDS}
    for row in rows:
        annotation_id = str(row.get("annotation_id") or "")
        if not annotation_id or annotation_id in result:
            raise ValueError(f"missing or duplicate annotation_id in {path}")
        labels: dict[str, str] = {}
        for field, allowed in V2_LABEL_FIELDS.items():
            value = str(row.get(field) or "").strip()
            if value not in allowed:
                raise ValueError(f"invalid or blank {field} for {annotation_id}: {value!r}")
            labels[field] = value
        if (
            labels["should_card_remain_active"] == "yes"
            and labels["expiry_cause"] != "still_active"
        ):
            raise ValueError(f"active card must use expiry_cause=still_active for {annotation_id}")
        if labels["should_card_remain_active"] == "no" and labels["expiry_cause"] == "still_active":
            raise ValueError(
                f"inactive card cannot use expiry_cause=still_active for {annotation_id}"
            )
        result[annotation_id] = labels
        for field in ANNOTATION_METADATA_FIELDS:
            metadata_values[field].add(str(row.get(field) or "").strip())
    inconsistent = {
        field: sorted(values) for field, values in metadata_values.items() if len(values) > 1
    }
    if inconsistent:
        raise ValueError(f"inconsistent annotation metadata in {path}: {inconsistent}")
    metadata = {field: next(iter(values), "") for field, values in metadata_values.items()}
    if require_metadata and metadata.get("annotation_version") != "2.0":
        raise ValueError(f"annotation_version=2.0 is required in {path}")
    return result, metadata


def _cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float:
    if not a or len(a) != len(b):
        raise ValueError("agreement inputs must be non-empty and equally sized")
    observed = sum(left == right for left, right in zip(a, b, strict=True)) / len(a)
    counts_a, counts_b = Counter(a), Counter(b)
    expected = sum(
        counts_a[label] / len(a) * counts_b[label] / len(a)
        for label in set(counts_a) | set(counts_b)
    )
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1 - expected)


def _gwet_ac1(a: Sequence[str], b: Sequence[str], categories: Sequence[str]) -> float:
    """Return two-rater, multi-category Gwet's AC1."""

    if not a or len(a) != len(b):
        raise ValueError("agreement inputs must be non-empty and equally sized")
    q = len(categories)
    if q < 2:
        return 1.0
    observed = sum(left == right for left, right in zip(a, b, strict=True)) / len(a)
    counts_a, counts_b = Counter(a), Counter(b)
    marginal = {label: (counts_a[label] + counts_b[label]) / (2 * len(a)) for label in categories}
    chance = sum(value * (1 - value) for value in marginal.values()) / (q - 1)
    if chance == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - chance) / (1 - chance)


def _safe_ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _confusion(
    predictions: Mapping[str, Mapping[str, str]],
    gold: Mapping[str, Mapping[str, str]],
    ids: Sequence[str],
    field: str,
) -> dict[str, dict[str, int]]:
    values: dict[str, Counter[str]] = defaultdict(Counter)
    for annotation_id in ids:
        values[str(gold[annotation_id][field])][str(predictions[annotation_id][field])] += 1
    return {actual: dict(counts) for actual, counts in sorted(values.items())}


def score_failure_chain_annotations_v2(
    annotator_a: Path,
    annotator_b: Path,
    annotation_key: Path,
    *,
    adjudication: Path | None = None,
    minimum_complete_chains: int = 60,
) -> dict[str, Any]:
    """Score v2 annotations without collapsing retention state into cause."""

    labels_a, metadata_a = _read_annotation_sheet(annotator_a)
    labels_b, metadata_b = _read_annotation_sheet(annotator_b)
    key = json.loads(annotation_key.read_text(encoding="utf-8"))
    if str(key.get("schema_version")) != V2_SCHEMA_VERSION:
        raise ValueError("v2 scorer requires schema_version=2.0")
    predictions = {
        str(item["annotation_id"]): item["prediction"] for item in key.get("items") or []
    }
    if set(labels_a) != set(labels_b) or set(labels_a) != set(predictions):
        raise ValueError("annotator files and annotation key contain different ids")
    adjudicated: dict[str, dict[str, str]] = {}
    if adjudication is not None:
        adjudicated, _ = _read_annotation_sheet(adjudication, require_metadata=False)

    sorted_ids = sorted(labels_a)
    agreement: dict[str, Any] = {}
    for field, categories in V2_LABEL_FIELDS.items():
        a_values = [labels_a[item][field] for item in sorted_ids]
        b_values = [labels_b[item][field] for item in sorted_ids]
        agreement[field] = {
            "raw_agreement": _safe_ratio(
                sum(left == right for left, right in zip(a_values, b_values, strict=True)),
                len(a_values),
            ),
            "cohen_kappa": _cohen_kappa(a_values, b_values),
            "gwet_ac1": _gwet_ac1(a_values, b_values, categories),
            "annotator_a_distribution": dict(Counter(a_values)),
            "annotator_b_distribution": dict(Counter(b_values)),
        }

    gold: dict[str, dict[str, str]] = {}
    unresolved: list[dict[str, str]] = []
    adjudication_rows: dict[str, dict[str, str]] = {}
    for annotation_id in sorted_ids:
        gold[annotation_id] = {}
        for field in V2_LABEL_FIELDS:
            left, right = labels_a[annotation_id][field], labels_b[annotation_id][field]
            if left == right:
                gold[annotation_id][field] = left
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
                            for label_field in V2_LABEL_FIELDS
                        },
                        **{field_name: "" for field_name in ANNOTATION_METADATA_FIELDS},
                        "confidence": "",
                        "notes": "",
                    },
                )
                fields = set(filter(None, row["disagreement_fields"].split(";")))
                fields.add(field)
                row["disagreement_fields"] = ";".join(sorted(fields))

    evaluated_ids = [
        annotation_id
        for annotation_id in sorted_ids
        if len(gold[annotation_id]) == len(V2_LABEL_FIELDS)
    ]
    active_tp = active_fp = active_fn = active_tn = 0
    expiry_correct = expiry_evaluated = 0
    unsafe_overmerge = unsafe_overmerge_opportunities = 0
    conservative_undermerge = conservative_undermerge_opportunities = 0
    coverage_correct = coverage_evaluated = 0
    actionable_tp = actionable_fp = actionable_fn = 0
    for annotation_id in evaluated_ids:
        predicted, actual = predictions[annotation_id], gold[annotation_id]
        predicted_active = predicted["should_card_remain_active"]
        actual_active = actual["should_card_remain_active"]
        if predicted_active in {"yes", "no"} and actual_active in {"yes", "no"}:
            active_tp += int(predicted_active == "yes" and actual_active == "yes")
            active_fp += int(predicted_active == "yes" and actual_active == "no")
            active_fn += int(predicted_active == "no" and actual_active == "yes")
            active_tn += int(predicted_active == "no" and actual_active == "no")
        if predicted_active == "no" and actual_active == "no":
            expiry_evaluated += 1
            expiry_correct += int(predicted["expiry_cause"] == actual["expiry_cause"])

        predicted_scope = predicted["scope_relation"]
        actual_scope = actual["scope_relation"]
        if predicted_scope == "same_operation" and actual_scope in {
            "same_operation",
            "different_operation",
        }:
            unsafe_overmerge_opportunities += 1
            unsafe_overmerge += int(actual_scope == "different_operation")
        if actual_scope == "same_operation":
            conservative_undermerge_opportunities += 1
            conservative_undermerge += int(
                predicted_scope in {"different_operation", "not_applicable"}
            )

        if (
            predicted["card_covers_next_step"] != "unclear"
            and actual["card_covers_next_step"] != "unclear"
        ):
            coverage_evaluated += 1
            coverage_correct += int(
                predicted["card_covers_next_step"] == actual["card_covers_next_step"]
            )

        predicted_actionable = predicted["failure_class"] == "actionable"
        actual_actionable = actual["failure_class"] == "actionable"
        actionable_tp += int(predicted_actionable and actual_actionable)
        actionable_fp += int(predicted_actionable and not actual_actionable)
        actionable_fn += int(not predicted_actionable and actual_actionable)

    provenances = {
        metadata_a.get("annotation_provenance") or "unknown",
        metadata_b.get("annotation_provenance") or "unknown",
    }
    annotation_provenance = provenances.pop() if len(provenances) == 1 else "mixed"
    identities = {
        metadata_a.get("annotator_identity") or "",
        metadata_b.get("annotator_identity") or "",
    }
    human_independent = (
        annotation_provenance == "human_independent"
        and "" not in identities
        and len(identities) == 2
    )
    complete = len(labels_a) >= minimum_complete_chains and not unresolved
    gold_distributions = {
        field: dict(Counter(gold[item][field] for item in evaluated_ids))
        for field in V2_LABEL_FIELDS
    }
    prediction_distributions = {
        field: dict(Counter(predictions[item][field] for item in evaluated_ids))
        for field in V2_LABEL_FIELDS
    }
    return {
        "schema_version": "2.0",
        "construct": "factorized_failure_card_retention_v2",
        "chain_count": len(labels_a),
        "complete": complete,
        "annotation_provenance": annotation_provenance,
        "human_independent_annotations": human_independent,
        "provisional_only": not human_independent,
        "annotators": {"a": metadata_a, "b": metadata_b},
        "agreement": agreement,
        "minimum_cohen_kappa": min(values["cohen_kappa"] for values in agreement.values()),
        "minimum_gwet_ac1": min(values["gwet_ac1"] for values in agreement.values()),
        "gold_distributions": gold_distributions,
        "prediction_distributions": prediction_distributions,
        "adjudication_applied": adjudication is not None,
        "adjudicated_chain_count": len(adjudicated),
        "unresolved_adjudications": len(unresolved),
        "retention_safety": {
            "precision": _safe_ratio(active_tp, active_tp + active_fp),
            "recall": _safe_ratio(active_tp, active_tp + active_fn),
            "confusion_counts": {
                "tp": active_tp,
                "fp": active_fp,
                "fn": active_fn,
                "tn": active_tn,
            },
        },
        "expiry_cause_accuracy_when_both_inactive": _safe_ratio(expiry_correct, expiry_evaluated),
        "scope_safety": {
            "unsafe_overmerge_count": unsafe_overmerge,
            "unsafe_overmerge_opportunities": unsafe_overmerge_opportunities,
            "unsafe_overmerge_rate": _safe_ratio(unsafe_overmerge, unsafe_overmerge_opportunities),
            "conservative_undermerge_count": conservative_undermerge,
            "conservative_undermerge_opportunities": conservative_undermerge_opportunities,
            "conservative_undermerge_rate": _safe_ratio(
                conservative_undermerge, conservative_undermerge_opportunities
            ),
        },
        "coverage": {
            "accuracy": _safe_ratio(coverage_correct, coverage_evaluated),
            "evaluated": coverage_evaluated,
            "confusion": _confusion(predictions, gold, evaluated_ids, "card_covers_next_step"),
        },
        "failure_class_actionable_precision": _safe_ratio(
            actionable_tp, actionable_tp + actionable_fp
        ),
        "failure_class_actionable_recall": _safe_ratio(
            actionable_tp, actionable_tp + actionable_fn
        ),
        "metric_counts": {
            "evaluated_chains": len(evaluated_ids),
            "expiry_cause_evaluated": expiry_evaluated,
            "coverage_evaluated": coverage_evaluated,
        },
        "disagreements": unresolved,
        "adjudication_rows": list(adjudication_rows.values()),
        "interpretation_warning": (
            "Near-single-class fields require distributions and AC1; no agreement "
            "coefficient alone establishes construct validity."
        ),
    }


def write_failure_chain_score_v2(report: Mapping[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(dict(report), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    rows = list(report.get("adjudication_rows") or [])
    adjudication_output = output.with_name("adjudication_v2.csv")
    if rows or not adjudication_output.exists():
        fields = (
            "annotation_id",
            "disagreement_fields",
            *V2_LABEL_FIELDS,
            *ANNOTATION_METADATA_FIELDS,
            "confidence",
            "notes",
        )
        with adjudication_output.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


# Imported after definitions so mutually-referential helpers initialize safely.
from .annotation_v2_package import (
    _read_csv as _read_csv,
)
