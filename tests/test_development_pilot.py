"""Pilot integration and adversarial checks. No test contacts a model provider."""

from __future__ import annotations

import copy
import json
import os
from collections import Counter
from dataclasses import replace
from pathlib import Path

import pytest

from tracegraph.benchmark.compression_audit.build import verify_file_manifest, write_file_manifest
from tracegraph.benchmark.compression_audit.dataset import generate_controlled_dataset
from tracegraph.benchmark.compression_audit.development_adapters import (
    METHODS, close_pairs, make_development_adapter, public_graph,
)
from tracegraph.benchmark.compression_audit.development_experiment import (
    answer_request, challenge_examples, evidence_role_guidance, load_pilot_config,
    load_prepared, prepare_pilot, select_population, write_json, write_rows, load_counter,
)
from tracegraph.benchmark.compression_audit.development_results import judge_gate
from tracegraph.benchmark.compression_audit.development_rescore import rescore_pilot
from tracegraph.benchmark.compression_audit.development_runner import (
    evaluate_judge_example, run_pilot,
)
from tracegraph.benchmark.compression_audit.development_scoring import (
    canonical_answer, extract_strict, judge_request, make_rubric, minimal_evidence_sets,
    parse_judge, provider_response, rule_judge_fixture, score_submission,
)
from tracegraph.benchmark.compression_audit.io import canonical_json, load_jsonl, stable_digest
from tracegraph.capture import estimate_tokens
from tracegraph.context_engine.policy import GraphConstrainedPolicy

CONFIG = Path("configs/compression_audit_v0_2_pilot.json")


@pytest.fixture(scope="module")
def data():
    prefixes, gold, queries = generate_controlled_dataset()
    return prefixes, {g.prefix_id: g for g in gold}, {(q.prefix_id, q.query_type): q for q in queries}


@pytest.fixture(scope="module")
def prepared(tmp_path_factory, data):
    root = tmp_path_factory.mktemp("pilot")
    dataset = root / "data"
    (dataset / "public").mkdir(parents=True)
    (dataset / "private").mkdir()
    prefixes, gold, queries = data
    write_rows(dataset / "public/prefixes.jsonl", [p.to_dict() for p in prefixes])
    write_rows(dataset / "public/queries.jsonl", [q.to_dict() for q in queries.values()])
    write_rows(dataset / "private/all_gold.jsonl", [g.to_dict() for g in gold.values()])
    write_json(dataset / "manifest.json", {"benchmark_id": "compression_audit_v1"})
    write_file_manifest(dataset)
    package = root / "prepared"
    summary = prepare_pilot(CONFIG, dataset, package)
    return dataset, package, summary


@pytest.fixture
def chain(data):
    prefixes, gold, queries = data
    prefix = prefixes[0]
    query = queries[(prefix.prefix_id, "audit_chain")]
    rubric = make_rubric(query, gold[prefix.prefix_id])
    return prefix, query, rubric


def test_population_is_prefix_disjoint_balanced_and_frozen(data):
    prefixes, _, _ = data
    first = select_population(prefixes, 20260912)
    assert first == select_population(list(reversed(prefixes)), 20260912)
    assert len(first["calibration"]) == 8 and len(first["main"]) == 24
    assert not set(first["calibration"]) & set(first["main"])
    assert set(first["diagnostic"]) <= set(first["main"])
    pmap = {p.prefix_id: p for p in prefixes}
    assert Counter(pmap[p].recoverability for p in first["main"]) == dict.fromkeys(
        ["R0", "R1", "R2", "R3"], 6)
    assert all(pmap[p].split == "dev" for ids in first.values() for p in ids)
    with pytest.raises(ValueError, match="insufficient"):
        select_population(prefixes[:2], 1)


