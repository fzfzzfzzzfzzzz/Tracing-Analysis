"""Concrete, development-safe plugins for bounded recursive memory improvement."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import re
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

from ..capture import estimate_tokens
from .compression_audit.development_adapters import observed_recovery_chains
from .compression_audit.io import canonical_json, load_jsonl, stable_digest
from .compression_audit.models import PrefixRecord
from .failure_experience import ContinuationTrial, EXPERIENCE_CONDITIONS
from .recursive_memory import (
    EvolutionPolicy,
    FailureExperience,
    MemoryEntry,
    MemoryRevision,
    MemoryUpdateCandidate,
    PostCommitGuard,
    ValidationEvidence,
    action_key,
    compare_revisions,
    evaluate_post_commit_guard,
    preview_candidate_revision,
)


_ACTION_ARGUMENT_KEYS = {
    "action", "command", "method", "mode", "op", "operation", "payload",
    "query", "request", "script", "sql", "statement", "subcommand",
}


def _content_text(value: Any) -> str:
    text = value if isinstance(value, str) else canonical_json(value)
    text = text.strip()
    return text or "unspecified"


def _arguments(event: Mapping[str, Any]) -> Mapping[str, Any]:
    content = event.get("content")
    if not isinstance(content, Mapping):
        return {}
    arguments = content.get("arguments")
    return arguments if isinstance(arguments, Mapping) else {}


def _applicability(failed: Mapping[str, Any], retry: Mapping[str, Any]) -> tuple[str, ...]:
    left, right = _arguments(failed), _arguments(retry)
    values = []
    for key in sorted(set(left) & set(right), key=str):
        if str(key).casefold() in _ACTION_ARGUMENT_KEYS or left[key] != right[key]:
            continue
        values.append(f"{key}={canonical_json(left[key])}")
    return tuple(values)


class DeterministicRecoveryExtractor:
    """Extract only publicly observed error→decision→same-target retry→success chains."""

    extractor_id = "deterministic_contiguous_same_target_recovery_v1"

    def extract(
        self, prefix: PrefixRecord, *, task_id: str, checkpoint_id: str
    ) -> Sequence[FailureExperience]:
        experiences = []
        for chain in observed_recovery_chains(prefix):
            failed, error, *middle, retry, result = chain
            decisions = middle
            error_content = error.get("content")
            if isinstance(error_content, Mapping) and error_content.get("error"):
                signature = _content_text(error_content["error"])
            else:
                signature = _content_text(error_content)
            decision_steps = tuple(
                f"decision-{index}:{_content_text(event.get('content'))}"
                for index, event in enumerate(decisions, 1)
            )
            recovery_steps = (*decision_steps, f"retry:{action_key(retry)}")
            payload = {
                "source_task_id": str(task_id),
                "source_checkpoint_id": str(checkpoint_id),
                "source_prefix_hash": prefix.prefix_hash,
                "source_split": prefix.split,
                "failure_signature": signature,
                "failed_action_key": action_key(failed),
                "failure_reason": signature,
                "diagnosis": " | ".join(
                    _content_text(event.get("content")) for event in decisions
                ),
                "recovery_steps": recovery_steps,
                "successful_action_key": action_key(retry),
                "applicability_conditions": _applicability(failed, retry),
                "evidence_event_ids": tuple(
                    str(event["event_id"]) for event in chain
                ),
                "extractor_id": self.extractor_id,
            }
            payload["estimated_tokens"] = max(
                1, estimate_tokens(canonical_json({**payload, "result": result.get("content")}))
            )
            experiences.append(FailureExperience.create(**payload))
        return tuple(sorted(experiences, key=lambda item: item.experience_id))


def experience_scope_key(experience: FailureExperience) -> str:
    """Identify the situation to which one recovery recommendation applies."""

    return stable_digest({
        "failed_action_key": experience.failed_action_key,
        "failure_signature": experience.failure_signature.casefold().strip(),
        "applicability_conditions": sorted(experience.applicability_conditions),
    })


def experience_advice_key(experience: FailureExperience) -> str:
    return stable_digest({
        "scope": experience_scope_key(experience),
        "recovery_steps": experience.recovery_steps,
        "successful_action_key": experience.successful_action_key,
    })


class ConservativeAddOnlyGenerator:
    """Add one unambiguous experience per unseen scope; never replace automatically."""

    generator_id = "conservative_add_only_failure_experience_v1"

    def __init__(self, *, max_candidates: int = 8) -> None:
        if type(max_candidates) is not int or max_candidates <= 0:
            raise ValueError("max_candidates must be a positive integer")
        self.max_candidates = max_candidates

    def propose(
        self,
        experiences: Sequence[FailureExperience],
        parent: MemoryRevision,
        *,
        round_id: str,
    ) -> Sequence[MemoryUpdateCandidate]:
        existing_scopes = {
            experience_scope_key(entry.experience) for entry in parent.entries
        }
        groups: dict[str, list[FailureExperience]] = {}
        for experience in experiences:
            groups.setdefault(experience_scope_key(experience), []).append(experience)
        candidates = []
        for scope, group in sorted(groups.items()):
            if scope in existing_scopes:
                continue
            advice = {experience_advice_key(item) for item in group}
            if len(advice) != 1:
                # Conflicting recoveries require adjudication or a replace proposal.
                continue
            experience = min(group, key=lambda item: item.experience_id)
            entry = MemoryEntry.from_experience(experience, round_id)
            candidates.append(MemoryUpdateCandidate.create(
                round_id=round_id,
                parent_revision_id=parent.revision_id,
                operation="add",
                source_experience_ids=tuple(sorted(
                    item.experience_id for item in group
                )),
                generator_id=self.generator_id,
                rationale="new unambiguous failure-recovery scope",
                proposed_entry=entry,
            ))
            if len(candidates) >= self.max_candidates:
                break
        return tuple(candidates)


@dataclass(frozen=True, slots=True)
class RenderedRevisionMemory:
    revision_id: str
    records: tuple[Mapping[str, Any], ...]
    selected_entry_ids: tuple[str, ...]
    omitted_entry_ids: tuple[str, ...]
    token_count: int
    budget_tokens: int
    send_eligible: bool
    safety_reasons: tuple[str, ...]
    schema_version: str = "rendered_recursive_memory_v1"

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["records"] = [dict(item) for item in self.records]
        return value


class DeterministicMemoryRenderer:
    """Render whole experience entries under budget; never slice evidence fields."""

    renderer_id = "deterministic_failure_experience_renderer_v1"

    def __init__(self, token_counter: Callable[[Any], int] = estimate_tokens) -> None:
        self.count = token_counter

    @staticmethod
    def _record(entry: MemoryEntry) -> dict[str, Any]:
        experience = entry.experience
        return {
            "record_id": entry.entry_id,
            "kind": "failure_experience",
            "content": {
                "failure_signature": experience.failure_signature,
                "failed_action_key": experience.failed_action_key,
                "failure_reason": experience.failure_reason,
                "diagnosis": experience.diagnosis,
                "recovery_steps": list(experience.recovery_steps),
                "successful_action_key": experience.successful_action_key,
                "applicability_conditions": list(experience.applicability_conditions),
                "source_evidence_event_ids": list(experience.evidence_event_ids),
            },
        }

    def render(self, revision: MemoryRevision, *, query: str, budget_tokens: int
               ) -> RenderedRevisionMemory:
        if type(budget_tokens) is not int or budget_tokens <= 0:
            raise ValueError("budget_tokens must be a positive integer")
        terms = set(re.findall(r"[\w.-]+", query.casefold()))
        rows = [(entry, self._record(entry)) for entry in revision.entries]

        def score(item: tuple[MemoryEntry, dict[str, Any]]) -> tuple[int, str]:
            entry, record = item
            content_terms = set(re.findall(
                r"[\w.-]+", canonical_json(record["content"]).casefold()
            ))
            return len(terms & content_terms), entry.entry_id

        rows.sort(key=lambda item: (-score(item)[0], score(item)[1]))
        records: list[dict[str, Any]] = []
        selected = []
        omitted = []
        for entry, record in rows:
            if self.count([*records, record]) <= budget_tokens:
                records.append(record)
                selected.append(entry.entry_id)
            else:
                omitted.append(entry.entry_id)
        token_count = self.count(records)
        eligible = bool(records) or not revision.entries
        return RenderedRevisionMemory(
            revision_id=revision.revision_id,
            records=tuple(records),
            selected_entry_ids=tuple(selected),
            omitted_entry_ids=tuple(omitted),
            token_count=token_count,
            budget_tokens=budget_tokens,
            send_eligible=eligible,
            safety_reasons=() if eligible else ("no_complete_experience_fits_budget",),
        )


@dataclass(frozen=True, slots=True)
class ReplayObservation:
    source_split: str
    baseline_trials: tuple[ContinuationTrial, ...]
    evolved_trials: tuple[ContinuationTrial, ...]
    replay_reproduced: bool
    provenance_pass: bool
    contradiction_free: bool
    safety_pass: bool
    hidden_evaluation_observed: bool = False

    def __post_init__(self) -> None:
        if self.source_split not in {"dev", "validation", "test"}:
            raise ValueError("unsupported replay source split")
        if not self.baseline_trials or not self.evolved_trials:
            raise ValueError("replay observation needs baseline and evolved trials")
        for name in (
            "replay_reproduced", "provenance_pass", "contradiction_free",
            "safety_pass", "hidden_evaluation_observed",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")


class ReplayObservationProvider(Protocol):
    def observe(
        self, candidate: MemoryUpdateCandidate, parent: MemoryRevision
    ) -> ReplayObservation: ...


@dataclass(frozen=True, slots=True)
class MiniReplayPair:
    baseline_run: Path
    evolved_run: Path
    task_id: str
    checkpoint_id: str
    source_split: str
    known_failed_action_keys: tuple[str, ...]
    baseline_method_id: str = "baseline"
    evolved_method_id: str = "evolved"
    condition_id: str = "structured_failure_episode"
    replay_reproduced: bool = True
    provenance_pass: bool = True
    contradiction_free: bool = True
    safety_pass: bool = True
    hidden_evaluation_observed: bool = False
    checkpoint_model_calls: int | None = None
    baseline_prior_provider_rows: int | None = None
    evolved_prior_provider_rows: int | None = None

    def __post_init__(self) -> None:
        if self.source_split not in {"dev", "validation", "test"}:
            raise ValueError("unsupported mini replay split")
        if self.condition_id not in EXPERIENCE_CONDITIONS:
            raise ValueError("unsupported mini replay condition")
        if not self.task_id or not self.checkpoint_id or not self.known_failed_action_keys:
            raise ValueError("mini replay identity and known failed actions are required")
        if len(self.known_failed_action_keys) != len(set(self.known_failed_action_keys)):
            raise ValueError("mini replay failed action keys must be unique")
        for name in (
            "replay_reproduced", "provenance_pass", "contradiction_free",
            "safety_pass", "hidden_evaluation_observed",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be boolean")
        for name in (
            "checkpoint_model_calls", "baseline_prior_provider_rows",
            "evolved_prior_provider_rows",
        ):
            value = getattr(self, name)
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError(f"{name} must be a non-negative integer")
        for path in (self.baseline_run, self.evolved_run):
            if not path.is_dir():
                raise ValueError(f"mini replay run is unavailable: {path}")


class MiniArtifactReplayProvider:
    """Load already completed, isolated mini branches; never start a live run."""

    def __init__(self, pairs: Mapping[str, MiniReplayPair]) -> None:
        self.pairs = dict(pairs)
        if not self.pairs or any(not key for key in self.pairs):
            raise ValueError("candidate-indexed mini replay pairs are required")

    def observe(
        self, candidate: MemoryUpdateCandidate, parent: MemoryRevision
    ) -> ReplayObservation:
        if candidate.candidate_id not in self.pairs:
            raise ValueError("no completed mini replay pair for candidate")
        pair = self.pairs[candidate.candidate_id]
        baseline_identity = json.loads(
            (pair.baseline_run / "identity.json").read_text(encoding="utf-8")
        )
        evolved_identity = json.loads(
            (pair.evolved_run / "identity.json").read_text(encoding="utf-8")
        )
        if baseline_identity.get("task_id") != pair.task_id:
            raise ValueError("baseline mini replay task identity differs")
        for key in (
            "task_id", "plan_hash", "model_id", "method", "budget",
            "revision_memory_budget",
        ):
            if baseline_identity.get(key) != evolved_identity.get(key):
                raise ValueError(f"mini replay branches differ on {key}")
        budget = baseline_identity.get("budget")
        memory_budget = baseline_identity.get("revision_memory_budget")
        if (type(budget) is not int or type(memory_budget) is not int
                or not 0 < memory_budget < budget):
            raise ValueError("mini replay branches need the same reserved memory budget")
        if baseline_identity.get("revision_id") != parent.revision_id:
            raise ValueError("baseline mini replay does not use the parent revision")
        preview = preview_candidate_revision(parent, candidate)
        if evolved_identity.get("revision_id") != preview.revision_id:
            raise ValueError("evolved mini replay does not use the candidate preview")
        if not all(identity.get("memory_source_sha256") for identity in (
            baseline_identity, evolved_identity
        )):
            raise ValueError("mini replay branches lack content-addressed memory sources")
        restore_sources = tuple(
            identity.get("restore_source")
            for identity in (baseline_identity, evolved_identity)
        )
        if not all(isinstance(item, str) and item for item in restore_sources):
            raise ValueError("mini replay branches must restore a checkpoint")
        checkpoints = tuple(Path(item).resolve() for item in restore_sources)
        if checkpoints[0] != checkpoints[1] or not checkpoints[0].is_file():
            raise ValueError("mini replay branches do not share an available checkpoint")
        checkpoint = json.loads(checkpoints[0].read_text(encoding="utf-8"))
        declared_hash = checkpoint.get("checkpoint_hash")
        checkpoint_body = {key: value for key, value in checkpoint.items()
                           if key != "checkpoint_hash"}
        if (declared_hash != stable_digest(checkpoint_body)
                or declared_hash != pair.checkpoint_id):
            raise ValueError("mini replay checkpoint identity differs")
        if (checkpoint.get("development_only") is not True
                or checkpoint.get("independent_validation") is not False):
            raise ValueError("mini replay checkpoint lacks development provenance")
        common = {
            "task_id": pair.task_id,
            "checkpoint_id": pair.checkpoint_id,
            "condition_id": pair.condition_id,
            "known_failed_action_keys": pair.known_failed_action_keys,
            "checkpoint_model_calls": pair.checkpoint_model_calls,
        }
        baseline = continuation_trial_from_mini_run(
            pair.baseline_run,
            method_id=pair.baseline_method_id,
            prior_provider_rows=pair.baseline_prior_provider_rows,
            **common,
        )
        evolved = continuation_trial_from_mini_run(
            pair.evolved_run,
            method_id=pair.evolved_method_id,
            prior_provider_rows=pair.evolved_prior_provider_rows,
            **common,
        )
        return ReplayObservation(
            source_split=pair.source_split,
            baseline_trials=(baseline,),
            evolved_trials=(evolved,),
            replay_reproduced=pair.replay_reproduced,
            provenance_pass=pair.provenance_pass,
            contradiction_free=pair.contradiction_free,
            safety_pass=pair.safety_pass,
            hidden_evaluation_observed=pair.hidden_evaluation_observed,
        )


class CheckpointReplayValidator:
    """Convert paired checkpoint replay observations into update evidence."""

    validator_id = "paired_checkpoint_replay_validator_v1"

    def __init__(
        self,
        provider: ReplayObservationProvider,
        *,
        guard: PostCommitGuard | None = None,
    ) -> None:
        self.provider = provider
        self.guard = guard or PostCommitGuard()

    def validate(
        self, candidate: MemoryUpdateCandidate, parent: MemoryRevision
    ) -> ValidationEvidence:
        if candidate.parent_revision_id != parent.revision_id:
            raise ValueError("replay candidate/parent mismatch")
        observation = self.provider.observe(candidate, parent)
        comparison = compare_revisions(
            observation.baseline_trials,
            observation.evolved_trials,
            round_id=candidate.round_id,
            baseline_revision_id=parent.revision_id,
            evolved_revision_id="candidate:" + candidate.candidate_id,
        )
        guard = evaluate_post_commit_guard(comparison, self.guard)
        task_ids = tuple(sorted({
            trial.task_id for trial in observation.baseline_trials
        }))
        metrics = {
            key: float(comparison[key])
            for key in (
                "success_rate_delta", "repeated_failure_rate_delta",
                "mean_reacquisition_delta", "mean_tool_call_delta",
                "mean_token_delta", "mean_latency_delta",
            )
        }
        metrics["paired_replay_count"] = float(comparison["pair_count"])
        return ValidationEvidence(
            candidate_id=candidate.candidate_id,
            validator_id=self.validator_id,
            source_split=observation.source_split,
            provenance_pass=observation.provenance_pass,
            replay_pass=observation.replay_reproduced,
            regression_pass=guard["pass"],
            contradiction_free=observation.contradiction_free,
            safety_pass=observation.safety_pass,
            hidden_evaluation_observed=observation.hidden_evaluation_observed,
            evaluated_task_ids=task_ids,
            metrics=metrics,
        )


def continuation_trial_from_mini_run(
    run: Path,
    *,
    task_id: str,
    checkpoint_id: str,
    method_id: str,
    condition_id: str,
    known_failed_action_keys: Sequence[str],
    checkpoint_model_calls: int | None = None,
    prior_provider_rows: int | None = None,
    reacquisition_detector: Callable[[Mapping[str, Any]], bool] | None = None,
) -> ContinuationTrial:
    """Normalize one completed ``server_eval.mini`` branch into a continuation trial."""

    if condition_id not in EXPERIENCE_CONDITIONS:
        raise ValueError("unsupported continuation condition")
    identity = json.loads((run / "identity.json").read_text(encoding="utf-8"))
    result = json.loads((run / "task_result.json").read_text(encoding="utf-8"))
    if identity.get("task_id") != task_id:
        raise ValueError("mini run task identity differs")
    if result.get("development_only") is not True or result.get("independent_validation") is not False:
        raise ValueError("mini run lacks development provenance")
    if type(result.get("task_success")) is not bool:
        raise ValueError("mini run task_success must be boolean")

    restore_source = identity.get("restore_source")
    if restore_source and (checkpoint_model_calls is None or prior_provider_rows is None):
        checkpoint_path = Path(restore_source)
        if not checkpoint_path.is_file():
            raise ValueError("restore checkpoint is unavailable; pass explicit prior counts")
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        checkpoint_model_calls = (
            checkpoint["n_calls"] if checkpoint_model_calls is None else checkpoint_model_calls
        )
        source_ledger = checkpoint_path.parent / "provider_ledger.jsonl"
        prior_provider_rows = (
            len(load_jsonl(source_ledger)) if prior_provider_rows is None and source_ledger.exists()
            else (prior_provider_rows or 0)
        )
    checkpoint_model_calls = checkpoint_model_calls or 0
    prior_provider_rows = prior_provider_rows or 0

    attempts = load_jsonl(run / "tool_attempts.jsonl") if (run / "tool_attempts.jsonl").exists() else []
    results = load_jsonl(run / "tool_results.jsonl") if (run / "tool_results.jsonl").exists() else []
    if len(attempts) != len(results) or any(
        attempt != row.get("attempt") for attempt, row in zip(attempts, results)
    ):
        raise ValueError("mini run has unresolved or mismatched tool attempts")
    attempted_action_keys = tuple(action_key({
        "kind": "tool_call",
        "tool_name": "bash",
        "content": str(attempt.get("command", "")),
    }) for attempt in attempts)

    ledger = load_jsonl(run / "provider_ledger.jsonl") if (run / "provider_ledger.jsonl").exists() else []
    if prior_provider_rows > len(ledger):
        raise ValueError("prior provider row count exceeds the completed run")
    new_rows = ledger[prior_provider_rows:]
    if any(not row.get("valid_usage") for row in new_rows):
        raise ValueError("mini run contains invalid provider usage")
    input_tokens = sum(int(row.get("prompt_tokens") or 0) for row in new_rows)
    output_tokens = sum(int(row.get("completion_tokens") or 0) for row in new_rows)
    latency = sum(float(row.get("latency_seconds") or 0) for row in new_rows)
    contexts = load_jsonl(run / "memory_contexts.jsonl") if (run / "memory_contexts.jsonl").exists() else []
    observation_tokens = sum(int(
        row.get("bundle", {}).get("retrieval_usage", {}).get("observation_tokens") or 0
    ) for row in contexts)
    model_calls = int(result["model_calls"]) - checkpoint_model_calls
    if model_calls < 0:
        raise ValueError("mini run model calls precede its checkpoint")
    return ContinuationTrial(
        task_id=task_id,
        checkpoint_id=checkpoint_id,
        method_id=method_id,
        condition_id=condition_id,
        task_success=result["task_success"],
        attempted_action_keys=attempted_action_keys,
        known_failed_action_keys=tuple(map(str, known_failed_action_keys)),
        reacquisition_calls=sum(
            bool(reacquisition_detector(attempt)) for attempt in attempts
        ) if reacquisition_detector is not None else 0,
        tool_calls=len(attempts),
        model_calls=model_calls,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        observation_tokens=observation_tokens,
        latency_seconds=latency,
        steps_to_resolution=model_calls if result["task_success"] else None,
    )


def policy_compatible_plugins(policy: EvolutionPolicy) -> dict[str, Any]:
    """Expose a small manifest for experiment snapshots and reviews."""

    return {
        "schema_version": "recursive_memory_plugin_manifest_v1",
        "extractor_id": DeterministicRecoveryExtractor.extractor_id,
        "generator_id": ConservativeAddOnlyGenerator.generator_id,
        "validator_id": CheckpointReplayValidator.validator_id,
        "renderer_id": DeterministicMemoryRenderer.renderer_id,
        "policy_id": policy.policy_id,
        "model_calls": 0,
        "hidden_gold_observed": False,
        "supported_update_operations": ["add"],
    }
