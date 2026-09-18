"""Gold-ready evaluation contracts for failure-experience preservation and utility.

The functions in this module are deliberately offline and side-effect free.  They
do not infer gold labels, inspect held-out data, or call a model.  Synthetic and
development examples can exercise the complete pipeline now; independently
annotated ``failure_episode_gold_v1`` records can be plugged in later unchanged.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

from .compression_audit.models import (
    FailureChainGold,
    FailureEpisodeEvidencePolicy,
    PrefixRecord,
)


DIAGNOSTIC_SCHEMA_VERSION = "failure_experience_diagnostic_v1"
CONTINUATION_SCHEMA_VERSION = "failure_experience_continuation_v1"

EXPERIENCE_CONDITIONS = (
    "no_memory",
    "success_only",
    "failure_outcome",
    "failure_reason_and_success",
    "structured_failure_episode",
    "full_history",
)


def _unique_strings(values: Iterable[str], label: str) -> tuple[str, ...]:
    result = tuple(str(value) for value in values)
    if any(not value for value in result) or len(result) != len(set(result)):
        raise ValueError(f"{label} must contain unique nonempty strings")
    return result


def _prefix_order(prefix: PrefixRecord, event_ids: Iterable[str]) -> tuple[str, ...]:
    requested = set(map(str, event_ids))
    known = {str(event["event_id"]) for event in prefix.events}
    unknown = requested - known
    if unknown:
        raise ValueError(f"diagnostic references unknown events: {sorted(unknown)}")
    return tuple(
        str(event["event_id"])
        for event in sorted(prefix.events, key=lambda event: int(event["step_id"]))
        if str(event["event_id"]) in requested
    )


def _evidence_diagnostic(
    policy: FailureEpisodeEvidencePolicy,
    observed_event_ids: Sequence[str],
) -> dict[str, Any]:
    observed = _unique_strings(observed_event_ids, "observed evidence")
    observed_set = set(observed)
    relevant = set(policy.relevant_evidence_ids)
    positions = {event_id: index for index, event_id in enumerate(observed)}

    alternatives = []
    for index, required in enumerate(policy.alternative_evidence_sets):
        required_set = set(required)
        covered = len(required_set & observed_set)
        alternatives.append({
            "alternative_index": index,
            "required_event_ids": list(required),
            "covered_event_ids": sorted(required_set & observed_set),
            "missing_event_ids": sorted(required_set - observed_set),
            "coverage": covered / len(required_set),
            "sufficient": required_set <= observed_set,
        })
    best = max(
        alternatives,
        key=lambda item: (item["coverage"], -len(item["missing_event_ids"]),
                          -item["alternative_index"]),
    )
    path = policy.causal_paths[best["alternative_index"]]
    constraint_rows = []
    for left, right in path.constraints:
        present = left in positions and right in positions
        constraint_rows.append({
            "source_event_id": left,
            "target_event_id": right,
            "present": present,
            "ordered": bool(present and positions[left] < positions[right]),
        })
    causal_order_rate = (
        sum(row["ordered"] for row in constraint_rows) / len(constraint_rows)
        if constraint_rows else 1.0
    )
    irrelevant = sorted(observed_set - relevant)
    return {
        "observed_event_ids": list(observed),
        "best_alternative_index": best["alternative_index"],
        "best_path_coverage": best["coverage"],
        "path_sufficient": any(item["sufficient"] for item in alternatives),
        "relevant_precision": (
            len(observed_set & relevant) / len(observed_set) if observed_set else 0.0
        ),
        "irrelevant_event_ids": irrelevant,
        "causal_constraints": constraint_rows,
        "causal_order_rate": causal_order_rate,
        "causal_order_pass": causal_order_rate == 1.0,
        "alternatives": alternatives,
    }


def diagnose_preservation(
    prefix: PrefixRecord,
    gold: FailureChainGold,
    query_type: str,
    *,
    constructed_episode_event_ids: Sequence[str] | None = None,
    constructed_edges: Sequence[tuple[str, str]] | None = None,
    visible_event_ids: Sequence[str],
    answer_event_ids: Sequence[str] | None = None,
    semantic_pass: bool | None = None,
    protocol_valid: bool | None = None,
    send_eligible: bool = True,
    context_tokens: int | None = None,
    context_budget_tokens: int | None = None,
) -> dict[str, Any]:
    """Separate construction, selection, answer, semantic, and protocol failures.

    ``constructed_episode_event_ids`` must describe the events that an extractor
    grouped into the candidate failure episode; passing every graph node would make
    the construction diagnostic meaningless.  Selection IDs are normalized to
    public chronology.  Answer IDs keep the model-provided order so inversions are
    observable.
    """

    if gold.prefix_id != prefix.prefix_id:
        raise ValueError("gold/prefix mismatch")
    episode = gold.failure_episode
    if episode is None:
        raise ValueError("failure_episode_gold_v1 is required for layered diagnostics")
    episode.validate_against_prefix(prefix)
    policy = episode.policy_for_query(query_type)
    if policy is None:
        raise ValueError(f"failure episode has no policy for query type: {query_type}")
    if context_tokens is not None and context_tokens < 0:
        raise ValueError("context_tokens must be non-negative")
    if context_budget_tokens is not None and context_budget_tokens < 0:
        raise ValueError("context_budget_tokens must be non-negative")

    construction = None
    if constructed_episode_event_ids is not None:
        ordered = _prefix_order(prefix, constructed_episode_event_ids)
        construction = _evidence_diagnostic(policy, ordered)
        expected_edges = set(policy.causal_paths[
            construction["best_alternative_index"]
        ].constraints)
        supplied_edges = (
            {tuple(map(str, edge)) for edge in constructed_edges}
            if constructed_edges is not None else None
        )
        unknown_edge_nodes = {
            node for edge in (supplied_edges or set()) for node in edge
        } - {str(event["event_id"]) for event in prefix.events}
        if unknown_edge_nodes:
            raise ValueError(
                f"constructed edges reference unknown events: {sorted(unknown_edge_nodes)}"
            )
        construction.update({
            "expected_edge_count": len(expected_edges),
            "constructed_edge_count": (
                len(supplied_edges) if supplied_edges is not None else None
            ),
            "edge_recall": (
                (len(expected_edges & supplied_edges) / len(expected_edges)
                 if expected_edges else 1.0)
                if supplied_edges is not None else None
            ),
        })
        construction["pass"] = bool(
            construction["path_sufficient"]
            and (construction["edge_recall"] in (None, 1.0))
        )

    selection = _evidence_diagnostic(policy, _prefix_order(prefix, visible_event_ids))
    selection["send_eligible"] = bool(send_eligible)
    selection["pass"] = bool(send_eligible and selection["path_sufficient"])

    answer = None
    if answer_event_ids is not None:
        answer = _evidence_diagnostic(policy, answer_event_ids)
        answer["pass"] = bool(
            answer["path_sufficient"] and answer["causal_order_pass"]
        )

    if construction is not None and not construction["pass"]:
        first_failure_stage = "construction"
    elif not send_eligible:
        first_failure_stage = "selection_budget"
    elif not selection["path_sufficient"]:
        first_failure_stage = "selection_evidence"
    elif answer is None:
        first_failure_stage = "pending_answer"
    elif not answer["pass"]:
        first_failure_stage = "answer_evidence"
    elif semantic_pass is None:
        first_failure_stage = "pending_semantic_evaluation"
    elif not semantic_pass:
        first_failure_stage = "semantics"
    elif protocol_valid is None:
        first_failure_stage = "pending_protocol_evaluation"
    elif not protocol_valid:
        first_failure_stage = "protocol"
    else:
        first_failure_stage = "pass"

    return {
        "schema_version": DIAGNOSTIC_SCHEMA_VERSION,
        "prefix_id": prefix.prefix_id,
        "query_type": query_type,
        "construction": construction,
        "selection": selection,
        "answer": answer,
        "semantic_pass": semantic_pass,
        "protocol_valid": protocol_valid,
        "context_tokens": context_tokens,
        "context_budget_tokens": context_budget_tokens,
        "first_failure_stage": first_failure_stage,
    }


@dataclass(frozen=True, slots=True)
class OracleGateThresholds:
    """Explicit, preregisterable validation thresholds; no hidden defaults."""

    min_cases: int
    min_context_path_pass_rate: float
    min_answer_path_pass_rate: float
    min_semantic_pass_rate: float
    min_protocol_valid_rate: float
    min_send_eligible_rate: float

    def __post_init__(self) -> None:
        if type(self.min_cases) is not int or self.min_cases <= 0:
            raise ValueError("min_cases must be a positive integer")
        for name, value in asdict(self).items():
            if name == "min_cases":
                continue
            if type(value) not in (int, float) or not 0 <= value <= 1:
                raise ValueError(f"{name} must be between zero and one")


def evaluate_oracle_gate(
    diagnostics: Sequence[Mapping[str, Any]],
    thresholds: OracleGateThresholds,
) -> dict[str, Any]:
    """Evaluate an Oracle capability gate without changing any answer or score."""

    rows = list(diagnostics)
    required_booleans: list[tuple[str, list[bool]]] = [
        ("context_path_pass_rate", []),
        ("answer_path_pass_rate", []),
        ("semantic_pass_rate", []),
        ("protocol_valid_rate", []),
        ("send_eligible_rate", []),
    ]
    for row in rows:
        if row.get("schema_version") != DIAGNOSTIC_SCHEMA_VERSION:
            raise ValueError("oracle gate received an unsupported diagnostic row")
        selection = row.get("selection") or {}
        answer = row.get("answer") or {}
        values = (
            selection.get("path_sufficient"),
            answer.get("pass"),
            row.get("semantic_pass"),
            row.get("protocol_valid"),
            selection.get("send_eligible"),
        )
        if any(type(value) is not bool for value in values):
            raise ValueError("oracle gate requires complete boolean diagnostics")
        for (_, bucket), value in zip(required_booleans, values):
            bucket.append(value)

    rates = {
        name: (sum(values) / len(values) if values else 0.0)
        for name, values in required_booleans
    }
    minimums = {
        "context_path_pass_rate": thresholds.min_context_path_pass_rate,
        "answer_path_pass_rate": thresholds.min_answer_path_pass_rate,
        "semantic_pass_rate": thresholds.min_semantic_pass_rate,
        "protocol_valid_rate": thresholds.min_protocol_valid_rate,
        "send_eligible_rate": thresholds.min_send_eligible_rate,
    }
    checks = {name: rates[name] >= minimum for name, minimum in minimums.items()}
    checks["minimum_cases"] = len(rows) >= thresholds.min_cases
    return {
        "schema_version": "failure_experience_oracle_gate_v1",
        "case_count": len(rows),
        "thresholds": asdict(thresholds),
        "rates": rates,
        "checks": checks,
        "pass": all(checks.values()),
    }


@dataclass(frozen=True, slots=True)
class ContinuationTrial:
    """Normalized result for one method resumed from one fixed checkpoint."""

    task_id: str
    checkpoint_id: str
    method_id: str
    condition_id: str
    task_success: bool
    attempted_action_keys: tuple[str, ...]
    known_failed_action_keys: tuple[str, ...]
    reacquisition_calls: int
    tool_calls: int
    model_calls: int
    input_tokens: int
    output_tokens: int
    observation_tokens: int
    latency_seconds: float
    steps_to_resolution: int | None = None
    schema_version: str = CONTINUATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in ("task_id", "checkpoint_id", "method_id"):
            if not getattr(self, name):
                raise ValueError(f"{name} must be nonempty")
        if self.condition_id not in EXPERIENCE_CONDITIONS:
            raise ValueError(f"unsupported experience condition: {self.condition_id}")
        if type(self.task_success) is not bool:
            raise ValueError("task_success must be boolean")
        _unique_strings(self.known_failed_action_keys, "known failed actions")
        if any(not str(value) for value in self.attempted_action_keys):
            raise ValueError("attempted action keys must be nonempty")
        for name in (
            "reacquisition_calls", "tool_calls", "model_calls", "input_tokens",
            "output_tokens", "observation_tokens",
        ):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        if self.reacquisition_calls > self.tool_calls:
            raise ValueError("reacquisition_calls cannot exceed tool_calls")
        if type(self.latency_seconds) not in (int, float) or self.latency_seconds < 0:
            raise ValueError("latency_seconds must be non-negative")
        if self.steps_to_resolution is not None and (
            type(self.steps_to_resolution) is not int or self.steps_to_resolution < 0
        ):
            raise ValueError("steps_to_resolution must be a non-negative integer or null")

    def score(self) -> dict[str, Any]:
        failed = set(self.known_failed_action_keys)
        repeated = [action for action in self.attempted_action_keys if action in failed]
        return {
            **asdict(self),
            "repeated_failed_operation_count": len(repeated),
            "repeated_failed_action_keys": repeated,
            "repeated_failure": bool(repeated),
            "total_tokens": self.input_tokens + self.output_tokens + self.observation_tokens,
        }


def summarize_continuations(trials: Sequence[ContinuationTrial]) -> dict[str, Any]:
    """Aggregate by method and memory condition while retaining the paired unit."""

    if not trials:
        raise ValueError("at least one continuation trial is required")
    scored = [trial.score() for trial in trials]
    identities = [
        (row["task_id"], row["checkpoint_id"], row["method_id"], row["condition_id"])
        for row in scored
    ]
    if len(identities) != len(set(identities)):
        raise ValueError("duplicate continuation trial identity")

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in scored:
        groups.setdefault((row["method_id"], row["condition_id"]), []).append(row)
    summaries = []
    for (method_id, condition_id), rows in sorted(groups.items()):
        resolved_steps = [row["steps_to_resolution"] for row in rows
                          if row["steps_to_resolution"] is not None]
        summaries.append({
            "method_id": method_id,
            "condition_id": condition_id,
            "trial_count": len(rows),
            "unique_task_count": len({row["task_id"] for row in rows}),
            "task_success_rate": fmean(row["task_success"] for row in rows),
            "repeated_failure_rate": fmean(row["repeated_failure"] for row in rows),
            "mean_repeated_failed_operations": fmean(
                row["repeated_failed_operation_count"] for row in rows
            ),
            "mean_reacquisition_calls": fmean(row["reacquisition_calls"] for row in rows),
            "mean_tool_calls": fmean(row["tool_calls"] for row in rows),
            "mean_model_calls": fmean(row["model_calls"] for row in rows),
            "mean_total_tokens": fmean(row["total_tokens"] for row in rows),
            "mean_latency_seconds": fmean(row["latency_seconds"] for row in rows),
            "mean_steps_to_resolution": (
                fmean(resolved_steps) if resolved_steps else None
            ),
            "resolved_steps_count": len(resolved_steps),
        })
    return {
        "schema_version": "failure_experience_continuation_summary_v1",
        "paired_unit": ["task_id", "checkpoint_id"],
        "trial_count": len(scored),
        "groups": summaries,
    }