def test_four_fields_score_and_adversarial_examples(chain):
    prefix, _, rubric = chain
    wire = canonical_answer(rubric)
    visible = [e["event_id"] for e in prefix.events]
    judge = rule_judge_fixture(wire, rubric)
    good = score_submission(wire, rubric, visible, judge=judge, judge_calibrated=True)
    assert good["audit_pass"] and good["strict_chain_recovered"]
    assert good["missing_strict_label_fields"] == []
    assert good["judge_facts_all_supported"]
    for change in (
        {"a": "A plausible but wrong explanation."},
        {"e": wire["e"] + [prefix.events[-1]["event_id"]]},
        {"e": wire["e"] + ["unseen"]},
        {"e": wire["e"][:-1]},
        {"e": list(reversed(wire["e"]))}, {"t": "current"}, {"s": True},
        {"a": wire["a"] + "\n" + rubric["contradictory_facts"][0]},
    ):
        assert not score_submission({**wire, **change}, rubric, visible,
                                    judge=judge, judge_calibrated=True)["audit_pass"]
    assert not score_submission(wire, rubric, visible, judge=judge,
                                judge_calibrated=False)["audit_pass"]
    assert not score_submission(wire, rubric, visible, judge=judge,
                                judge_calibrated=True, unsafe_attempts=1)["audit_pass"]
    assert not score_submission(wire, rubric, visible, judge=judge,
                                judge_calibrated=True, executed_side_effects=1)["audit_pass"]
    assert not score_submission({"a": "x"}, rubric, visible)["structured_output_valid"]
    unlabelled = score_submission({**wire, "a": "A plausible explanation."}, rubric, visible,
                                  judge=judge, judge_calibrated=True)
    assert set(unlabelled["missing_strict_label_fields"]) == set(rubric["strict_values"])
    assert "answer_strict_value_protocol_failure" in unlabelled["failure_labels"]


def test_alternative_evidence_and_partial_order(chain):
    prefix, _, rubric = chain
    rubric = copy.deepcopy(rubric)
    answer = canonical_answer(rubric)
    alternative = answer["e"][1:]
    rubric["alternative_evidence_sets"].append(alternative)
    rubric["causal_constraints"] = list(map(list, zip(alternative, alternative[1:])))
    answer["e"] = alternative
    score = score_submission(answer, rubric, [e["event_id"] for e in prefix.events],
        judge=rule_judge_fixture(answer, rubric), judge_calibrated=True)
    assert score["audit_pass"]
    assert score["causal_constraint_rate"] == 1
    assert score["strict_chain_recovered"] is False


def test_failure_cause_requires_error_record_for_exact_signature(data):
    prefixes, gold, queries = data
    prefix = prefixes[0]
    query = queries[(prefix.prefix_id, "audit_failure_cause")]
    item = gold[prefix.prefix_id]
    rubric = make_rubric(query, item)
    error_id = item.ordered_event_ids[1]
    assert item.evidence_by_field["error_signature"] == (error_id,)
    decision_id = next(event_id for event_id in item.evidence_by_field["diagnostic_evidence"]
                       if event_id != error_id)
    assert rubric["alternative_evidence_sets"] == [[error_id]]
    assert {error_id, decision_id} <= set(rubric["relevant_evidence_ids"])
    answer = canonical_answer(rubric)
    score = score_submission(answer, rubric, [e["event_id"] for e in prefix.events],
        judge=rule_judge_fixture(answer, rubric), judge_calibrated=True)
    assert score["evidence_pass"] and score["audit_pass"]

    decision_only = {**answer, "e": [decision_id]}
    score = score_submission(decision_only, rubric,
        [event_id for event_id in [e["event_id"] for e in prefix.events]
         if event_id != error_id],
        judge=rule_judge_fixture(decision_only, rubric), judge_calibrated=True)
    assert not score["necessary_evidence_present"]
    assert score["failure_stages"] == ["context_selection"]
    assert score["attribution"] == "context_selection"

    legacy_evidence = dict(item.evidence_by_field)
    legacy_evidence.pop("error_signature")
    legacy_gold = replace(item, evidence_by_field=legacy_evidence)
    assert minimal_evidence_sets(query, legacy_gold) == [[error_id]]


