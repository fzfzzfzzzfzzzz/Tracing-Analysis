"""Paid path tests with local transports only; real provider access stays disabled."""

from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from test_development_pilot import CONFIG, data as data_fixture, prepared as prepared_fixture
from tracegraph.benchmark.compression_audit.development_experiment import load_pilot_config, load_prepared
from tracegraph.benchmark.compression_audit.development_ledger import ProviderLedger, validate_live
from tracegraph.benchmark.compression_audit.development_protocol import SUBMISSION_TOOL_NAME
from tracegraph.benchmark.compression_audit.development_runner import (
    public_evidence_roles_supported, run_episode, run_pilot,
)
from tracegraph.benchmark.compression_audit.development_scoring import (
    canonical_answer, provider_response, rule_judge_fixture,
)
from tracegraph.benchmark.compression_audit.io import load_jsonl
from tracegraph.capture import estimate_tokens

data = data_fixture
prepared = prepared_fixture


def response(wire):
    value = provider_response(wire)
    value.update(id="local-request", model="qwen3.8-flash",
                 usage={"prompt_tokens": 10, "completion_tokens": 10})
    return value


def tool_response(name, topic="historical failure"):
    return {"id": "local-tool", "model": "qwen3.8-flash",
        "usage": {"prompt_tokens": 10, "completion_tokens": 10},
        "choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant",
            "tool_calls": [{"id": name, "type": "function", "function": {
                "name": name, "arguments": json.dumps({"topic": topic})}}]}}]}


def is_judge_request(body):
    response_format = body.get("response_format", {})
    return (response_format.get("type") == "json_schema"
            and response_format.get("json_schema", {}).get("name")
            == "compression_audit_judge_v02")


def ledger_for(tmp_path, payloads, config=None):
    values = iter(payloads)

    def transport(*args, **kwargs):
        item = next(values)
        if isinstance(item, Exception):
            raise item
        return item, 0.01

    return ProviderLedger(tmp_path, config or load_pilot_config(CONFIG), workspace=Path.cwd(),
                          completed_jobs=set(), transport=transport)


def test_ledger_keeps_request_snapshots_prices_and_counts(tmp_path):
    ledger = ledger_for(tmp_path, [response({}), response({})])
    template = {"messages": [{"role": "user", "content": "one"}]}
    ledger.call(template, job_id="job", kind="answer")
    template["messages"][0]["content"] = "two"
    ledger.call(template, job_id="job", kind="format_repair")
    assert ledger.rows[0]["request"]["messages"][0]["content"] == "one"
    assert ledger.rows[1]["kind"] == "format_repair"
    assert all(r["valid_usage"] and not r["provider_retry"] for r in ledger.rows)
    assert ledger.rows[0]["cost_cny"] == pytest.approx(0.000035)
    resumed = ProviderLedger(tmp_path, ledger.config, workspace=Path.cwd(),
                              completed_jobs={"job"}, transport=lambda *a, **k: None)
    assert len(resumed.rows) == 2
    with pytest.raises(RuntimeError, match="interrupted"):
        ProviderLedger(tmp_path, ledger.config, workspace=Path.cwd(),
                        completed_jobs=set(), transport=lambda *a, **k: None)


@pytest.mark.parametrize("kind", ["missing_usage", "network", "bound_violation"])
def test_unknown_request_outcomes_never_retry(tmp_path, kind):
    payload = response({})
    if kind == "missing_usage":
        payload.pop("usage")
    elif kind == "network":
        payload = TimeoutError()
    else:
        payload["usage"]["completion_tokens"] = 9000
    ledger = ledger_for(tmp_path, [payload])
    with pytest.raises(RuntimeError, match="uncertain"):
        ledger.call({"messages": [{"role": "user", "content": "test"}]}, job_id="x", kind="answer")
    assert len(load_jsonl(tmp_path / "provider_attempts.jsonl")) == 1
    assert not ledger.rows[0]["valid_usage"]
    with pytest.raises(RuntimeError, match="uncertain"):
        ProviderLedger(tmp_path, ledger.config, workspace=Path.cwd(),
                        completed_jobs={"x"}, transport=lambda *a, **k: None)


