"""Extended paths tested with simulated transports, official agent code, no live calls."""
# ruff: noqa: F811

import copy
import json
import os
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from test_server_eval import data, config, response, Counter  # noqa: F401
from tracegraph.capture import estimate_tokens
from tracegraph.benchmark.compression_audit.artifacts import load_dataset
from tracegraph.benchmark.compression_audit.development_experiment import write_json, write_rows
from tracegraph.benchmark.compression_audit.development_scoring import make_rubric, canonical_answer, score_submission
from tracegraph.benchmark.compression_audit.io import file_sha256, stable_digest, load_jsonl
from tracegraph.benchmark.server_eval.ablations import ABLATIONS
from tracegraph.benchmark.server_eval.analysis import budget_frontiers
from tracegraph.benchmark.server_eval.candidate import NAME, scoped_prefix
from tracegraph.benchmark.server_eval.config import load_config, live_blockers
from tracegraph.benchmark.server_eval.data_workflow import review_export, import_dataset
from tracegraph.benchmark.server_eval.methods import MemoryMethods
from tracegraph.benchmark.server_eval.mini import (
    official_agent, CheckpointEnvironment, MiniModel, run_task, validate_restore_source,
    action_response_format, native_action_tools, parse_action_response,
    parse_native_action_response, prefix_from_messages,
)
from tracegraph.benchmark.server_eval.mini_runner import prepare_mini, execute_mini
from tracegraph.benchmark.server_eval.provider import ServerLedger, StopRun
from tracegraph.benchmark.server_eval.rubrics import validate_rubric, causal_evaluation, import_rubrics
from tracegraph.benchmark.server_eval.runtime import record_runtime, verify_runtime, validate_receipt
from tracegraph.benchmark.server_eval.tuning import prepare_tuning, select_guidance


def workspace():
    return Path(os.environ.get("TRACEGRAPH_FROZEN_ROOT", Path.cwd()))


@pytest.fixture
def extended():
    return load_config(Path("configs/server_eval_qwen38_extended.json"))


@pytest.mark.parametrize("method", [*ABLATIONS, NAME])
def test_actual_policy_ablations_and_candidate(data, config, method):
    prefix = data[1][0]
    query = next(q for q in data[2] if q.prefix_id == prefix.prefix_id)
    calls = []
    class Ledger:
        config = {"model": "fixture"}
        def call(self, body, **kwargs):
            calls.append(body)
            return response(prefix.events[0]["event_id"])
    engine = MemoryMethods(config, estimate_tokens, workspace(), Ledger())
    state = engine.build(prefix, method, 1536, "build")
    bundle = engine.materialize(state, prefix, query, "retrieve")
    assert state["usage"]["implementation"] == method
    assert "policy_plan" in bundle["retrieval_usage"]
    assert state["usage"]["hidden_gold_observed"] is False
    if method == "tracegraph_no_retrieval":
        assert not bundle["retrieval_usage"]["read_event_ids"]
    if method == NAME:
        assert calls and all(query.text not in json.dumps(c) for c in calls)
        assert state["usage"]["algorithm_status"] == "experimental_unvalidated"


def test_public_goal_ownership_never_inferred(data):
    from dataclasses import replace
    prefix = data[1][0]
    events = [dict(e) for e in prefix.events]
    events[0]["goal_id"], events[1]["goal_id"] = "a", "b"
    events[1]["relations"] = [{"source": events[0]["event_id"], "type": "resolves"}]
    scoped, provenance = scoped_prefix(replace(prefix, events=tuple(events)))
    assert not scoped.events[1]["relations"]
    assert provenance["rejected_cross_goal_relations"]
    assert prefix.events[1].get("goal_id") is None