def test_interactive_uses_minimal_role_evidence_and_chain_remains_strict(data):
    prefixes, gold, queries = data
    prefix = prefixes[0]
    visible = [e["event_id"] for e in prefix.events]
    interactive = queries[(prefix.prefix_id, "interactive_reacquisition")]
    rubric = make_rubric(interactive, gold[prefix.prefix_id])
    alternatives = rubric["alternative_evidence_sets"]
    assert alternatives == minimal_evidence_sets(interactive, gold[prefix.prefix_id])
    assert len(alternatives) == 1
    assert len(alternatives[0]) == 4
    assert gold[prefix.prefix_id].ordered_event_ids[1] in alternatives[0]
    for evidence in alternatives:
        answer = {**canonical_answer(rubric), "e": evidence}
        score = score_submission(answer, rubric, visible,
            judge=rule_judge_fixture(answer, rubric), judge_calibrated=True)
        assert score["audit_pass"] and score["answer_citation_pass"]

    chain_query = queries[(prefix.prefix_id, "audit_chain")]
    chain_rubric = make_rubric(chain_query, gold[prefix.prefix_id])
    chain_answer = canonical_answer(chain_rubric)
    chain_answer["e"] = chain_answer["e"][:-1]
    score = score_submission(chain_answer, chain_rubric, visible,
        judge=rule_judge_fixture(chain_answer, chain_rubric), judge_calibrated=True)
    assert not score["audit_pass"]
    assert score["failure_stages"] == ["answer_citation"]
    assert score["attribution"] == "answer_citation"


def test_failure_stage_distinguishes_context_selection_from_answer_citation(chain):
    prefix, _, rubric = chain
    answer = canonical_answer(rubric)
    all_visible = [e["event_id"] for e in prefix.events]
    missing_context = [event_id for event_id in all_visible
                       if event_id != rubric["strict_chain"][-1]]
    context_score = score_submission(answer, rubric, missing_context,
        judge=rule_judge_fixture(answer, rubric), judge_calibrated=True)
    assert "context_selection" in context_score["failure_stages"]
    assert "answer_citation" not in context_score["failure_stages"]

    incomplete = {**answer, "e": answer["e"][:-1]}
    citation_score = score_submission(incomplete, rubric, all_visible,
        judge=rule_judge_fixture(incomplete, rubric), judge_calibrated=True)
    assert citation_score["necessary_evidence_present"]
    assert citation_score["failure_stages"] == ["answer_citation"]


def test_answer_format_guidance_is_query_specific(data):
    prefixes, gold, queries = data
    prefix = prefixes[0]
    current = queries[(prefix.prefix_id, "distractor_current")]
    failure = queries[(prefix.prefix_id, "audit_failure_cause")]
    chain = queries[(prefix.prefix_id, "audit_chain")]
    interactive = queries[(prefix.prefix_id, "interactive_reacquisition")]
    current_request = answer_request(current, [])
    current_system = current_request["messages"][0]["content"]
    failure_request = answer_request(failure, [])
    failure_system = failure_request["messages"][0]["content"]
    chain_system = answer_request(chain, [])["messages"][0]["content"]
    interactive_request = answer_request(interactive, [])
    interactive_system = interactive_request["messages"][0]["content"]
    assert "required_exact_labels is empty" in current_system
    assert "example_error" not in current_system
    assert "error_signature: \"example_error\"" in failure_system
    assert "example_failed_tool" not in failure_system
    payload = json.loads(failure_request["messages"][1]["content"])
    assert payload["required_semantic_labels"] == ["diagnostic_evidence"]
    assert "generic error category" in failure_system
    assert "visible error record whose error key supplies error_signature" in failure_system
    assert "later diagnostic decision" in failure_system
    assert "never put a record ID there" in failure_system
    assert "Do not collapse a result and a later decision" in chain_system
    assert "failed tool-call record" in evidence_role_guidance(chain)
    assert "Set t to historical" in chain_system
    assert "requested current fact" not in interactive_system
    assert "permitted read-only reacquisition tool" in interactive_system
    assert "Set t to historical" in interactive_system
    assert "Set t to current" in current_system
    encoded_contract = chain_system + interactive_system
    assert prefix.prefix_id not in encoded_contract
    assert gold[prefix.prefix_id].failed_action not in encoded_contract
    current_pattern = current_request["response_format"]["json_schema"]["schema"][
        "properties"]["a"]["pattern"]
    failure_pattern = failure_request["response_format"]["json_schema"]["schema"][
        "properties"]["a"]["pattern"]
    assert current_pattern == r"^current_fact: [^\n]+$"
    assert failure_pattern == (
        "^error_signature: [^\\n]+\\ndiagnostic_evidence: [^\\n]+$")

    server_request = answer_request(failure, [], server_field_transport=True)
    server_schema = server_request["response_format"]["json_schema"]
    assert server_schema["name"] == "compression_audit_v02_server_fields"
    fields = server_schema["schema"]["properties"]["answer_fields"]
    assert fields["required"] == ["error_signature", "diagnostic_evidence"]
    assert "matching tool_name, never the operation argument" in (
        server_request["messages"][0]["content"])


