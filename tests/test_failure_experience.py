from __future__ import annotations

from tracegraph.benchmark.compression_audit.development_adapters import (
    make_development_adapter,
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
from tracegraph.benchmark.failure_experience import (
    ContinuationTrial,
    OracleGateThresholds,
    diagnose_preservation,
    evaluate_oracle_gate,
    summarize_continuations,
)
from tracegraph.benchmark.recursive_memory import experience_from_failure_episode


def _fixture():
    events = (
        {"event_id": "e1", "step_id": 1, "kind": "tool_call", "call_id": "c1",
         "tool_name": "run", "content": {"arguments": {"path": "x", "action": "old"}}},
        {"event_id": "e2", "step_id": 2, "kind": "error", "call_id": "c1",
         "tool_name": "run", "content": {"error": "gradient broke"}},
        {"event_id": "e3", "step_id": 3, "kind": "decision",
         "content": "switch to the safe implementation"},
        {"event_id": "e4", "step_id": 4, "kind": "tool_call", "call_id": "c2",
         "tool_name": "run", "content": {"arguments": {"path": "x", "action": "safe"}}},
        {"event_id": "e5", "step_id": 5, "kind": "observation", "call_id": "c2",
         "tool_name": "run", "content": {"success": True, "status": "passed"}},
    )
    prefix = PrefixRecord(
        prefix_id="fixture", source_kind="synthetic", source_ref={}, split="dev",
        failure_family="fixture", task_domain="software", recoverability="R1",
        context_length="short", budget_tokens=4096, events=events, messages=(),
        tool_schemas=(), environment_snapshot={},
    )
    core = ("e1", "e2", "e4", "e5")
    constraints = (("e1", "e2"), ("e2", "e4"), ("e4", "e5"))
    policy = FailureEpisodeEvidencePolicy(
        alternative_evidence_sets=(core,),
        relevant_evidence_ids=("e1", "e2", "e3", "e4", "e5"),
        causal_paths=(FailureEpisodeEvidencePath(core, constraints),),
    )
    episode = FailureEpisodeGold(
        anchor_event_id="e1", initial_action_event_id="e1",
        initial_result_event_ids=("e2",),
        repair_steps=(FailureEpisodeRepairStep(
            step_id="repair", decision_event_ids=("e3",), action_event_id="e4",
            result_event_ids=("e5",), outcome="resolved", semantic_change="use safe",
        ),),
        resolution_event_ids=("e5",), recovery_sequence="old failed; safe passed",
        required_core_event_ids=core, optional_support_event_ids=("e3",),
        chain_policy=policy,
    )
    gold = FailureChainGold(
        prefix_id="fixture", failed_action="run", failed_arguments={"action": "old"},
        error_signature="gradient broke", diagnostic_evidence="gradient broke",
        switch_decision="use safe", replacement_action="run",
        replacement_arguments={"action": "safe"}, resolution_evidence="passed",
        ordered_event_ids=core,
        evidence_by_field={"failed_action": ("e1",), "error_signature": ("e2",),
                           "replacement_action": ("e4",), "resolution_evidence": ("e5",)},
        recoverability="R1", current_fact="passed", current_event_ids=("e5",),
        failure_episode=episode,
    )
    query = QueryRecord(
        query_id="fixture:audit_chain", prefix_id="fixture", track="audit_qa",
        query_type="audit_chain", text="Why did the gradient attempt fail?",
        allowed_tools=(), required_fields=("ordered_event_ids",),
    )
    return prefix, gold, query, constraints


def test_layered_diagnostic_attributes_the_first_failed_stage():
    prefix, gold, _, constraints = _fixture()
    construction_failure = diagnose_preservation(
        prefix, gold, "audit_chain", constructed_episode_event_ids=("e1", "e2"),
        constructed_edges=(), visible_event_ids=("e1", "e2", "e4", "e5"),
        answer_event_ids=("e1", "e2", "e4", "e5"), semantic_pass=True,
        protocol_valid=True,
    )
    assert construction_failure["first_failure_stage"] == "construction"
    assert construction_failure["construction"]["best_path_coverage"] == 0.5

    answer_failure = diagnose_preservation(
        prefix, gold, "audit_chain",
        constructed_episode_event_ids=("e1", "e2", "e4", "e5"),
        constructed_edges=constraints, visible_event_ids=("e1", "e2", "e4", "e5"),
        answer_event_ids=("e1", "e4", "e2", "e5"), semantic_pass=True,
        protocol_valid=True,
    )
    assert answer_failure["first_failure_stage"] == "answer_evidence"
    assert not answer_failure["answer"]["causal_order_pass"]

    passed = diagnose_preservation(
        prefix, gold, "audit_chain",
        constructed_episode_event_ids=("e1", "e2", "e4", "e5"),
        constructed_edges=constraints, visible_event_ids=("e1", "e2", "e4", "e5"),
        answer_event_ids=("e1", "e2", "e4", "e5"), semantic_pass=True,
        protocol_valid=True, context_tokens=300, context_budget_tokens=512,
    )
    assert passed["first_failure_stage"] == "pass"
    assert passed["selection"]["path_sufficient"]


def test_oracle_gate_requires_explicit_thresholds_and_complete_rows():
    prefix, gold, _, constraints = _fixture()
    row = diagnose_preservation(
        prefix, gold, "audit_chain",
        constructed_episode_event_ids=("e1", "e2", "e4", "e5"),
        constructed_edges=constraints, visible_event_ids=("e1", "e2", "e4", "e5"),
        answer_event_ids=("e1", "e2", "e4", "e5"), semantic_pass=True,
        protocol_valid=True,
    )
    thresholds = OracleGateThresholds(
        min_cases=2, min_context_path_pass_rate=1, min_answer_path_pass_rate=1,
        min_semantic_pass_rate=1, min_protocol_valid_rate=1,
        min_send_eligible_rate=1,
    )
    assert not evaluate_oracle_gate([row], thresholds)["pass"]
    report = evaluate_oracle_gate([row, row], thresholds)
    assert report["pass"] and report["rates"]["semantic_pass_rate"] == 1


def test_continuation_summary_exposes_repeat_and_reacquisition_costs():
    trials = [
        ContinuationTrial(
            task_id="task-1", checkpoint_id="cp-1", method_id="tracegraph",
            condition_id="structured_failure_episode", task_success=True,
            attempted_action_keys=("safe",), known_failed_action_keys=("old",),
            reacquisition_calls=0, tool_calls=1, model_calls=1, input_tokens=100,
            output_tokens=20, observation_tokens=10, latency_seconds=2,
            steps_to_resolution=1,
        ),
        ContinuationTrial(
            task_id="task-1", checkpoint_id="cp-1", method_id="baseline",
            condition_id="success_only", task_success=False,
            attempted_action_keys=("old", "safe"), known_failed_action_keys=("old",),
            reacquisition_calls=1, tool_calls=2, model_calls=2, input_tokens=200,
            output_tokens=40, observation_tokens=20, latency_seconds=4,
        ),
    ]
    report = summarize_continuations(trials)
    by_method = {row["method_id"]: row for row in report["groups"]}
    assert by_method["baseline"]["repeated_failure_rate"] == 1
    assert by_method["tracegraph"]["repeated_failure_rate"] == 0
    assert by_method["tracegraph"]["mean_total_tokens"] == 130


def test_structural_bm25_controls_use_public_units_without_gold():
    prefix, _, query, _ = _fixture()
    window = make_development_adapter("bm25_pair_window_archive", ingest_budget=1)
    window_state = window.ingest(prefix, 4096)
    window_bundle = window.materialize(window_state, query, 4096)
    assert window_state.ingestion_usage["hidden_gold_observed"] is False
    assert window_state.ingestion_usage["retrieval_unit"] == "event_pair_window"
    assert {"e1", "e2", "e3"} <= set(window_bundle.visible_event_ids)

    episode = make_development_adapter("bm25_episode_archive", ingest_budget=1)
    episode_state = episode.ingest(prefix, 4096)
    episode_bundle = episode.materialize(episode_state, query, 4096)
    assert episode_state.ingestion_usage["hidden_gold_observed"] is False
    assert episode_state.ingestion_usage["retrieval_unit"] == "inferred_failure_episode"
    assert {"e1", "e2", "e3", "e4", "e5"} <= set(episode_bundle.visible_event_ids)


def test_annotated_episode_adapts_to_the_runtime_experience_contract():
    prefix, gold, _, _ = _fixture()
    experience = experience_from_failure_episode(
        prefix, gold, source_task_id="task", source_checkpoint_id="checkpoint",
        extractor_id="gold-fixture-adapter", applicability_conditions=("same path",),
    )
    assert experience.source_split == "dev"
    assert experience.recovery_steps == ("use safe",)
    assert experience.failed_action_key != experience.successful_action_key
    assert set(experience.evidence_event_ids) == {"e1", "e2", "e3", "e4", "e5"}
