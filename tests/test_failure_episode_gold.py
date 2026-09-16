"""Regression coverage for first-class task-level failure episodes."""

from __future__ import annotations

from dataclasses import replace

import pytest

from tracegraph.benchmark.compression_audit.development_scoring import (
    canonical_answer,
    make_rubric,
    score_submission,
)
from tracegraph.benchmark.compression_audit.models import (
    FailureChainGold,
    FailureEpisodeEvidencePath,
    FailureEpisodeEvidencePolicy,
    FailureEpisodeGold,
    FailureEpisodeRepairStep,
    PrefixRecord,
    QueryRecord,
)


def _policy(alternatives: list[list[str]], relevant: list[str]
            ) -> FailureEpisodeEvidencePolicy:
    return FailureEpisodeEvidencePolicy(
        alternative_evidence_sets=tuple(tuple(values) for values in alternatives),
        relevant_evidence_ids=tuple(relevant),
        causal_paths=tuple(
            FailureEpisodeEvidencePath(
                evidence_ids=tuple(values),
                constraints=tuple(zip(values, values[1:])),
            )
            for values in alternatives
        ),
    )


def _fixture(case: str):
    prefix_id = f"real:swe_gym:{case}"

    def event_id(suffix: str) -> str:
        return f"{prefix_id}:{suffix}"

    if case == "f31d17c3c14ddd51":
        suffixes = [
            "E017", "E018", "E019", "E022", "E023", "E025", "E026",
            "E027", "E028", "E030", "E031", "E032", "E033", "E035", "E036",
        ]
        action_suffixes = {"E017", "E023", "E025", "E028", "E030", "E033", "E035"}
        decision_suffixes = {"E019", "E022", "E027", "E032"}
        repair_specs = [
            ("gradient", ["E019", "E022"], "E023", "E026", "intermediate_failure"),
            ("indent_1", ["E027"], "E028", "E031", "intermediate_failure"),
            ("indent_2", ["E032"], "E033", "E036", "resolved"),
        ]
        chain_alternatives = [
            ["E017", "E018", "E023", "E036"],
            ["E017", "E018", "E033", "E036"],
        ]
        local_subfailure = ["E025", "E026", "E033", "E036"]
        error = "Loss requires grad: False"
        recovery = (
            "Make the returned loss require gradients, then correct both indentation "
            "regressions before the successful rerun."
        )
    else:
        suffixes = [
            "E041", "E042", "E043", "E044", "E046", "E047", "E048",
            "E049", "E051", "E052", "E053", "E054", "E056", "E057",
        ]
        action_suffixes = {"E041", "E044", "E046", "E049", "E051", "E054", "E056"}
        decision_suffixes = {"E043", "E048", "E053"}
        repair_specs = [
            ("spatial_dims", ["E043"], "E044", "E047", "intermediate_failure"),
            ("channels", ["E048"], "E049", "E052", "intermediate_failure"),
            ("strides", ["E053"], "E054", "E057", "resolved"),
        ]
        chain_alternatives = [[
            "E041", "E042", "E044", "E047", "E049", "E052", "E054", "E057",
        ]]
        local_subfailure = ["E051", "E052", "E054", "E057"]
        error = "TypeError: missing a required argument: 'spatial_dims'"
        recovery = "Rename spatial_dims, add channels, then add strides before rerunning."

    events = []
    for index, suffix in enumerate(suffixes, 1):
        if suffix in action_suffixes:
            kind = "tool_call"
            content = {"name": "fixture_tool", "arguments": {"event": suffix}}
        else:
            kind = "decision" if suffix in decision_suffixes else "observation"
            content = f"fixture {suffix}"
        events.append({
            "event_id": event_id(suffix),
            "step_id": index,
            "kind": kind,
            "tool_name": "fixture_tool" if kind == "tool_call" else None,
            "content": content,
        })
    prefix = PrefixRecord(
        prefix_id=prefix_id,
        source_kind="swe_gym",
        source_ref={},
        split="dev",
        failure_family="fixture",
        task_domain="software",
        recoverability="R1",
        context_length="short",
        budget_tokens=4096,
        events=tuple(events),
        messages=({"role": "user", "content": "fixture"},),
        tool_schemas=(),
        environment_snapshot={},
    )
    steps = tuple(
        FailureEpisodeRepairStep(
            step_id=step_id,
            decision_event_ids=tuple(event_id(value) for value in decisions),
            action_event_id=event_id(action),
            result_event_ids=(event_id(result),),
            outcome=outcome,
            semantic_change=f"apply {step_id}",
        )
        for step_id, decisions, action, result, outcome in repair_specs
    )
    relevant = [event_id(value) for value in suffixes]
    recovery_ids = [
        value
        for step in steps
        for value in (step.action_event_id, *step.result_event_ids)
    ]
    chain_ids = [[event_id(value) for value in values] for values in chain_alternatives]
    interactive_ids = [event_id(suffixes[0]), event_id(suffixes[1]), *recovery_ids]
    episode = FailureEpisodeGold(
        anchor_event_id=event_id(suffixes[0]),
        initial_action_event_id=event_id(suffixes[0]),
        initial_result_event_ids=(event_id(suffixes[1]),),
        repair_steps=steps,
        resolution_event_ids=(steps[-1].result_event_ids[-1],),
        recovery_sequence=recovery,
        required_core_event_ids=tuple(chain_ids[0]),
        optional_support_event_ids=tuple(
            value for value in relevant if value not in set().union(*map(set, chain_ids))
        ),
        chain_policy=_policy(chain_ids, relevant),
        query_policies={
            "audit_recovery": _policy([recovery_ids], relevant),
            "interactive_reacquisition": _policy([interactive_ids], relevant),
        },
    )
    evidence = {
        "failed_action": (event_id(suffixes[0]),),
        "failed_arguments": (event_id(suffixes[0]),),
        "error_signature": (event_id(suffixes[1]),),
        "failure_cause": (event_id(suffixes[1]),),
        "diagnostic_evidence": (event_id(suffixes[1]),),
        "switch_decision": tuple(step.decision_event_ids[0] for step in steps),
        "replacement_action": tuple(step.action_event_id for step in steps),
        "replacement_arguments": tuple(step.action_event_id for step in steps),
        "resolution_evidence": episode.resolution_event_ids,
    }
    gold = FailureChainGold(
        prefix_id=prefix_id,
        failed_action="fixture_tool",
        failed_arguments={"event": suffixes[0]},
        error_signature=error,
        diagnostic_evidence="the anchored task-level failure",
        switch_decision=recovery,
        replacement_action="fixture_tool",
        replacement_arguments={"event": repair_specs[-1][2]},
        resolution_evidence="the final rerun succeeds",
        ordered_event_ids=tuple(chain_ids[0]),
        evidence_by_field=evidence,
        recoverability="R1",
        current_fact="done",
        current_event_ids=(episode.resolution_event_ids[-1],),
        failure_episode=episode,
    )
    return prefix, gold, [event_id(value) for value in local_subfailure]