def test_extraction_requires_actual_unicode_span():
    text = '说明 tool-name {"x": 3}'
    judge = {"extractions": {"failed_action": {"start": 3, "end": 12, "quote": "tool-name"},
        "failed_arguments": {"start": 13, "end": len(text), "quote": '{"x": 3}'}}}
    assert extract_strict(text, "failed_action", judge) == ("tool-name", "verified_span")
    assert extract_strict(text, "failed_arguments", judge) == ({"x": 3}, "verified_span")
    judge["extractions"]["failed_action"]["quote"] = "invented"
    assert extract_strict(text, "failed_action", judge)[1] == "pending_review"
    legacy = {"extractions": {"failed_action": {
        "start": 3, "end": 12, "text": "tool-name"}}}
    assert extract_strict(text, "failed_action", legacy) == (
        "tool-name", "verified_legacy_span")
    legacy["extractions"]["failed_action"]["text"] = "invented"
    assert extract_strict(text, "failed_action", legacy)[1] == "pending_review"
    assert extract_strict("failed_action: foo", "failed_action", None)[0] == "foo"
    assert extract_strict('failed_arguments: {bad}', "failed_arguments", None)[1] == "pending_review"


def test_judge_is_blind_and_malformed_verdict_cannot_score(chain):
    _, _, rubric = chain
    answer = canonical_answer(rubric)
    request = judge_request(answer, rubric, [])
    encoded = json.dumps(request)
    assert "method_id" not in encoded and "condition_id" not in encoded
    prompt = request["messages"][0]["content"]
    assert "exactly those necessary_facts keys" in prompt
    assert "record_id values are evidence IDs, never fact IDs" in prompt
    assert "exact substring copied from answer.a" in prompt
    assert "unless it also occurs in answer.a" in prompt
    assert "different or unrelated cause" in prompt
    assert "correct cause appearing in records cannot rescue" in prompt
    response_format = request["response_format"]
    assert response_format["type"] == "json_schema"
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]["facts"]["properties"]) == set(
        rubric["necessary_facts"])
    extraction_schema = schema["properties"]["extractions"]
    assert extraction_schema["additionalProperties"] is False
    assert extraction_schema["properties"] == {}
    assert "uniqueItems" not in json.dumps(response_format)
    payload = json.loads(request["messages"][1]["content"])
    assert payload["extract_fields"] == []
    assert "claims_to_check" not in payload
    assert "contradictory_facts" not in payload
    verdict = rule_judge_fixture(answer, rubric)
    assert parse_judge(provider_response(verdict), rubric, answer["a"]) == verdict
    field = sorted(rubric["strict_values"])[0]
    quote = next(line for line in answer["a"].splitlines()
                 if line.startswith(field + ": ")).split(": ", 1)[1]
    start = answer["a"].index(quote)
    legacy = {**verdict, "extractions": {field: {
        "start": start, "end": start + len(quote), "text": quote}}}
    normalized = parse_judge(provider_response(legacy), rubric, answer["a"])
    assert normalized["extractions"][field] == {
        "start": start, "end": start + len(quote), "quote": quote}
    legacy["extractions"][field].update(start=0, end=1)
    repaired = parse_judge(provider_response(legacy), rubric, answer["a"])
    assert repaired["extractions"][field] == {
        "start": start, "end": start + len(quote), "quote": quote}
    ambiguous_answer = quote + " / " + quote
    with pytest.raises(ValueError, match="extraction_span_mismatch"):
        parse_judge(provider_response(legacy), rubric, ambiguous_answer)
    for bad in ({}, {**verdict, "facts": []}, {**verdict, "contradictions": [5]}):
        with pytest.raises(ValueError):
            parse_judge(provider_response(bad), rubric, answer["a"])
    invented = {**verdict, "contradictions": ["not present in the answer"]}
    with pytest.raises(ValueError, match="exact_answer_substring"):
        parse_judge(provider_response(invented), rubric, answer["a"])
    score = score_submission(answer, rubric, answer["e"], judge=invented,
                             judge_calibrated=True)
    assert score["hard_pass"] and not score["audit_pass"]
    assert score["judge_auxiliary_pass"] is None
    assert score["judge_protocol_valid"] is False
    assert score["judge_protocol_errors"] == ["contradiction_not_exact_answer_substring"]
    assert "judge_protocol_failure" in score["failure_labels"]
    assert score["failure_stages"] == ["judge_protocol"]
    assert "semantic_failure" not in score["failure_labels"]
    response = provider_response(verdict)
    response["choices"][0]["finish_reason"] = "length"
    with pytest.raises(ValueError):
        parse_judge(response, rubric, answer["a"])