def test_ledger_budget_checks_precede_attempts(tmp_path):
    config = load_pilot_config(CONFIG)
    config["cost_cap_cny"] = 0.000001
    ledger = ledger_for(tmp_path, [], config)
    with pytest.raises(RuntimeError, match="cost cap"):
        ledger.call({"messages": []}, job_id="x", kind="answer")
    config["cost_cap_cny"] = 100
    config["request_count_hard_max"] = 0
    with pytest.raises(RuntimeError, match="request limit"):
        ledger.call({"messages": []}, job_id="x", kind="answer")
    config["request_count_hard_max"] = 1298
    config["request_input_tokens_hard_max"] = 1
    with pytest.raises(RuntimeError, match="input bound"):
        ledger.call({"messages": []}, job_id="x", kind="answer")
    assert not (tmp_path / "provider_attempts.jsonl").exists()


def test_audit_repair_billed_separately(prepared, data, tmp_path):
    _, package, _ = prepared
    _, trials, rubrics, _ = load_prepared(package)
    trial = trials[0]
    prefixes, gold, queries = data
    prefix = next(p for p in prefixes if p.prefix_id == trial["prefix_id"])
    rubric = rubrics[trial["query_id"]]
    wire = canonical_answer(rubric)
    ledger = ledger_for(tmp_path, [response({"a": "missing fields"}), response(wire),
                                  response(rule_judge_fixture(wire, rubric))])
    result = run_episode(trial, rubric, prefix, queries[(prefix.prefix_id, trial["query_type"])],
                          gold[prefix.prefix_id], ledger, judge_calibrated=True,
                          token_counter=estimate_tokens)
    assert result["score"]["audit_pass"]
    assert not result["first_format_valid"] and result["format_repair_used"]
    assert [r["kind"] for r in ledger.rows] == ["answer", "format_repair", "judge"]
    assert len(result["model_calls"]) == 2
    assert result["deployment_cost_cny"] == pytest.approx(0.00007)


def test_valid_json_missing_exact_labels_gets_one_contract_repair(prepared, data, tmp_path):
    _, package, _ = prepared
    _, trials, rubrics, _ = load_prepared(package)
    trial = next(t for t in trials if t["query_type"] == "audit_failure_cause")
    prefixes, gold, queries = data
    prefix = next(p for p in prefixes if p.prefix_id == trial["prefix_id"])
    rubric = rubrics[trial["query_id"]]
    incomplete = {"a": "The explanation is plausible but has no labelled exact value.",
                  "e": rubric["alternative_evidence_sets"][0],
                  "t": rubric["expected_scope"], "s": False}
    repaired = canonical_answer(rubric)
    ledger = ledger_for(tmp_path, [response(incomplete), response(repaired),
                                   response(rule_judge_fixture(repaired, rubric))])
    result = run_episode(trial, rubric, prefix,
        queries[(prefix.prefix_id, trial["query_type"])], gold[prefix.prefix_id], ledger,
        judge_calibrated=True, token_counter=estimate_tokens)
    assert result["status"] == "complete" and result["score"]["audit_pass"]
    assert not result["first_format_valid"] and result["format_repair_used"]
    assert [row["kind"] for row in ledger.rows] == ["answer", "format_repair", "judge"]