def _query(prefix_id: str, query_type: str) -> QueryRecord:
    required = {
        "audit_recovery": (
            "switch_decision", "replacement_action", "replacement_arguments",
            "resolution_evidence",
        ),
        "audit_chain": (
            "failed_action", "failed_arguments", "failure_cause", "switch_decision",
            "replacement_action", "replacement_arguments", "resolution_evidence",
            "ordered_event_ids",
        ),
        "interactive_reacquisition": (
            "failed_action", "failed_arguments", "failure_cause", "replacement_action",
            "replacement_arguments", "resolution_evidence",
        ),
    }[query_type]
    return QueryRecord(
        query_id=f"{prefix_id}:{query_type}",
        prefix_id=prefix_id,
        track="audit_qa" if query_type != "interactive_reacquisition"
        else "interactive_reacquisition",
        query_type=query_type,
        text="Answer the anchored task-level episode.",
        allowed_tools=(),
        required_fields=required,
    )


def _judge(answer: dict, rubric: dict) -> dict:
    return {
        "facts": {key: "supported" for key in rubric["necessary_facts"]},
        "contradictions": [],
        "extractions": {},
    }


@pytest.mark.parametrize("case", ["f31d17c3c14ddd51", "9b9e94e63c2c9e54"])
def test_exposed_task_episode_accepts_full_chain_and_rejects_local_subfailure(case):
    prefix, gold, local_subfailure = _fixture(case)
    gold.failure_episode.validate_against_prefix(prefix)
    query = _query(prefix.prefix_id, "audit_chain")
    rubric = make_rubric(query, gold)
    assert rubric["source"] == "failure_episode_gold_v1"
    assert rubric["causal_mode"] == "partial_order"
    assert rubric["necessary_facts"]["repair_sequence"] == gold.failure_episode.recovery_sequence

    answer = canonical_answer(rubric)
    score = score_submission(
        answer,
        rubric,
        [event["event_id"] for event in prefix.events],
        judge=_judge(answer, rubric),
        judge_calibrated=True,
    )
    assert score["audit_pass"]

    local_answer = {**answer, "e": local_subfailure}
    local_score = score_submission(
        local_answer,
        rubric,
        [event["event_id"] for event in prefix.events],
        judge=_judge(local_answer, rubric),
        judge_calibrated=True,
    )
    assert not local_score["evidence_pass"]
    assert not local_score["audit_pass"]


@pytest.mark.parametrize("query_type", ["audit_recovery", "interactive_reacquisition"])
def test_episode_policy_applies_to_recovery_queries(query_type):
    prefix, gold, _ = _fixture("9b9e94e63c2c9e54")
    rubric = make_rubric(_query(prefix.prefix_id, query_type), gold)
    expected = gold.failure_episode.query_policies[query_type]
    assert rubric["source"] == "failure_episode_gold_v1"
    assert rubric["alternative_evidence_sets"] == [
        list(values) for values in expected.alternative_evidence_sets
    ]
    assert "repair_sequence" in rubric["necessary_facts"]


def test_episode_round_trip_and_legacy_hash_compatibility():
    prefix, gold, _ = _fixture("f31d17c3c14ddd51")
    encoded = gold.to_dict()
    decoded = FailureChainGold.from_dict(encoded)
    assert decoded == gold
    decoded.failure_episode.validate_against_prefix(prefix)

    legacy = replace(gold, failure_episode=None)
    legacy_hash = legacy.gold_hash
    legacy_encoded = legacy.to_dict()
    assert "failure_episode" not in legacy_encoded
    assert FailureChainGold.from_dict(legacy_encoded).gold_hash == legacy_hash


def test_episode_rejects_cyclic_policy():
    with pytest.raises(ValueError, match="causal cycle"):
        FailureEpisodeEvidencePath(
            evidence_ids=("a", "b"), constraints=(("a", "b"), ("b", "a"))
        )