def test_server_native_tool_transport_preserves_typed_answer_contract(data):
    _, _, queries = data
    failure = next(
        q for q in queries.values() if q.query_type == "audit_failure_cause"
    )
    request = answer_request(
        failure, [], server_field_transport=True, server_tool_transport=True
    )
    assert "response_format" not in request
    assert request["tool_choice"] == "auto"
    assert request["parallel_tool_calls"] is False
    assert request["tools"][0]["function"]["name"] == "submit_compression_audit_v02"
    assert "must call submit_compression_audit_v02 exactly once" in (
        request["messages"][0]["content"]
    )
    assert "Do not write the final answer in ordinary assistant content" in (
        request["messages"][0]["content"]
    )


def test_real_policy_called_and_no_query_or_gold_leak(chain, monkeypatch):
    prefix, query, _ = chain
    calls = []
    original = GraphConstrainedPolicy.materialize

    def spy(self, snapshot, incoming, protocol):
        calls.append(incoming)
        return original(self, snapshot, incoming, protocol)

    monkeypatch.setattr(GraphConstrainedPolicy, "materialize", spy)
    adapter = make_development_adapter("tracegraph_0_4")
    state = adapter.ingest(prefix, 1536)
    scrambled = replace(prefix, events=tuple({**e, "causal_role": "wrong"} for e in prefix.events))
    assert adapter.ingest(scrambled, 1536).state_hash == state.state_hash
    first = adapter.materialize(state, query, 1536)
    second = adapter.materialize(state, replace(query, track="distractor"), 1536)
    assert calls == [query.text, query.text]
    assert first.records == second.records
    assert state.ingestion_usage["policy_class"].endswith("GraphConstrainedPolicy")
    assert not state.ingestion_usage["hidden_gold_observed"]
    graph, _ = public_graph(prefix)
    assert all(e.edge_type.value in {
        "produces", "failed_with", "provides_input", "retried_by", "leads_to",
    } for e in graph.edges.values())
    assert any(e.edge_type.value == "retried_by" for e in graph.edges.values())
    assert not any(e.edge_type.value == "resolved_by" for e in graph.edges.values())


