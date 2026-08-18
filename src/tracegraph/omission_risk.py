"""Deterministic safety mask and dependency-free frozen logistic risk model."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from .decision_query import DecisionQuery
from .decision_state import DecisionStateGraph, StateAtom, StateAtomType, stable_digest
from .representations import RepresentationType


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    probability: float
    omit_allowed: bool
    high_risk: bool
    reasons: tuple[str, ...]
    model_version: str


class OmissionRiskModel(Protocol):
    version: str

    def assess(
        self,
        atom: StateAtom,
        state: DecisionStateGraph,
        query: DecisionQuery,
        representation_type: RepresentationType = RepresentationType.OMIT,
    ) -> RiskAssessment: ...


_NEVER_OMIT = {
    StateAtomType.ACTIVE_GOAL,
    StateAtomType.OPEN_SUBGOAL,
    StateAtomType.UNKNOWN_SLOT,
    StateAtomType.CONFIRMATION_REQUIREMENT,
    StateAtomType.GLOBAL_POLICY_RULE,
    StateAtomType.APPLICABLE_POLICY_RULE,
    StateAtomType.CRITICAL_EVIDENCE,
    StateAtomType.SIDE_EFFECT_RECEIPT,
    StateAtomType.PENDING_OPERATION,
}


class DeterministicRiskModel:
    version = "deterministic_safety_mask_v1"

    def assess(
        self,
        atom: StateAtom,
        state: DecisionStateGraph,
        query: DecisionQuery,
        representation_type: RepresentationType = RepresentationType.OMIT,
    ) -> RiskAssessment:
        del state
        reasons: list[str] = []
        if atom.hard:
            reasons.append("hard_atom")
        if atom.atom_type in _NEVER_OMIT:
            reasons.append(f"protected_type:{atom.atom_type.value}")
        metadata = atom.metadata_dict()
        if metadata.get("blocking"):
            reasons.append("unresolved_blocking_state")
        if atom.atom_id in query.pending_confirmation:
            reasons.append("pending_confirmation")
        if representation_type == RepresentationType.ARCHIVE_HANDLE and not atom.raw_refs:
            reasons.append("unrecoverable_archive")
        if reasons:
            return RiskAssessment(1.0, False, True, tuple(sorted(set(reasons))), self.version)
        probability = 0.05
        if atom.status == "conflicting":
            probability = 0.9
            reasons.append("conflicting_state")
        elif atom.status == "superseded":
            probability = 0.01
            reasons.append("superseded_state")
        elif atom.atom_type == StateAtomType.STATE_DELTA:
            probability = 0.1
            reasons.append("recoverable_state_delta")
        else:
            reasons.append("optional_current_state")
        return RiskAssessment(
            probability,
            probability < 0.5,
            probability >= 0.5,
            tuple(reasons),
            self.version,
        )


def extract_features(
    atom: StateAtom,
    query: DecisionQuery,
    representation_type: RepresentationType,
) -> dict[str, float]:
    metadata = atom.metadata_dict()
    return {
        "hard": float(atom.hard),
        "verified": float(atom.verified),
        "current": float(atom.status == "current"),
        "conflicting": float(atom.status == "conflicting"),
        "superseded": float(atom.status == "superseded"),
        "has_archive": float(bool(atom.raw_refs)),
        "blocking": float(bool(metadata.get("blocking"))),
        "side_effect": float(atom.atom_type == StateAtomType.SIDE_EFFECT_RECEIPT),
        "pending": float(atom.atom_type == StateAtomType.PENDING_OPERATION),
        "required_slot": float(atom.atom_type == StateAtomType.UNKNOWN_SLOT),
        "policy": float(
            atom.atom_type
            in {StateAtomType.GLOBAL_POLICY_RULE, StateAtomType.APPLICABLE_POLICY_RULE}
        ),
        "query_confirmation": float(atom.atom_id in query.pending_confirmation),
        "representation_omit": float(representation_type == RepresentationType.OMIT),
        "representation_handle": float(representation_type == RepresentationType.ARCHIVE_HANDLE),
    }


@dataclass(frozen=True, slots=True)
class LogisticRiskArtifact:
    intercept: float
    coefficients: dict[str, float]
    feature_names: tuple[str, ...]
    threshold: float
    calibration: dict[str, Any]
    metrics: dict[str, float]
    training_provenance: dict[str, Any]
    version: str = "calibrated_logistic_omission_v1"

    def __post_init__(self) -> None:
        if len(self.feature_names) != len(set(self.feature_names)):
            raise ValueError("feature_names contains duplicates")
        if set(self.coefficients).difference(self.feature_names):
            raise ValueError("coefficient not declared in feature_names")
        if not 0.0 < self.threshold < 1.0:
            raise ValueError("risk threshold must be between zero and one")
        if self.training_provenance.get("split") != "task_held_out":
            raise ValueError("omission-risk artifact must use a task-held-out split")

    @property
    def feature_order(self) -> tuple[str, ...]:
        """Backward-readable alias for early R2 fixtures."""

        return self.feature_names

    @property
    def training_metrics(self) -> dict[str, float]:
        return self.metrics

    @property
    def eligible_for_runtime(self) -> bool:
        metrics = self.metrics
        positives = metrics.get("harm_positives", 0.0)
        recall = metrics.get("high_risk_recall", 0.0)
        ece = metrics.get("ece", 1.0)
        brier = metrics.get("brier", 1.0)
        constant = metrics.get("constant_brier", 0.0)
        return positives >= 20 and recall >= 0.9 and ece <= 0.1 and brier < constant

    def predict(self, features: Mapping[str, float]) -> float:
        score = self.intercept
        for name in self.feature_names:
            score += self.coefficients.get(name, 0.0) * float(features.get(name, 0.0))
        if score >= 0:
            return 1.0 / (1.0 + math.exp(-score))
        exponential = math.exp(score)
        return exponential / (1.0 + exponential)

    def assess(
        self,
        atom: StateAtom,
        state: DecisionStateGraph,
        query: DecisionQuery,
        representation_type: RepresentationType = RepresentationType.OMIT,
    ) -> RiskAssessment:
        mask = DeterministicRiskModel().assess(atom, state, query, representation_type)
        if not mask.omit_allowed:
            return mask
        if not self.eligible_for_runtime:
            return RiskAssessment(
                mask.probability,
                mask.omit_allowed,
                mask.high_risk,
                mask.reasons + ("statistical_model_gate_failed",),
                DeterministicRiskModel.version,
            )
        probability = self.predict(extract_features(atom, query, representation_type))
        return RiskAssessment(
            probability,
            probability < self.threshold,
            probability >= self.threshold,
            ("calibrated_logistic_prediction",),
            self.version,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "version": self.version,
            "intercept": self.intercept,
            "coefficients": {
                key: float(value) for key, value in sorted(self.coefficients.items())
            },
            "feature_names": list(self.feature_names),
            "threshold": self.threshold,
            "calibration": dict(sorted(self.calibration.items())),
            "metrics": {key: float(value) for key, value in sorted(self.metrics.items())},
            "training_provenance": dict(sorted(self.training_provenance.items())),
            "eligible_for_runtime": self.eligible_for_runtime,
        }
        payload["artifact_hash"] = stable_digest(payload)
        return payload

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "LogisticRiskArtifact":
        values = dict(data)
        values.pop("eligible_for_runtime", None)
        declared_hash = values.pop("artifact_hash", None)
        # Accept the early internal field names while freezing all new writes to
        # the public R2 artifact contract above.
        if "feature_names" not in values and "feature_order" in values:
            values["feature_names"] = values.pop("feature_order")
        if "metrics" not in values and "training_metrics" in values:
            values["metrics"] = values.pop("training_metrics")
        if "training_provenance" not in values and "training_split" in values:
            values["training_provenance"] = {"split": values.pop("training_split")}
        values.setdefault("threshold", 0.5)
        values.setdefault("calibration", {"method": "none"})
        values["coefficients"] = {
            str(key): float(value) for key, value in dict(values.get("coefficients", {})).items()
        }
        values["feature_names"] = tuple(map(str, values.get("feature_names", ())))
        values["metrics"] = {
            str(key): float(value)
            for key, value in dict(values.get("metrics", {})).items()
        }
        artifact = cls(**values)
        if declared_hash is not None and declared_hash != artifact.to_dict()["artifact_hash"]:
            raise ValueError("omission-risk artifact hash mismatch")
        return artifact