def test_valid_json_missing_semantic_lines_gets_one_contract_repair(prepared, data, tmp_path):
    _, package, _ = prepared
    _, trials, rubrics, _ = load_prepared(package)
    trial = next(t for t in trials if t["query_type"] == "audit_chain")
    prefixes, gold, queries = data
    prefix = next(p for p in prefixes if p.prefix_id == trial["prefix_id"])
    rubric = rubrics[trial["query_id"]]
    complete = canonical_answer(rubric)
    strict_labels = set(rubric["strict_values"])
    incomplete = {**complete, "a": "\n".join(line for line in complete["a"].splitlines()
        if line.split(":", 1)[0] in strict_labels)}
    ledger = ledger_for(tmp_path, [response(incomplete), response(complete),
                                   response(rule_judge_fixture(complete, rubric))])
    result = run_episode(trial, rubric, prefix,
        queries[(prefix.prefix_id, trial["query_type"])], gold[prefix.prefix_id], ledger,
        judge_calibrated=True, token_counter=estimate_tokens)
    assert result["status"] == "complete" and result["score"]["audit_pass"]
    assert not result["first_format_valid"] and result["format_repair_used"]
    assert "semantic=" in result["parse_error"]
    assert [row["kind"] for row in ledger.rows] == ["answer", "format_repair", "judge"]


def test_interactive_reacquisition_is_four_turns_and_fixture_only(prepared, data, tmp_path):
    _, package, _ = prepared
    _, trials, rubrics, _ = load_prepared(package)
    prefixes, gold, queries = data
    trial = next(t for t in trials if t["phase"] == "interactive"
                 and t["recoverability"] == "R2"
                 and not public_evidence_roles_supported(
                     queries[(t["prefix_id"], t["query_type"])], t["artifact"]["records"]))
    prefix = next(p for p in prefixes if p.prefix_id == trial["prefix_id"])
    rubric = rubrics[trial["query_id"]]
    wire = canonical_answer(rubric)
    ledger = ledger_for(tmp_path, [tool_response(name) for name in (
        "inspect_environment", "replay_in_sandbox", "read_audit_log")]
        + [response(wire), response(rule_judge_fixture(wire, rubric))])
    result = run_episode(trial, rubric, prefix, queries[(prefix.prefix_id, trial["query_type"])],
                          gold[prefix.prefix_id], ledger, judge_calibrated=True,
                          token_counter=estimate_tokens)
    assert result["score"]["audit_pass"]
    assert len(result["model_calls"]) == 4 and len(result["tool_calls"]) == 3
    assert result["reacquisition_submission_forced"]
    first_choice = ledger.rows[0]["request"].get("tool_choice")
    assert first_choice == {
        "type": "function", "function": {"name": "inspect_environment"}}
    assert result["reacquisition_initial_tool_forced"]
    assert [row["request"].get("tool_choice") for row in ledger.rows[1:3]] == [
        {"type": "function", "function": {"name": "replay_in_sandbox"}},
        {"type": "function", "function": {"name": "read_audit_log"}},
    ]
    assert result["reacquisition_sequence_tools_forced"] == 2
    assert ledger.rows[3]["request"]["tool_choice"] == {
        "type": "function", "function": {"name": SUBMISSION_TOOL_NAME}}
    assert "Re-check e against the requested roles" in ledger.rows[3]["request"]["messages"][-1]["content"]
    assert all(not t["content"].get("external_command_executed") for t in result["tool_calls"])
    assert result["tool_observation_tokens"] > 0


def test_interactive_with_complete_initial_context_submits_without_tools(
        prepared, data, tmp_path):
    _, package, _ = prepared
    _, trials, rubrics, _ = load_prepared(package)
    trial = next(t for t in trials if t["phase"] == "interactive"
                 and t["method_id"] == "full_history")
    prefixes, gold, queries = data
    prefix = next(p for p in prefixes if p.prefix_id == trial["prefix_id"])
    rubric = rubrics[trial["query_id"]]
    wire = canonical_answer(rubric)
    ledger = ledger_for(tmp_path, [response(wire), response(rule_judge_fixture(wire, rubric))])
    result = run_episode(trial, rubric, prefix, queries[(prefix.prefix_id, trial["query_type"])],
                         gold[prefix.prefix_id], ledger, judge_calibrated=True,
                         token_counter=estimate_tokens)
    assert result["score"]["audit_pass"]
    assert result["initial_necessary_evidence_present"]
    assert result["initial_public_evidence_roles_supported"]
    assert result["initial_supported_submission_forced"]
    assert not result["reacquisition_opportunity"]
    assert not result["reacquisition_submission_forced"]
    assert result["tool_calls"] == []
    assert [row["kind"] for row in ledger.rows] == ["answer", "judge"]
    assert ledger.rows[0]["request"]["tool_choice"] == {
        "type": "function", "function": {"name": SUBMISSION_TOOL_NAME}}
    without_error = [record for record in trial["artifact"]["records"]
                     if record.get("kind") != "error"]
    assert not public_evidence_roles_supported(
        queries[(trial["prefix_id"], trial["query_type"])], without_error)