@pytest.mark.parametrize("budget", [512, 768])
def test_tracegraph_low_budget_selects_complete_observed_recovery_unit(chain, budget):
    prefix, query, rubric = chain
    adapter = make_development_adapter("tracegraph_0_4", ingest_budget=budget // 2)
    state = adapter.ingest(prefix, budget)
    bundle = adapter.materialize(state, query, budget)
    assert bundle.retrieval_usage["send_eligible"]
    assert set(rubric["strict_chain"]) <= set(bundle.visible_event_ids)
    assert bundle.token_count <= budget
    assert not state.ingestion_usage["future_query_observed"]
    assert not state.ingestion_usage["hidden_gold_observed"]
    assert not any(event_id.endswith(":E023") for event_id in bundle.visible_event_ids)


def test_tracegraph_current_only_query_does_not_carry_recovery_chain(data):
    prefixes, _, queries = data
    prefix = prefixes[0]
    query = queries[(prefix.prefix_id, "distractor_current")]
    adapter = make_development_adapter("tracegraph_0_4", ingest_budget=384)
    state = adapter.ingest(prefix, 768)
    bundle = adapter.materialize(state, query, 768)
    assert bundle.retrieval_usage["send_eligible"]
    current_id = next(event["event_id"] for event in prefix.events
                      if event.get("causal_role") == "current_fact")
    assert current_id in bundle.visible_event_ids
    assert not any(event_id.endswith(suffix) for event_id in bundle.visible_event_ids
                   for suffix in (":E004", ":E005", ":E006", ":E007"))


def test_public_graph_does_not_link_different_target_as_recovery(chain):
    prefix, _, _ = chain
    events = [dict(event) for event in prefix.events]
    retry_index = next(index for index, event in enumerate(events)
                       if event["event_id"].endswith(":E006"))
    retry = events[retry_index]
    retry_content = dict(retry["content"])
    retry_arguments = dict(retry_content["arguments"])
    retry_arguments["entity"] = "a-different-target"
    retry_content["arguments"] = retry_arguments
    events[retry_index] = {**retry, "content": retry_content}
    graph, _ = public_graph(replace(prefix, events=tuple(events)))
    assert not any(edge.metadata.get("basis") == "contiguous_same_target_recovery"
                   for edge in graph.edges.values())


def test_method_budgets_protocol_pairs_and_mutation_rejection(chain):
    prefix, query, _ = chain
    for name in METHODS:
        adapter = make_development_adapter(name)
        state = adapter.ingest(prefix, 1536)
        bundle = adapter.materialize(state, query, 1536)
        assert bundle.token_count <= bundle.budget_tokens
        assert close_pairs(prefix, set(bundle.visible_event_ids)) == set(bundle.visible_event_ids)
        if name != "full_history":
            assert bundle.budget_tokens == 1536
        state.ingestion_usage["tampered"] = True
        with pytest.raises(ValueError, match="modified"):
            adapter.materialize(state, query, 1536)
    with pytest.raises(ValueError, match="unknown"):
        make_development_adapter("pretend-ours")


def test_policy_counter_and_budget_failure_are_not_clipped(chain):
    prefix, query, _ = chain
    def counter(value):
        return estimate_tokens(value) * 20
    adapter = make_development_adapter("tracegraph_0_4", token_counter=counter)
    state = adapter.ingest(prefix, 1536)
    bundle = adapter.materialize(state, query, 1536)
    assert not bundle.retrieval_usage["send_eligible"]
    assert "history_budget_exceeded" in bundle.retrieval_usage["safety_reasons"]
    assert bundle.retrieval_usage["attempted_records"]


def test_policy_counts_frozen_messages_as_json_values():
    from types import MappingProxyType

    values = []
    policy = GraphConstrainedPolicy(token_counter=lambda value: values.append(value) or 1)
    policy._count_tokens([MappingProxyType({"role": "user", "content": "hello"})])
    assert values == [[{"role": "user", "content": "hello"}]]
    assert type(values[0][0]) is dict


def test_preparation_inventory_bounds_and_isolation(prepared, tmp_path):
    dataset, package, summary = prepared
    config, trials, rubrics, examples = load_prepared(package)
    assert summary["phase_counts"] == {"calibration": 40, "main": 288,
                                       "diagnostic": 24, "interactive": 32}
    assert summary["request_upper_bound"] == 1298
    assert summary["cost_upper_bound_cny"] == pytest.approx(75.2300032)
    assert summary["provider_requests"] == 0 and not summary["live_ready"]
    assert summary["canonical_rubric_self_checks"] == len(rubrics)
    assert len(examples) == 80 and len({e["example_id"] for e in examples}) == 80
    assert Counter(e["split"] for e in examples) == {"tuning": 40, "holdout": 40}
    assert not ({e["prefix_id"] for e in examples if e["split"] == "tuning"}
                & {e["prefix_id"] for e in examples if e["split"] == "holdout"})
    assert not config["authorization"]["authorized_by_user"]
    for t in trials:
        assert t["request_template_sha256"] == stable_digest(t["request_template"])
        assert t["rubric_hash"] == rubrics[t["query_id"]]["rubric_hash"]
        assert "necessary_facts" not in canonical_json(t["request_template"])
        payload = json.loads(t["request_template"]["messages"][-1]["content"])
        if t["query_type"] == "audit_failure_cause":
            assert payload["required_exact_labels"] == ["error_signature"]
            assert payload["required_semantic_labels"] == ["diagnostic_evidence"]
    with pytest.raises(FileExistsError):
        prepare_pilot(CONFIG, dataset, package)
    with pytest.raises(ValueError, match="outside"):
        prepare_pilot(CONFIG, dataset, dataset / "child")
    altered = json.loads(CONFIG.read_text())
    altered["model"] = "other"
    write_json(tmp_path / "bad.json", altered)
    with pytest.raises(ValueError, match="model"):
        load_pilot_config(tmp_path / "bad.json")


def test_complete_offline_loop_and_resume(prepared, tmp_path):
    dataset, package, _ = prepared
    output = tmp_path / "offline"
    result = run_pilot(package, dataset, output)
    assert result["episode_count"] == 384 and result["provider_requests"] == 0
    assert result["judge_gate"]["pass"]
    assert result["interpretation"] == "offline_wiring_only"
    assert result["stop_reason"] is None
    assert result["total_cost_cny"] == 0
    episodes = load_jsonl(output / "episodes.jsonl")
    assert all(set(e["answer"]) == {"a", "e", "t", "s"} for e in episodes if e["answer"])
    verify_file_manifest(output)
    rescored = rescore_pilot(dataset, output, tmp_path / "rescored")
    assert rescored == result
    verify_file_manifest(tmp_path / "rescored")
    with pytest.raises(FileExistsError):
        rescore_pilot(dataset, output, tmp_path / "rescored")
    with pytest.raises(ValueError, match="immutable"):
        rescore_pilot(dataset, output, output / "score")
    resumed = run_pilot(package, dataset, output, resume=True)
    assert resumed == result
    verify_file_manifest(output)
    with pytest.raises(FileExistsError):
        run_pilot(package, dataset, output)


def test_live_blocks_before_files_or_provider_access(prepared, tmp_path, monkeypatch):
    dataset, package, _ = prepared
    monkeypatch.setenv("TRACEGRAPH_DISABLE_LIVE", "1")
    with pytest.raises(RuntimeError, match="disabled"):
        run_pilot(package, dataset, tmp_path / "live", mode="live")
    assert not (tmp_path / "live").exists()
    with pytest.raises(ValueError, match="offline"):
        run_pilot(package, dataset, tmp_path / "fake", transport=lambda: None)


def test_scoring_cli_dispatches_saved_pilot(prepared, tmp_path):
    from tracegraph.cli import main

    dataset, package, _ = prepared
    output = tmp_path / "run"
    run_pilot(package, dataset, output)
    assert main(["benchmark-score", "--dataset", str(dataset), "--run", str(output),
                 "--output", str(tmp_path / "score")]) == 0


def test_judge_gate_requires_full_holdout_and_no_critical_false_positive():
    gates = load_pilot_config(CONFIG)["gates"]
    rows = [{"split": "holdout", "expected_pass": True, "predicted_pass": True,
             "judge_valid": True, "critical_false_positive": False} for _ in range(40)]
    assert judge_gate(rows, gates)["pass"]
    rows[-1]["critical_false_positive"] = True
    assert not judge_gate(rows, gates)["pass"]
    assert not judge_gate(rows[:39], gates)["pass"]


def test_challenges_cover_intended_failures(data):
    prefixes, gold, queries = data
    pop = select_population(prefixes, 20260912)
    examples = challenge_examples(pop["calibration"], queries, gold,
                                  {p.prefix_id: p for p in prefixes})
    for example in examples:
        answer, rubric = example["answer"], example["rubric"]
        score = score_submission(answer, rubric, [r["record_id"] for r in example["records"]],
            judge=rule_judge_fixture(answer, rubric), judge_calibrated=True)
        assert score["audit_pass"] == example["expected_pass"], example["case"]


def test_deterministically_blocked_contradiction_is_not_a_critical_false_positive(data):
    prefixes, gold, queries = data
    pop = select_population(prefixes, 20260912)
    example = next(e for e in challenge_examples(pop["calibration"], queries, gold,
        {p.prefix_id: p for p in prefixes}) if e["case"] == "contradiction")
    verdict = {"facts": {key: "supported" for key in example["rubric"]["necessary_facts"]},
               "contradictions": [], "extractions": {}}

    class OmissiveJudge:
        def call(self, *args, **kwargs):
            return provider_response(verdict)

    result = evaluate_judge_example(example, OmissiveJudge())
    assert not result["predicted_pass"]
    assert not result["critical_false_positive"]


def test_pinned_tokenizer_matches_both_context_and_full_request(prepared, tmp_path):
    source = Path(os.environ.get("TRACEGRAPH_FROZEN_ROOT", Path.cwd()))
    config_path = source / "configs/compression_audit_v0_2_pilot_qwen38_flash.json"
    config = load_pilot_config(config_path)
    if not (source / config["tokenizer"]["path"]).exists():
        pytest.skip("optional pinned tokenizer is not downloaded")
    count, provenance = load_counter(config, source)
    assert provenance["exact"]
    dataset, _, _ = prepared
    package = tmp_path / "exact"
    summary = prepare_pilot(config_path, dataset, package, workspace=source)
    assert summary["tokenizer"]["exact"]
    assert summary["blockers"] == ["fresh_live_authorization_required"]
    _, trials, _, _ = load_prepared(package)
    oracle = {t["query_id"]: t for t in trials if t["method_id"] == "oracle"}
    controls = [t for t in trials if t["method_id"] == "irrelevant"]
    assert len(controls) == 8
    for control in controls:
        reference = oracle[control["query_id"]]
        assert count(control["artifact"]["records"]) == count(reference["artifact"]["records"])
        assert count(control["request_template"]) == count(reference["request_template"])
        assert control["artifact"]["retrieval_usage"]["exact_size_match"]
    wrong = copy.deepcopy(config)
    wrong["tokenizer"]["model"] = "other"
    with pytest.raises(ValueError, match="identity"):
        load_counter(wrong, source)
    wrong = copy.deepcopy(config)
    wrong["tokenizer"]["verification_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="evidence changed"):
        load_counter(wrong, source)
