"""Fit a task-held-out calibrated logistic omission-risk artifact offline."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from tracegraph.decision_point_dataset import stable_task_split
from tracegraph.omission_risk import LogisticRiskArtifact


for _stream in (sys.stdout, sys.stderr):
    _reconfigure = getattr(_stream, "reconfigure", None)
    if _reconfigure is not None:
        _reconfigure(encoding="utf-8", errors="backslashreplace")


RESERVED = {
    "harm", "label", "domain", "task_id", "decision_point_id", "candidate_object_id",
    "row_id", "representation", "source_ids", "split", "trial", "seed", "replicate",
}


def _load(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    value = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(value, list):
        return value
    return value.get("rows") or value.get("representation_rows") or []


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1 / (1 + math.exp(-min(value, 700)))
    exp = math.exp(max(value, -700))
    return exp / (1 + exp)


def _features(rows: Sequence[Mapping[str, Any]]) -> list[str]:
    return sorted(
        key
        for key in {key for row in rows for key in row}
        if key not in RESERVED
        and all(
            row.get(key) is None or isinstance(row.get(key), (bool, int, float))
            for row in rows
        )
        and any(isinstance(row.get(key), (bool, int, float)) for row in rows)
    )


def _matrix(
    rows: Sequence[Mapping[str, Any]], names: Sequence[str], means: Sequence[float], scales: Sequence[float]
) -> list[list[float]]:
    return [
        [
            (float(row.get(name) or 0.0) - means[index]) / scales[index]
            for index, name in enumerate(names)
        ]
        for row in rows
    ]


def fit_logistic(
    rows: Sequence[Mapping[str, Any]], *, iterations: int = 1000, learning_rate: float = 0.05, l2: float = 0.01
) -> dict[str, Any]:
    if not rows:
        raise ValueError("risk training rows are empty")
    names = _features(rows)
    if not names:
        raise ValueError("risk training rows contain no numeric features")
    splits = stable_task_split(rows)
    train = splits["train"]
    if not train:
        raise ValueError("task-held-out split produced an empty training partition")
    means = [statistics.fmean(float(row.get(name) or 0.0) for row in train) for name in names]
    scales = []
    for index, name in enumerate(names):
        variance = statistics.fmean(
            (float(row.get(name) or 0.0) - means[index]) ** 2 for row in train
        )
        scales.append(max(math.sqrt(variance), 1e-9))
    x = _matrix(train, names, means, scales)
    y = [float(bool(row.get("harm", row.get("label", False)))) for row in train]
    coefficients = [0.0] * len(names)
    prevalence = min(1 - 1e-6, max(1e-6, statistics.fmean(y)))
    intercept = math.log(prevalence / (1 - prevalence))
    for _ in range(iterations):
        predictions = [
            _sigmoid(intercept + sum(weight * value for weight, value in zip(coefficients, vector)))
            for vector in x
        ]
        intercept -= learning_rate * statistics.fmean(pred - target for pred, target in zip(predictions, y))
        for column in range(len(coefficients)):
            gradient = statistics.fmean(
                (pred - target) * vector[column]
                for pred, target, vector in zip(predictions, y, x)
            ) + l2 * coefficients[column]
            coefficients[column] -= learning_rate * gradient
    return {
        "feature_names": names,
        "feature_means": means,
        "feature_scales": scales,
        "coefficients": coefficients,
        "intercept": intercept,
        "splits": splits,
    }


def _predict(model: Mapping[str, Any], row: Mapping[str, Any]) -> float:
    value = float(model["intercept"])
    for name, mean, scale, coefficient in zip(
        model["feature_names"], model["feature_means"], model["feature_scales"], model["coefficients"]
    ):
        value += float(coefficient) * (float(row.get(name) or 0.0) - float(mean)) / float(scale)
    return _sigmoid(value)


def _metrics(model: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], threshold: float) -> dict[str, Any]:
    labels = [bool(row.get("harm", row.get("label", False))) for row in rows]
    predictions = [_predict(model, row) for row in rows]
    positives = sum(labels)
    recall = (
        sum(label and pred >= threshold for label, pred in zip(labels, predictions)) / positives
        if positives else None
    )
    brier = statistics.fmean((pred - float(label)) ** 2 for label, pred in zip(labels, predictions)) if labels else None
    prevalence = statistics.fmean(float(label) for label in labels) if labels else None
    constant_brier = (
        statistics.fmean((prevalence - float(label)) ** 2 for label in labels)
        if labels else None
    )
    bins = []
    for index in range(10):
        members = [
            (label, pred)
            for label, pred in zip(labels, predictions)
            if (index / 10 <= pred < (index + 1) / 10) or (index == 9 and pred == 1)
        ]
        if members:
            bins.append({
                "count": len(members),
                "mean_prediction": statistics.fmean(pred for _label, pred in members),
                "observed_rate": statistics.fmean(float(label) for label, _pred in members),
            })
    ece = (
        sum(item["count"] * abs(item["mean_prediction"] - item["observed_rate"]) for item in bins) / len(labels)
        if labels else None
    )
    return {
        "rows": len(rows), "harm_positives": positives, "high_risk_recall": recall,
        "brier": brier, "constant_brier": constant_brier, "ece": ece, "calibration_bins": bins,
    }


def build_artifact(rows: Sequence[Mapping[str, Any]], *, threshold: float = 0.5) -> dict[str, Any]:
    fitted = fit_logistic(rows)
    evaluation = fitted["splits"]["test"] or fitted["splits"]["validation"]
    metrics = _metrics(fitted, evaluation, threshold)
    total_positives = sum(bool(row.get("harm", row.get("label", False))) for row in rows)
    checks = {
        "minimum_harm_positives": total_positives >= 20,
        "high_risk_recall": metrics["high_risk_recall"] is not None and metrics["high_risk_recall"] >= 0.90,
        "ece": metrics["ece"] is not None and metrics["ece"] <= 0.10,
        "brier_beats_constant": metrics["brier"] is not None
        and metrics["constant_brier"] is not None and metrics["brier"] < metrics["constant_brier"],
    }
    raw_coefficients = {
        name: float(coefficient) / float(scale)
        for name, coefficient, scale in zip(
            fitted["feature_names"], fitted["coefficients"], fitted["feature_scales"]
        )
    }
    raw_intercept = float(fitted["intercept"]) - sum(
        float(coefficient) * float(mean) / float(scale)
        for coefficient, mean, scale in zip(
            fitted["coefficients"], fitted["feature_means"], fitted["feature_scales"]
        )
    )
    artifact = LogisticRiskArtifact(
        intercept=raw_intercept,
        coefficients=raw_coefficients,
        feature_names=tuple(fitted["feature_names"]),
        threshold=threshold,
        calibration={
            "method": "held_out_reliability_bins_v1",
            "bins": metrics["calibration_bins"],
        },
        metrics={
            "harm_positives": float(total_positives),
            "high_risk_recall": float(metrics["high_risk_recall"] or 0.0),
            "ece": float(metrics["ece"] if metrics["ece"] is not None else 1.0),
            "brier": float(metrics["brier"] if metrics["brier"] is not None else 1.0),
            "constant_brier": float(
                metrics["constant_brier"] if metrics["constant_brier"] is not None else 0.0
            ),
            "evaluation_rows": float(metrics["rows"]),
        },
        training_provenance={
            "split": "task_held_out",
            "split_method": "domain_task_sha256_60_20_20",
            "split_counts": {key: len(value) for key, value in fitted["splits"].items()},
            "training_rows": len(rows),
            "gate_checks": checks,
            "fallback_when_ineligible": "deterministic_safety_mask_v1",
        },
    )
    return artifact.to_dict()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.5)
    args = parser.parse_args()
    if not 0 < args.threshold < 1:
        raise ValueError("threshold must be in (0, 1)")
    artifact = build_artifact(_load(args.input), threshold=args.threshold)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(artifact, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