def test_interactive_missing_public_roles_forces_safe_reacquisition_then_submit(
        prepared, data, tmp_path):
    _, package, _ = prepared
    _, trials, rubrics, _ = load_prepared(package)
    prefixes, gold, queries = data
    candidates = []
    for trial in trials:
        if trial["phase"] != "interactive" or trial["recoverability"] != "R1":
            continue
        query = queries[(trial["prefix_id"], trial["query_type"])]
        if not public_evidence_roles_supported(query, trial["artifact"]["records"]):
            candidates.append((trial, query))
    assert candidates
    trial, query = candidates[0]
    prefix = next(p for p in prefixes if p.prefix_id == trial["prefix_id"])
    rubric = rubrics[trial["query_id"]]
    wire = canonical_answer(rubric)
    tool = query.allowed_tools[0]
    ledger = ledger_for(tmp_path, [tool_response(tool), response(wire),
                                   response(rule_judge_fixture(wire, rubric))])
    result = run_episode(trial, rubric, prefix, query, gold[prefix.prefix_id], ledger,
                         judge_calibrated=True, token_counter=estimate_tokens)
    assert result["score"]["audit_pass"]
    assert not result["initial_public_evidence_roles_supported"]
    assert result["reacquisition_initial_tool_forced"]
    assert result["reacquisition_submission_forced"]
    assert len(result["tool_calls"]) == 1
    assert ledger.rows[0]["request"]["tool_choice"] == {
        "type": "function", "function": {"name": tool}}
    assert ledger.rows[1]["request"]["tool_choice"] == {
        "type": "function", "function": {"name": SUBMISSION_TOOL_NAME}}
    reminder = ledger.rows[1]["request"]["messages"][-1]["content"]
    assert "failed-action tool_call" in reminder
    assert "error record containing the exact error_signature" in reminder
    assert trial["prefix_id"] not in reminder


@pytest.mark.parametrize("variant", ["unauthorized", "malformed", "length", "limit", "bad_judge"])
def test_runner_failure_paths(prepared, data, tmp_path, variant):
    _, package, _ = prepared
    _, trials, rubrics, _ = load_prepared(package)
    trial = next(t for t in trials if t["phase"] == "interactive" and t["recoverability"] == "R2")
    prefixes, gold, queries = data
    prefix = next(p for p in prefixes if p.prefix_id == trial["prefix_id"])
    rubric = rubrics[trial["query_id"]]
    if variant == "unauthorized":
        payloads = [tool_response("delete_everything")]
    elif variant == "malformed":
        payloads = [tool_response("inspect_environment", topic=5)] * 2
    elif variant == "length":
        value = response(canonical_answer(rubric))
        value["choices"][0]["finish_reason"] = "length"
        payloads = [value, value]
    elif variant == "limit":
        payloads = [tool_response("inspect_environment")] * 4
    else:
        payloads = [response(canonical_answer(rubric)), response({})]
    ledger = ledger_for(tmp_path, payloads)
    result = run_episode(trial, rubric, prefix, queries[(prefix.prefix_id, trial["query_type"])],
                          gold[prefix.prefix_id], ledger, judge_calibrated=True,
                          token_counter=estimate_tokens)
    assert result["score"]["audit_pass"] is (variant == "bad_judge")
    assert len(result["model_calls"]) <= 4
    if variant == "unauthorized":
        assert not result["score"]["safety_pass"]
    if variant == "limit":
        assert result["status"] == "max_turns"
        assert result["score"]["operational_safety_pass"]
        assert "tool_loop" in result["score"]["failure_stages"]
        assert "safety" not in result["score"]["failure_stages"]
    if variant == "bad_judge":
        assert result["score"]["judge_auxiliary_pass"] is None
        assert not result["score"]["judge_protocol_valid"]
        assert "judge_protocol_failure" in result["score"]["auxiliary_failure_labels"]
        assert result["score"]["failure_stages"] == []
        assert result["score"]["auxiliary_failure_stages"] == ["judge_protocol"]
        assert result["judge_parse_error"]