def test_alternative_partial_order_scoring_and_rejections(data, tmp_path):
    prefixes, queries, gold = load_dataset(data[0], legacy=False)
    p, g = prefixes[0], gold[0]
    q = next(q for q in queries if q.prefix_id == p.prefix_id and q.query_type == "audit_chain")
    rubric = make_rubric(q, g)
    rubric.pop("rubric_hash")
    a, b, c = [e["event_id"] for e in p.events[:3]]
    rubric.update(causal_mode="partial_order", alternative_evidence_sets=[[a, b], [a, c]],
        relevant_evidence_ids=[a, b, c], causal_paths=[{"evidence_ids": [a, b], "constraints": [[a, b]]},
        {"evidence_ids": [a, c], "constraints": [[a, c]]}])
    value = validate_rubric(rubric, q, p, g)
    assert causal_evaluation(value, [a, c]) == (True, 1.0)
    assert causal_evaluation(value, [c, a]) == (False, 0.0)
    assert causal_evaluation(value, [a]) == (False, 0.0)
    answer = canonical_answer(value)
    answer["e"] = [a, c]
    score = score_submission(answer, value, [a, b, c])
    assert score["causal_constraint_rate"] == 1 and not score["strict_chain_recovered"]
    path = tmp_path / "rubrics.jsonl"
    write_rows(path, [value])
    imported = import_rubrics(path, [q], {p.prefix_id: p}, {g.prefix_id: g})
    assert imported[q.query_id] == value
    for mutate in (
        lambda r: r.update(gold_hash="wrong"),
        lambda r: r.update(relevant_evidence_ids=["invented"]),
        lambda r: r["causal_paths"][0].update(constraints=[[a, b], [b, a]]),
        lambda r: r.update(human_validated=True),
    ):
        bad = copy.deepcopy(rubric)
        mutate(bad)
        with pytest.raises(ValueError):
            validate_rubric(bad, q, p, g)


def test_gold_annotation_can_define_task_episode_chain_policy(data):
    prefixes, queries, gold = load_dataset(data[0], legacy=False)
    prefix, item = prefixes[0], gold[0]
    query = next(
        row for row in queries
        if row.prefix_id == prefix.prefix_id and row.query_type == "audit_chain"
    )
    core = list(item.ordered_event_ids[:4])
    constraints = [[left, right] for left, right in zip(core, core[1:])]
    migrated = replace(item, annotation={
        **item.annotation,
        "audit_chain_rubric": {
            "alternative_evidence_sets": [core],
            "relevant_evidence_ids": list(item.ordered_event_ids),
            "causal_paths": [{"evidence_ids": core, "constraints": constraints}],
        },
    })
    rubric = make_rubric(query, migrated)
    assert rubric["source"] == "development_gold_migration_policy"
    assert rubric["causal_mode"] == "partial_order"
    assert rubric["alternative_evidence_sets"] == [core]
    assert rubric["causal_paths"][0]["constraints"] == constraints


@pytest.mark.parametrize("bad", [False, "shape", "nan", "network"])
def test_embedding_ledger_no_fallback_or_retry(tmp_path, config, bad):
    config["embedding"] = {"id": "embed", "base_url": "http://localhost:8001/v1",
        "served_model": "embed", "returned_model_allowlist": ["embed"],
        "context_window": 8192, "dimension": 2}
    calls = []
    def transport(endpoint, key, body, **kwargs):
        calls.append(body)
        assert endpoint.endswith("/embeddings") and "temperature" not in body
        if bad == "network":
            raise TimeoutError()
        vector = [1., 0.] if not bad else ([1.] if bad == "shape" else [float("nan"), 0.])
        return {"model": "embed", "usage": {"prompt_tokens": 1},
                "data": [{"embedding": vector}]}, .01
    ledger = ServerLedger(tmp_path, config, {"embed": SimpleNamespace(count=lambda x: 4)}, set(), transport=transport)
    view = ledger.for_model("qwen38_14b")
    if bad:
        with pytest.raises(StopRun):
            view.embed("text", job_id="build", kind="construction_embedding")
        with pytest.raises(StopRun):
            view.embed("text", job_id="build", kind="construction_embedding")
        assert len(calls) == 1
    else:
        assert view.embed("text", job_id="build", kind="construction_embedding") == [1., 0.]
        assert ledger.rows[0]["completion_tokens"] == 0


