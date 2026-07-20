"""Factorized, auditable construct validation for Failure Cards.

Version two deliberately lives beside :mod:`failure_chain_annotation` instead
of changing the frozen phase-three schema.  Its central distinction is between
whether a card should remain active and why an inactive card expired.
"""

from __future__ import annotations

import csv
import hashlib
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .failure_chain_annotation import build_failure_chain_items
from .graph import TraceGraph


V2_LABEL_FIELDS: dict[str, tuple[str, ...]] = {
    "should_card_remain_active": ("yes", "no", "unclear"),
    "expiry_cause": (
        "resolved",
        "superseded",
        "corrected_syntax",
        "alternative_completed",
        "user_abandoned",
        "constraint_changed",
        "final_accepted",
        "ttl_expired",
        "still_active",
        "other",
        "unclear",
    ),
    "scope_relation": (
        "same_operation",
        "different_operation",
        "alternative_completion",
        "syntax_correction",
        "not_applicable",
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
    "card_covers_next_step": ("yes", "no", "not_applicable", "unclear"),
}

ANNOTATION_METADATA_FIELDS = (
    "annotation_provenance",
    "annotator_identity",
    "annotation_version",
    "independence_warning",
)

ANNOTATION_CONTEXT_FIELDS = (
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
)

ANNOTATION_FIELDS = (
    *ANNOTATION_CONTEXT_FIELDS,
    *V2_LABEL_FIELDS,
    *ANNOTATION_METADATA_FIELDS,
    "confidence",
    "notes",
)

V2_SCHEMA_VERSION = "2.0"
V2_GENERATOR_VERSION = "phase4_failure_chain_v2"


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _should_remain_active(expiry: str) -> str:
    if expiry == "still_active":
        return "yes"
    if expiry == "unclear":
        return "unclear"
    return "no"


def _expiry_cause(expiry: str) -> tuple[str, bool]:
    if expiry in V2_LABEL_FIELDS["expiry_cause"]:
        return expiry, False
    if expiry in {"terminal", "stale"}:
        return "other", True
    return "unclear", True


def _scope_relation(same_scope: str, relation: str) -> tuple[str, bool]:
    if same_scope == "yes":
        return "same_operation", False
    if same_scope == "no":
        return "different_operation", False
    if same_scope == "unclear":
        return "unclear", False
    if relation == "alternative_completed":
        return "alternative_completion", False
    if relation == "corrected_syntax":
        return "syntax_correction", False
    if same_scope == "not_applicable":
        return "not_applicable", False
    return "unclear", True


def convert_v1_labels(labels: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    """Deterministically map one v1 row to v2 and disclose lossy fields."""

    expiry = str(labels.get("expiry_trigger") or "unclear")
    cause, lossy_cause = _expiry_cause(expiry)
    scope, lossy_scope = _scope_relation(
        str(labels.get("same_operation_scope") or "unclear"),
        str(labels.get("relation") or "unclear"),
    )
    converted = {
        "should_card_remain_active": _should_remain_active(expiry),
        "expiry_cause": cause,
        "scope_relation": scope,
        "failure_class": str(labels.get("failure_class") or "unclear"),
        "card_covers_next_step": str(labels.get("card_covers_next_step") or "unclear"),
    }
    lossy: list[str] = []
    if lossy_cause:
        lossy.append("expiry_cause")
    if lossy_scope:
        lossy.append("scope_relation")
    return converted, lossy


def convert_v1_prediction(prediction: Mapping[str, str]) -> dict[str, str]:
    converted, _ = convert_v1_labels(prediction)
    return converted


def build_failure_chain_items_v2(
    graphs: Iterable[tuple[str, str, TraceGraph]],
) -> list[dict[str, Any]]:
    """Build v2 items while preserving the v1 trace extraction semantics."""

    items = build_failure_chain_items(graphs)
    for item in items:
        item["prediction"] = convert_v1_prediction(item["prediction"])
    return items


def _stratified_sample(
    items: list[dict[str, Any]], *, sample_size: int, seed: int
) -> list[dict[str, Any]]:
    if sample_size < 0:
        raise ValueError("sample_size must be non-negative")
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        prediction = item["prediction"]
        grouped[
            (
                str(prediction["failure_class"]),
                str(prediction["should_card_remain_active"]),
                str(prediction["scope_relation"]),
            )
        ].append(item)
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


def _write_blank_sheets(items: Sequence[Mapping[str, Any]], output_dir: Path, *, seed: int) -> None:
    for annotator, order_seed in (("a", seed + 2), ("b", seed + 3)):
        ordered = list(items)
        random.Random(order_seed).shuffle(ordered)
        path = output_dir / f"human_annotator_{annotator}.csv"
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=ANNOTATION_FIELDS)
            writer.writeheader()
            for item in ordered:
                writer.writerow(
                    {
                        **{field: item.get(field, "") for field in ANNOTATION_CONTEXT_FIELDS},
                        **{field: "" for field in V2_LABEL_FIELDS},
                        **{field: "" for field in ANNOTATION_METADATA_FIELDS},
                        "confidence": "",
                        "notes": "",
                    }
                )


def _write_instructions(output_dir: Path) -> None:
    lines = [
        "# Phase 4 Failure-chain v2 双人盲标说明",
        "",
        "两位标注者必须独立填写 `human_annotator_a.csv` 和 "
        "`human_annotator_b.csv`。冻结前不得打开 `annotation_key.json`、"
        "`migrated_codex_*.csv` 或 `migration_audit.json`。",
        "",
        "## 核心拆分",
        "",
        "- `should_card_remain_active` 只判断下一决策时 Card 是否仍应保留，不填写原因。",
        "- `expiry_cause` 只填写 inactive 的原因；active 时必须为 `still_active`。",
        "- `scope_relation` 区分同一操作、不同操作、替代完成和语法修正。",
        "- `card_covers_next_step` 判断 Card 内容是否足以指导紧接着的纠正。",
        "",
        "## 允许值",
        "",
    ]
    for field, labels in V2_LABEL_FIELDS.items():
        lines.append(f"- `{field}`: {', '.join(labels)}")
    lines.extend(
        [
            "",
            "## 一致性约束",
            "",
            "- remain=yes 时 expiry_cause 必须为 still_active。",
            "- remain=no 时 expiry_cause 不能为 still_active。",
            "- 只有证据不足时使用 unclear；notes 记录具体缺口。",
            "- 正式报告同时给出 κ、AC1、原始一致率与类别分布。",
        ]
    )
    (output_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_failure_chain_package_v2(
    *,
    controlled_items: list[dict[str, Any]],
    natural_items: list[dict[str, Any]],
    output_dir: Path,
    controlled_sample_size: int = 32,
    natural_sample_size: int = 28,
    seed: int = 4400,
) -> dict[str, Any]:
    """Export clean v2 blind sheets and a prediction key."""

    controlled = _stratified_sample(controlled_items, sample_size=controlled_sample_size, seed=seed)
    natural = _stratified_sample(natural_items, sample_size=natural_sample_size, seed=seed + 1)
    if len(controlled) != controlled_sample_size:
        raise ValueError("not enough controlled failure chains")
    if len(natural) != natural_sample_size:
        raise ValueError("not enough natural failure chains")
    items = controlled + natural
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_blank_sheets(items, output_dir, seed=seed)
    key = {
        "schema_version": V2_SCHEMA_VERSION,
        "generator_version": V2_GENERATOR_VERSION,
        "algorithm_version": "failure_card_v3_argument_completion_v1",
        "seed": seed,
        "blind_annotation": True,
        "chain_count": len(items),
        "source_counts": dict(Counter(str(item["source_kind"]) for item in items)),
        "label_fields": {field: list(labels) for field, labels in V2_LABEL_FIELDS.items()},
        "warning": "Keep this key and all migrated Codex labels hidden until human labels are frozen.",
        "items": [
            {
                key_name: item[key_name]
                for key_name in (
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
    _write_instructions(output_dir)
    return key


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return fields, rows


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ANNOTATION_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def _convert_v1_row(
    row: Mapping[str, str], *, blank_labels: bool = False
) -> tuple[dict[str, Any], list[str]]:
    converted, lossy = convert_v1_labels(row)
    metadata = {field: str(row.get(field) or "") for field in ANNOTATION_METADATA_FIELDS}
    metadata["annotation_version"] = "2.0" if not blank_labels else ""
    return (
        {
            **{field: str(row.get(field) or "") for field in ANNOTATION_CONTEXT_FIELDS},
            **({field: "" for field in V2_LABEL_FIELDS} if blank_labels else converted),
            **({field: "" for field in ANNOTATION_METADATA_FIELDS} if blank_labels else metadata),
            "confidence": "" if blank_labels else str(row.get("confidence") or ""),
            "notes": "" if blank_labels else str(row.get("notes") or ""),
        },
        lossy,
    )


def migrate_v1_package_to_v2(v1_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Create a non-destructive v2 migration of the frozen 60-chain package."""

    required = (
        "annotation_key.json",
        "annotator_a.csv",
        "annotator_b.csv",
        "adjudication.csv",
    )
    missing = [name for name in required if not (v1_dir / name).is_file()]
    if missing:
        raise ValueError(f"v1 package is incomplete: {missing}")
    input_hashes = {name: _sha256_bytes((v1_dir / name).read_bytes()) for name in required}
    v1_key = json.loads((v1_dir / "annotation_key.json").read_text(encoding="utf-8"))
    if str(v1_key.get("schema_version")) != "1.0":
        raise ValueError("migration requires the frozen v1.0 annotation key")

    output_dir.mkdir(parents=True, exist_ok=True)
    converted_key_items = []
    prediction_lossy_counts: Counter[str] = Counter()
    for item in v1_key.get("items") or []:
        prediction, lossy = convert_v1_labels(item.get("prediction") or {})
        prediction_lossy_counts.update(lossy)
        converted_key_items.append(
            {
                **{key: value for key, value in item.items() if key != "prediction"},
                "prediction": prediction,
                "v1_prediction_sha256": _canonical_sha256(item.get("prediction") or {}),
            }
        )
    v2_key = {
        **{
            key: value
            for key, value in v1_key.items()
            if key not in {"schema_version", "generator_version", "label_fields", "items"}
        },
        "schema_version": V2_SCHEMA_VERSION,
        "generator_version": "phase4_failure_chain_v2_migrated_from_phase3_v1",
        "source_schema_version": "1.0",
        "source_key_sha256": input_hashes["annotation_key.json"],
        "blind_annotation": True,
        "label_fields": {field: list(labels) for field, labels in V2_LABEL_FIELDS.items()},
        "warning": "Migrated Codex labels are provisional and must remain hidden from human annotators.",
        "items": converted_key_items,
    }
    (output_dir / "annotation_key.json").write_text(
        json.dumps(v2_key, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    label_lossy_counts: Counter[str] = Counter()
    row_counts: dict[str, int] = {}
    first_rows: list[dict[str, Any]] = []
    for source_name, output_name in (
        ("annotator_a.csv", "migrated_codex_a.csv"),
        ("annotator_b.csv", "migrated_codex_b.csv"),
        ("adjudication.csv", "migrated_adjudication.csv"),
    ):
        _, source_rows = _read_csv(v1_dir / source_name)
        converted_rows = []
        for row in source_rows:
            converted, lossy = _convert_v1_row(row)
            converted_rows.append(converted)
            label_lossy_counts.update(lossy)
        _write_csv(output_dir / output_name, converted_rows)
        row_counts[output_name] = len(converted_rows)
        if source_name == "annotator_a.csv":
            first_rows = source_rows

    blank_a = [_convert_v1_row(row, blank_labels=True)[0] for row in first_rows]
    by_id = {str(row["annotation_id"]): row for row in blank_a}
    human_a = sorted(blank_a, key=lambda row: str(row["annotation_id"]))
    human_b = list(human_a)
    random.Random(int(v1_key.get("seed") or 4400) + 101).shuffle(human_a)
    random.Random(int(v1_key.get("seed") or 4400) + 102).shuffle(human_b)
    if len(by_id) != len(first_rows):
        raise ValueError("v1 annotator sheet contains duplicate annotation ids")
    _write_csv(output_dir / "human_annotator_a.csv", human_a)
    _write_csv(output_dir / "human_annotator_b.csv", human_b)
    _write_instructions(output_dir)

    audit = {
        "schema_version": "1.0",
        "migration": "phase3_failure_chain_v1_to_phase4_v2",
        "source_directory": v1_dir.as_posix(),
        "source_hashes": input_hashes,
        "source_chain_count": int(v1_key.get("chain_count") or 0),
        "migrated_key_count": len(converted_key_items),
        "row_counts": row_counts,
        "clean_human_sheet_count": len(human_a),
        "prediction_lossy_field_counts": dict(prediction_lossy_counts),
        "annotation_lossy_field_counts": dict(label_lossy_counts),
        "lossy_rule": {
            "terminal_or_stale_expiry": "expiry_cause=other; original failure_class remains available",
            "unknown_v1_value": "mapped to unclear and counted",
        },
        "provenance": "codex_provisional_migration_not_human_gold",
        "v1_inputs_modified": False,
    }
    (output_dir / "migration_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return audit


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