def test_judge_protocol_failure_gets_one_isolated_repair(prepared, data, tmp_path):
    _, package, _ = prepared
    _, trials, rubrics, _ = load_prepared(package)
    trial = next(t for t in trials if t["phase"] == "main")
    prefixes, gold, queries = data
    prefix = next(p for p in prefixes if p.prefix_id == trial["prefix_id"])
    rubric = rubrics[trial["query_id"]]
    wire = canonical_answer(rubric)
    invalid = rule_judge_fixture(wire, rubric)
    invalid["contradictions"] = ["This reference fact is absent from answer.a."]
    repaired = rule_judge_fixture(wire, rubric)
    ledger = ledger_for(tmp_path, [response(wire), response(invalid), response(repaired)])

    result = run_episode(
        trial,
        rubric,
        prefix,
        queries[(prefix.prefix_id, trial["query_type"])],
        gold[prefix.prefix_id],
        ledger,
        judge_calibrated=True,
        token_counter=estimate_tokens,
        judge_protocol_repair=True,
    )

    assert result["run_validity"] == "valid"
    assert result["judge_parse_error"] is None
    assert "contradiction_not_exact_answer_substring" in result["judge_first_parse_error"]
    assert result["judge_format_repair_used"]
    assert result["judge_format_repair_succeeded"]
    assert result["score"]["judge_protocol_valid"]
    assert [row["kind"] for row in ledger.rows] == [
        "answer", "judge", "judge_format_repair"]
    first_judge = ledger.rows[-2]["request"]
    repair_judge = ledger.rows[-1]["request"]
    assert first_judge["messages"][-1] == repair_judge["messages"][-1]
    assert "This reference fact" not in json.dumps(repair_judge)
    assert "previous judge output" in repair_judge["messages"][0]["content"]


def test_live_requires_date_price_tokenizer_and_budget(monkeypatch):
    monkeypatch.delenv("TRACEGRAPH_DISABLE_LIVE", raising=False)
    config = load_pilot_config(CONFIG)
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    config["authorization"].update(authorized_by_user=True, authorization_id="unit-test", date=today)
    config["pricing_snapshot"]["date"] = today
    preflight = {"tokenizer": {"exact": True}, "cost_upper_bound_cny": 75,
                 "request_upper_bound": 1298}
    with pytest.raises(ValueError, match="tokenizer"):
        validate_live(config, preflight, "unit-test", Path.cwd())
    monkeypatch.setattr("tracegraph.benchmark.compression_audit.development_ledger.load_counter",
                        lambda *args: (estimate_tokens, {"exact": True}))
    validate_live(config, preflight, "unit-test", Path.cwd())
    for field, change, match in (
        ("authorization", {"date": "2000-01-01"}, "authorization"),
        ("pricing_snapshot", {"date": "2000-01-01"}, "price"),
        ("pricing_snapshot", {"input_per_million": -1}, "prices"),
    ):
        changed = copy.deepcopy(config)
        changed[field].update(change)
        with pytest.raises(ValueError, match=match):
            validate_live(changed, preflight, "unit-test", Path.cwd())
    with pytest.raises(ValueError, match="limit"):
        validate_live(config, {**preflight, "cost_upper_bound_cny": 101}, "unit-test", Path.cwd())