def test_official_ama_embedding_bridge(data, extended):
    p = data[1][0]
    query = next(q for q in data[2] if q.prefix_id == p.prefix_id)
    vectors = []
    class Ledger:
        config = {"model": "fixture"}
        def embed(self, text, **kwargs):
            vectors.append(kwargs["kind"])
            return [1., 0.]
        def call(self, body, **kwargs):
            return response("**STATE_MEMORY**\nmemory_summary: " + p.events[0]["event_id"] +
                "\n**CAUSAL_GRAPH**\n[]" if kwargs["kind"] == "construction" else "SUFFICIENT")
    engine = MemoryMethods(extended, estimate_tokens, workspace(), Ledger())
    state = engine.build(p, "ama_official_embedding", 3072, "build")
    assert state["payload"]["embed_mem"]["embeddings"]
    engine.materialize(state, p, query, "retrieve")
    assert "construction_embedding" in vectors and vectors[-1] == "retrieval_embedding"


def test_runtime_receipt_and_mismatch(tmp_path, config):
    weights, tokenizer = tmp_path / "weights", tmp_path / "tokenizer"
    weights.mkdir()
    tokenizer.mkdir()
    (weights / "weight.safetensors").write_bytes(b"fixture")
    (tokenizer / "tokenizer.json").write_text("{}")
    spec = {"served_model": "local-test", "weights_revision": "a", "server_version": "b",
            "dtype": "bfloat16", "quantization": "none", "tool_call_parser": "pinned",
            "launch_argv": ["vllm", "serve", "/weights"], "hardware": {"gpu": "test"}}
    source, out = tmp_path / "spec.json", tmp_path / "receipt.json"
    write_json(source, spec)
    result = record_runtime(source, weights, tokenizer, out)
    model = {**config["models"][0], "weights_revision": "a", "server_version": "b",
             "tokenizer": {"files": {"tokenizer.json": file_sha256(tokenizer / "tokenizer.json")}},
             "runtime_receipt": {"path": str(out), "sha256": result["sha256"]}}
    assert verify_runtime(model, tmp_path)["dtype"] == "bfloat16"
    model["server_version"] = "different"
    with pytest.raises(ValueError, match="differs"):
        verify_runtime(model, tmp_path)
    with pytest.raises(ValueError):
        validate_receipt({})


def task():
    return {"id": "test-task", "image": "fixture@sha256:" + "a" * 64, "cwd": "/work",
        "task": "Inspect the local file", "system_prompt": "Return one bash code block.",
        "max_model_calls": 3, "evaluator_command": "test -f result", "env": {"LANG": "C"},
        "state_contract": "filesystem_and_explicit_env_no_background_services"}


def fake_docker(argv, **kwargs):
    text = ""
    if argv[1] == "inspect":
        text = '[{"Mounts": []}]'
    elif argv[1] == "commit":
        assert len(argv) == 3  # default pause semantics; no deprecated --pause=true flag
        text = "sha256:" + "b" * 64
    elif argv[1] == "exec":
        text = "observed\n"
    return SimpleNamespace(returncode=0, stdout=text, stderr="")


def mini_action(command="printf observed", thought="continue the task"):
    return response(json.dumps({"thought": thought, "command": command}))


def test_mini_action_uses_strict_structured_transport():
    schema = action_response_format()["json_schema"]["schema"]
    assert schema["required"] == ["thought", "command"] and schema["additionalProperties"] is False
    assert parse_action_response(mini_action()) == {
        "thought": "continue the task", "command": "printf observed"}
    with pytest.raises(ValueError, match="valid JSON"):
        parse_action_response(response("not-json"))


def test_mini_action_preserves_thinking_with_native_tool_transport(tmp_path):
    wire = response("")
    wire["choices"][0].update(finish_reason="tool_calls")
    wire["choices"][0]["message"].update(
        content="<think>\ninspect first\n</think>\n\n",
        tool_calls=[{"type": "function", "function": {
            "name": "bash", "arguments": json.dumps({"command": "ls -la"})}}],
    )
    assert native_action_tools()[0]["function"]["parameters"]["additionalProperties"] is False
    assert parse_native_action_response(wire) == {
        "thought": "<think>\ninspect first\n</think>", "command": "ls -la"}

    calls = []

    class Ledger:
        config = {"model": "fixture", "enable_thinking": True,
                  "action_transport": "native_tool_call"}

        def call(self, body, **kwargs):
            calls.append(body)
            return wire

    model = MiniModel(Ledger(), None, task(), "full_history", 16384, tmp_path)
    message = model.query([{"role": "system", "content": "system"},
                           {"role": "user", "content": "task"}])
    assert calls[0]["tool_choice"] == "auto" and "response_format" not in calls[0]
    assert message["extra"]["actions"] == [{"command": "ls -la"}]


def test_real_official_agent_loop_checkpoints_and_format_limit(tmp_path, extended, monkeypatch):
    monkeypatch.delenv("TRACEGRAPH_DISABLE_LIVE", raising=False)
    cls = official_agent(extended["sources"]["mini"], workspace())
    calls = []
    class Ledger:
        config = {"model": "fixture"}
        def call(self, body, **kwargs):
            calls.append(body)
            return response("bad format") if len(calls) == 1 else mini_action()
    engine = MemoryMethods(extended, estimate_tokens, workspace(), Ledger())
    model = MiniModel(Ledger(), engine, task(), "full_history", 1536, tmp_path)
    env = CheckpointEnvironment(task(), tmp_path, execute=True, command=fake_docker)
    result = run_task(task(), cls, model, env, tmp_path)
    assert result["model_calls"] == 3 and result["task_success"]
    continued = [call for call in calls if len(call["messages"]) == 3]
    assert continued and "chronological execution history" in continued[0]["messages"][2]["content"]
    assert "do not restart it" in continued[0]["messages"][2]["content"]
    checkpoints = list(tmp_path.glob("checkpoint-*.json"))
    assert len(checkpoints) == 4
    assert len(load_jsonl(tmp_path / "tool_attempts.jsonl")) == 3
    assert (tmp_path / "trajectory.json").exists()
    with pytest.raises(StopRun, match="completed"):
        validate_restore_source(tmp_path, json.loads(checkpoints[-1].read_text()))
    env.close()


def test_checkpoint_restore_unknown_tool_guard_and_environment_contract(tmp_path, monkeypatch):
    monkeypatch.delenv("TRACEGRAPH_DISABLE_LIVE", raising=False)
    with pytest.raises(ValueError, match="execute"):
        CheckpointEnvironment(task(), tmp_path)
    with pytest.raises(ValueError, match="declare"):
        CheckpointEnvironment({**task(), "state_contract": "unknown"}, tmp_path, execute=True)
    env = CheckpointEnvironment(task(), tmp_path, execute=True, command=fake_docker)
    def timeout(*args, **kwargs):
        raise TimeoutError()
    env.command = timeout
    with pytest.raises(StopRun):
        env.execute({"command": "anything"})
    with pytest.raises(StopRun):
        env.execute({"command": "anything"})
    with pytest.raises(StopRun, match="unresolved"):
        validate_restore_source(tmp_path, {})
    assert len(load_jsonl(tmp_path / "tool_attempts.jsonl")) == 1
    assert prefix_from_messages([], "a", 512) is None


def test_mini_freeze_and_unconfigured_live_block(tmp_path, extended):
    config_path, tasks = tmp_path / "c.json", tmp_path / "tasks.jsonl"
    write_json(config_path, extended)
    write_rows(tasks, [task()])
    output = tmp_path / "prepared"
    result = prepare_mini(config_path, tasks, output, workspace())
    assert result["provider_requests"] == 0 and result["request_upper_bound"] > 0
    assert live_blockers(extended, workspace())
    with pytest.raises(ValueError, match="execute"):
        execute_mini(output, tmp_path / "run", workspace(), task_id="test-task",
            model_id="qwen38_14b", method="full_history", budget=1536)
    with pytest.raises(ValueError, match="blocked"):
        execute_mini(output, tmp_path / "run", workspace(), task_id="test-task",
            model_id="qwen38_14b", method="full_history", budget=1536, execute=True)