def test_failed_judge_gate_does_not_block_deterministic_main_run(prepared, tmp_path, monkeypatch):
    dataset, package, _ = prepared
    monkeypatch.setattr("tracegraph.benchmark.compression_audit.development_runner.validate_live",
                        lambda *a: None)
    calls = []

    def transport(endpoint, key, body, **kwargs):
        calls.append(body)
        return response({}), 0

    report = run_pilot(package, dataset, tmp_path / "failed", mode="live", transport=transport)
    assert not report["judge_gate"]["pass"]
    assert report["stop_reason"] == "model_calibration_failed"
    assert report["episode_count"] == 40
    assert len(calls) > 80
    assert any(not is_judge_request(body) for body in calls)


def test_job_boundary_pause_resume_and_binding(prepared, tmp_path, monkeypatch):
    dataset, package, _ = prepared
    monkeypatch.setattr("tracegraph.benchmark.compression_audit.development_runner.validate_live",
                        lambda *a: None)
    output = tmp_path / "paused"
    report = run_pilot(package, dataset, output, mode="live", max_new_requests=1,
                       transport=lambda *a, **k: (response({}), 0))
    assert report["provider_requests"] == 1 and report["stop_reason"] == "paused_at_job_boundary"
    report = run_pilot(package, dataset, output, mode="live", max_new_requests=1, resume=True,
                       transport=lambda *a, **k: (response({}), 0))
    assert report["provider_requests"] == 2
    with pytest.raises(ValueError, match="binding"):
        run_pilot(package, dataset, output, resume=True)


def test_failed_model_gate_stops_before_main_matrix(prepared, tmp_path, monkeypatch):
    dataset, package, _ = prepared
    monkeypatch.setattr("tracegraph.benchmark.compression_audit.development_runner.validate_live",
                        lambda *a: None)

    def transport(endpoint, key, body, **kwargs):
        if is_judge_request(body):
            payload = json.loads(body["messages"][-1]["content"])
            rubric = {"necessary_facts": payload["necessary_facts"],
                      "contradictory_facts": []}
            phrase = "The earlier attempt succeeded without any failure."
            if phrase in payload["answer"]["a"]:
                rubric["contradictory_facts"] = [phrase]
            return response(rule_judge_fixture(payload["answer"], rubric)), 0
        return response({"a": "invalid"}), 0

    report = run_pilot(package, dataset, tmp_path / "failed-model", mode="live", transport=transport)
    assert report["judge_gate"]["pass"]
    assert report["stop_reason"] == "model_calibration_failed"
    assert report["episode_count"] == 40 and report["provider_requests"] == 160
    assert not report["model_gate"]["pass"]


def test_interrupted_paid_episode_is_reported_without_replaying(prepared, tmp_path, monkeypatch):
    dataset, package, _ = prepared
    monkeypatch.setattr("tracegraph.benchmark.compression_audit.development_runner.validate_live",
                        lambda *a: None)

    def transport(endpoint, key, body, **kwargs):
        if is_judge_request(body):
            payload = json.loads(body["messages"][-1]["content"])
            rubric = {"necessary_facts": payload["necessary_facts"],
                      "contradictory_facts": []}
            phrase = "The earlier attempt succeeded without any failure."
            if phrase in payload["answer"]["a"]:
                rubric["contradictory_facts"] = [phrase]
            return response(rule_judge_fixture(payload["answer"], rubric)), 0
        raise TimeoutError()

    output = tmp_path / "interrupted"
    report = run_pilot(package, dataset, output, mode="live", transport=transport)
    assert report["provider_requests"] == 81 and report["episode_count"] == 1
    row = load_jsonl(output / "episodes.jsonl")[0]
    assert row["incomplete"] and row["status"] == "interrupted"
    assert "provider_or_budget_interruption" in row["score"]["failure_labels"]
    assert not any(j["kind"] == "episode" for j in load_jsonl(output / "completed_jobs.jsonl"))
    resumed = run_pilot(package, dataset, output, mode="live", transport=transport, resume=True)
    assert resumed["provider_requests"] == 81 and resumed["episode_count"] == 1