def test_data_review_import_and_no_invented_real_traces(data, tmp_path):
    draft = tmp_path / "draft"
    report = review_export(data[0], draft)
    assert report["human_reviews"] == 0
    provenance = tmp_path / "provenance.jsonl"
    write_rows(provenance, [{"prefix_id": p.prefix_id, "prefix_hash": p.prefix_hash,
        "source_task_id": p.prefix_id, "source_kind": "controlled_fixture"} for p in data[1]])
    exposure = tmp_path / "exposure.jsonl"
    write_rows(exposure, [])
    out = tmp_path / "imported"
    result = import_dataset(data[0], draft / "rubrics.draft.jsonl", provenance, out, exposure)
    assert result["real_source_tasks"] == 0 and not result["real_100_threshold_met"]
    assert len(load_dataset(out, legacy=False)[0]) == len(data[1])


def test_budget_frontiers_match_questions_no_extrapolation():
    episodes = [{"phase": "main", "cell_id": "model-b" + str(b), "method_id": m,
        "query_id": "q", "score": {"audit_pass": good, "hard_pass": good}}
        for b, m, good in [(768, "a", False), (1536, "a", True), (768, "b", True)]]
    costs = [{"cell_id": "model-b" + str(b), "method_id": m, "input_tokens": t,
              "output_tokens": 0} for b, m, t in [(768, "a", 10), (1536, "a", 30), (768, "b", 20)]]
    result = budget_frontiers(episodes, costs)
    assert len(result["points"]) == 3
    assert result["comparisons"][0]["interpolation"] is False
    assert all(p["total_deployment_tokens"] is None for p in budget_frontiers(episodes, [])["points"])


def test_guidance_frozen_candidates_require_real_evidence(data, config, tmp_path):
    config["models"] = config["models"][:1]
    cp = tmp_path / "config.json"
    write_json(cp, config)
    plan = tmp_path / "tuning"
    result = prepare_tuning(cp, data[0], plan, workspace())
    assert len(result["candidates"]) == 3 and result["provider_requests"] == 0
    runs = tmp_path / "runs"
    (runs / "original").mkdir(parents=True)
    write_json(runs / "original/report.json", {"mode": "offline"})
    write_json(runs / "original/identity.json", {"config_hash": result["candidates"][0]["config_hash"]})
    with pytest.raises(ValueError, match="real"):
        select_guidance(plan, runs, tmp_path / "selection.json")
    for i, candidate in enumerate(result["candidates"]):
        directory = runs / candidate["candidate"]
        directory.mkdir(exist_ok=True)
        write_json(directory / "report.json", {"mode": "live", "completed_episodes": 1,
            "planned_episodes": 1, "cells": [{"calibration": {"pass": True}}],
            "deployment_usage_by_method": [{"method_id": "acon_official", "input_tokens": 10 + i,
                                           "output_tokens": 1}]})
        write_json(directory / "identity.json", {"config_hash": candidate["config_hash"]})
        write_rows(directory / "episodes.jsonl", [{"phase": "main", "method_id": "acon_official",
                                                   "score": {"hard_pass": True}}])
    assert select_guidance(plan, runs, tmp_path / "selected.json")["selected"] == "original"


def test_mini_end_to_end_and_restore_from_resolved_boundary(tmp_path, config, extended, monkeypatch):
    import tracegraph.benchmark.server_eval.mini_runner as runner
    monkeypatch.delenv("TRACEGRAPH_DISABLE_LIVE", raising=False)
    config["sources"]["mini"] = extended["sources"]["mini"]
    config["methods"], config["history_budgets"] = ["full_history"], [1536]
    monkeypatch.setattr(runner, "live_blockers", lambda *a: [])
    monkeypatch.setattr(runner, "source_status", lambda *a: [])
    monkeypatch.setattr(runner, "LocalTokenizer", lambda *a: Counter())
    monkeypatch.setattr("tracegraph.benchmark.server_eval.provider.post", lambda *a, **k:
        (mini_action(), .01))
    monkeypatch.setattr(runner, "CheckpointEnvironment", lambda *a, **k:
        CheckpointEnvironment(*a, **k, command=fake_docker))
    cp, tp = tmp_path / "config.json", tmp_path / "tasks.jsonl"
    write_json(cp, config)
    write_rows(tp, [task()])
    prepared = tmp_path / "prepared"
    prepare_mini(cp, tp, prepared, workspace())
    calibration = tmp_path / "calibration"
    calibration.mkdir()
    write_json(calibration / "report.json", {"mode": "live", "judge_gate": {"pass": True},
        "cells": [{"cell_id": "qwen38_14b-b1536", "calibration": {"pass": True}}]})
    write_json(calibration / "identity.json", {"config_hash": stable_digest(config)})
    original = tmp_path / "original"
    options = {"task_id": task()["id"], "model_id": "qwen38_14b", "method": "full_history",
               "budget": 1536, "execute": True, "calibration": calibration}
    result = execute_mini(prepared, original, workspace(), **options)
    assert result["model_calls"] == 3
    # Build a resolved checkpoint-boundary fixture from the first completed step.
    boundary = tmp_path / "boundary"
    boundary.mkdir()
    checkpoint = sorted(original.glob("checkpoint-0001-*.json"))[0]
    restored_file = boundary / checkpoint.name
    restored_file.write_bytes(checkpoint.read_bytes())
    (boundary / "identity.json").write_bytes((original / "identity.json").read_bytes())
    for filename in ("tool_attempts.jsonl", "tool_results.jsonl", "provider_attempts.jsonl",
                     "provider_ledger.jsonl", "completed_steps.jsonl"):
        write_rows(boundary / filename, load_jsonl(original / filename)[:1])
    restored = tmp_path / "restored"
    report = execute_mini(prepared, restored, workspace(), restore=restored_file, **options)
    assert report["model_calls"] == 3
    ledger = load_jsonl(restored / "provider_ledger.jsonl")
    assert len(ledger) == 3 and ledger[0] == load_jsonl(original / "provider_ledger.jsonl")[0]
    assert len(load_jsonl(restored / "tool_attempts.jsonl")) == 3
    from tracegraph.benchmark.server_eval.data_workflow import export_mini_trace
    exported = tmp_path / "exported"
    assert export_mini_trace(restored, exported)["annotated_prefixes"] == 0
    assert load_jsonl(exported / "prefixes.unannotated.jsonl")[0]["source_ref"]["checkpoint_sha256"]


def test_cli_extended_routes_are_reviewable_offline(tmp_path, data, extended, monkeypatch, capsys):
    import sys
    from tracegraph.benchmark.server_eval.__main__ import main
    monkeypatch.setattr(sys, "argv", ["server_eval", "review-export", "--dataset", str(data[0]),
                                     "--output", str(tmp_path / "review")])
    main()
    assert json.loads(capsys.readouterr().out)["human_reviews"] == 0
    config_path, tasks = tmp_path / "config.json", tmp_path / "tasks.jsonl"
    write_json(config_path, extended)
    write_rows(tasks, [task()])
    monkeypatch.setattr(sys, "argv", ["server_eval", "mini-prepare", "--config", str(config_path),
        "--tasks", str(tasks), "--output", str(tmp_path / "mini")])
    main()
    assert json.loads(capsys.readouterr().out)["real_tasks_executed"] == 0


def test_fallback_preserves_hard_spans_and_bounded_summary(data, config, monkeypatch):
    p = data[1][0]
    q = next(q for q in data[2] if q.prefix_id == p.prefix_id)
    class Ledger:
        config = {"model": "fixture"}
        def call(self, body, **kwargs):
            return response(p.events[0]["event_id"])
    engine = MemoryMethods(config, estimate_tokens, workspace(), Ledger())
    state = engine.build(p, NAME, 1536, "build")
    adapter, native = engine._builtin(p, NAME, 1536)
    original = adapter.materialize
    from dataclasses import replace
    def fail(*args):
        bundle = original(*args)
        return replace(bundle, retrieval_usage={**bundle.retrieval_usage, "send_eligible": False})
    monkeypatch.setattr(adapter, "materialize", fail)
    bundle = engine.materialize(state, p, q, "query")
    assert bundle["retrieval_usage"]["fallback"] == "explicit_summary_plus_hard_evidence"
    assert bundle["token_count"] <= 1536
    assert bundle["records"][0]["record_id"] == "fallback_summary"
