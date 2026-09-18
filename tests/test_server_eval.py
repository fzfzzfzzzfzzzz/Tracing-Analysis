"""Server matrix, official bridges, accounting and gates; no model network calls."""

from __future__ import annotations

import copy
import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracegraph.benchmark.compression_audit.artifacts import load_dataset
from tracegraph.benchmark.compression_audit.build import write_file_manifest
from tracegraph.benchmark.compression_audit.dataset import generate_controlled_dataset
from tracegraph.benchmark.compression_audit.development_experiment import write_json, write_rows
from tracegraph.benchmark.compression_audit.development_experiment import select_population
from tracegraph.benchmark.compression_audit.development_scoring import rule_judge_fixture
from tracegraph.benchmark.compression_audit.development_protocol import (
    compact_format_repair_message,
)
from tracegraph.benchmark.compression_audit.io import file_sha256, load_jsonl, stable_digest
from tracegraph.benchmark.server_eval.config import (
    LocalTokenizer, answer_response_format, load_config,
)
from tracegraph.benchmark.server_eval.external import bwrap_search, docker_search, verify_source
from tracegraph.benchmark.server_eval.methods import MemoryMethods, chunks, record_ids
from tracegraph.benchmark.server_eval.judge_review import (
    export_real_answer_review, import_real_answer_reviews,
)
from tracegraph.benchmark.server_eval.prepare import prepare
from tracegraph.benchmark.server_eval.provider import ServerLedger, StopRun
from tracegraph.benchmark.server_eval.report import (
    paired_interval,
    rescore,
    rescore_gold_migration,
)
from tracegraph.benchmark.server_eval.runner import control, run
from tracegraph.capture import estimate_tokens


@pytest.fixture(scope="module")
def data(tmp_path_factory):
    root = tmp_path_factory.mktemp("server-eval-data")
    prefixes, gold, queries = generate_controlled_dataset()
    (root / "public").mkdir()
    (root / "private").mkdir()
    write_rows(root / "public/prefixes.jsonl", [p.to_dict() for p in prefixes])
    write_rows(root / "public/queries.jsonl", [q.to_dict() for q in queries])
    write_rows(root / "private/all_gold.jsonl", [g.to_dict() for g in gold])
    write_json(root / "manifest.json", {"benchmark_id": "compression_audit_v1"})
    write_file_manifest(root)
    return root, prefixes, queries


@pytest.fixture
def config():
    value = load_config(Path("configs/server_eval_qwen38.json"))
    value["models"] = [value["models"][1]]
    value["methods"] = ["full_history", "rolling_summary"]
    value["history_budgets"] = [3072]
    for model in value["models"]:
        model.update(base_url="http://127.0.0.1:8000/v1", served_model="local-test",
                     returned_model_allowlist=["local-test"])
    return value


def freeze(tmp_path, config, dataset):
    path = tmp_path / "config.json"
    write_json(path, config)
    out = tmp_path / "prepared"
    report = prepare(path, dataset, out, Path.cwd())
    return out, report


def test_vllm_085_uses_compatible_wire_schema_with_local_strict_validation() -> None:
    response_format = answer_response_format({"server_version": "0.8.5"})
    schema = response_format["json_schema"]["schema"]
    assert schema["properties"]["a"]["minLength"] == 1
    assert "uniqueItems" not in schema["properties"]["e"]


def test_format_repair_is_bounded_and_does_not_echo_prior_response() -> None:
    huge_error = "malformed " + "private-response-token " * 10_000
    message = compact_format_repair_message(
        huge_error,
        missing_exact=["error_signature"],
        missing_semantic=["resolution_evidence"],
    )
    assert len(message) < 900
    assert "private-response-token" in message
    assert message.count("private-response-token") < 20
    assert "prior response is omitted" in message.lower()


def test_freeze_matrix_has_disjoint_population_and_no_provider_calls(tmp_path, data, config):
    prepared, summary = freeze(tmp_path, config, data[0])
    assert summary["provider_requests"] == 0 and not summary["live_ready"]
    population = json.loads((prepared / "population.json").read_text())
    assert not set(population["main"]) & set(population["calibration"])
    assert summary["episode_count"] == 224
    trials = load_jsonl(prepared / "trials.jsonl")
    assert len({r["episode_id"] for r in trials}) == len(trials)
    with pytest.raises(ValueError, match="new output"):
        prepare(tmp_path / "config.json", data[0], prepared, Path.cwd())


def test_calibration_only_freeze_cannot_schedule_main_or_exposed_prefixes(
        tmp_path, data, config):
    first = select_population(data[1], config["seed"])
    refreshed = copy.deepcopy(config)
    refreshed["seed"] = 20260918
    refreshed["data"]["calibration_only"] = True
    refreshed["data"]["calibration_exclude_prefix_ids"] = first["calibration"]
    path = tmp_path / "refresh.json"
    write_json(path, refreshed)
    output = tmp_path / "refresh"
    report = prepare(path, data[0], output, Path.cwd())
    population = json.loads((output / "population.json").read_text())
    trials = load_jsonl(output / "trials.jsonl")
    assert report["calibration_only"] and report["episode_count"] == 40
    assert report["phase_counts"] == {"calibration": 40}
    assert not population["main"] and not population["diagnostic"]
    assert set(population["calibration"]).isdisjoint(first["calibration"])
    assert {row["phase"] for row in trials} == {"calibration"}

    refreshed["seed"] = config["seed"]
    write_json(tmp_path / "overlap.json", refreshed)
    with pytest.raises(ValueError, match="exposed prefix"):
        prepare(tmp_path / "overlap.json", data[0], tmp_path / "overlap", Path.cwd())


def test_explicit_development_subset_freezes_reproducible_canary(
        tmp_path, data, config):
    selected = select_population(data[1], config["seed"])["main"][:2]
    canary = copy.deepcopy(config)
    canary["data"]["prefix_ids"] = selected
    prepared, summary = freeze(tmp_path, canary, data[0])
    population = json.loads((prepared / "population.json").read_text())
    trials = load_jsonl(prepared / "trials.jsonl")
    assert summary["explicit_development_subset"]
    assert summary["main_prefix_count"] == 2
    assert population["main"] == selected
    assert population["diagnostic"] == selected
    assert summary["episode_count"] == 62
    assert {row["prefix_id"] for row in trials if row["phase"] == "main"} == set(selected)

    canary["data"]["prefix_ids"] = ["missing-prefix"]
    path = tmp_path / "bad-canary.json"
    write_json(path, canary)
    with pytest.raises(ValueError, match="unknown prefix"):
        prepare(path, data[0], tmp_path / "bad-canary", Path.cwd())


def test_capability_attribution_freezes_only_full_and_oracle_chain(
        tmp_path, data, config):
    selected = select_population(data[1], config["seed"])["main"][:2]
    capability = copy.deepcopy(config)
    capability["data"]["prefix_ids"] = selected
    capability["methods"] = ["full_history"]
    capability["interactive"] = False
    capability["capability_attribution_only"] = True
    prepared, summary = freeze(tmp_path, capability, data[0])
    trials = load_jsonl(prepared / "trials.jsonl")
    assert summary["episode_count"] == 44
    assert summary["phase_counts"] == {
        "calibration": 40, "main": 2, "diagnostic": 2,
    }
    target = [row for row in trials if row["phase"] != "calibration"]
    assert {(row["phase"], row["query_type"], row["method_id"])
            for row in target} == {
        ("main", "audit_chain", "full_history"),
        ("diagnostic", "audit_chain", "oracle"),
    }


def test_oracle_gate_only_skips_full_history_without_changing_population(
        tmp_path, data, config):
    selected = select_population(data[1], config["seed"])["main"][:2]
    capability = copy.deepcopy(config)
    capability["data"]["prefix_ids"] = selected
    capability["methods"] = ["full_history"]
    capability["interactive"] = False
    capability["capability_attribution_only"] = True
    capability["oracle_gate_only"] = True
    prepared, summary = freeze(tmp_path, capability, data[0])
    trials = load_jsonl(prepared / "trials.jsonl")
    population = json.loads((prepared / "population.json").read_text())
    assert summary["oracle_gate_only"]
    assert summary["episode_count"] == 42
    assert summary["phase_counts"] == {"calibration": 40, "diagnostic": 2}
    assert summary["full_history_context_check_count"] == 0
    assert population["main"] == selected
    assert population["diagnostic"] == selected
    target = [row for row in trials if row["phase"] != "calibration"]
    assert {(row["prefix_id"], row["query_type"], row["method_id"])
            for row in target} == {
        (prefix_id, "audit_chain", "oracle") for prefix_id in selected
    }

    invalid = copy.deepcopy(config)
    invalid["oracle_gate_only"] = True
    path = tmp_path / "invalid-oracle-gate.json"
    write_json(path, invalid)
    with pytest.raises(ValueError, match="requires capability"):
        load_config(path)


def test_evidence_only_oracle_is_budget_invariant_and_has_no_recent_filler(
        tmp_path, data, config):
    selected = select_population(data[1], config["seed"])["main"][:2]
    capability = copy.deepcopy(config)
    capability["data"]["prefix_ids"] = selected
    capability["methods"] = ["full_history"]
    capability["interactive"] = False
    capability["capability_attribution_only"] = True
    capability["oracle_gate_only"] = True
    capability["oracle_context_mode"] = "evidence_only"
    prepared, summary = freeze(tmp_path, capability, data[0])
    trials = load_jsonl(prepared / "trials.jsonl")
    target = [row for row in trials if row["phase"] == "diagnostic"]
    assert summary["oracle_context_mode"] == "evidence_only"
    assert {row["method_id"] for row in target} == {"oracle_evidence_only"}

    prefix = next(item for item in data[1] if item.prefix_id == selected[0])
    query = next(item for item in data[2]
                 if item.prefix_id == selected[0] and item.query_type == "audit_chain")
    # load_dataset objects keep gold separately; resolve it from the fixture dataset.
    _, _, gold_rows = load_dataset(data[0])
    gold = next(item for item in gold_rows if item.prefix_id == selected[0])
    small = control(prefix, query, gold, "oracle_evidence_only", 4096,
                    estimate_tokens, False)
    large = control(prefix, query, gold, "oracle_evidence_only", 8192,
                    estimate_tokens, False)
    assert small["records"] == large["records"]
    assert small["visible_event_ids"] == large["visible_event_ids"]
    assert small["token_count"] == large["token_count"]
    assert set(gold.ordered_event_ids) <= set(small["visible_event_ids"])

    invalid = copy.deepcopy(capability)
    invalid["oracle_gate_only"] = False
    path = tmp_path / "invalid-evidence-only.json"
    write_json(path, invalid)
    with pytest.raises(ValueError, match="requires oracle_gate_only"):
        load_config(path)


def test_explicit_calibration_population_is_frozen_and_stratified(
        tmp_path, data, config):
    selected = select_population(data[1], config["seed"])
    frozen = copy.deepcopy(config)
    frozen["data"]["calibration_prefix_ids"] = selected["calibration"]
    frozen["data"]["prefix_ids"] = selected["main"][:2]
    prepared, _ = freeze(tmp_path, frozen, data[0])
    population = json.loads((prepared / "population.json").read_text())
    assert population["calibration"] == selected["calibration"]

    frozen["data"]["calibration_prefix_ids"] = selected["calibration"][:-1]
    path = tmp_path / "bad-calibration.json"
    write_json(path, frozen)
    with pytest.raises(ValueError, match="exactly 8"):
        load_config(path)


def test_full_offline_suite_pause_resume_and_independent_rescore(tmp_path, data, config):
    prepared, _ = freeze(tmp_path, config, data[0])
    output = tmp_path / "run"
    partial = run(prepared, data[0], output, Path.cwd(), max_new_jobs=2)
    assert partial["stop_reason"] == "paused_at_job_boundary"
    report = run(prepared, data[0], output, Path.cwd(), resume=True)
    assert report["completed_episodes"] == 224
    assert report["provider_requests"] == 0
    assert report["cells"][0]["calibration"]["pass"]
    assert (output / "cells/qwen38_14b-b3072/failure_table.csv").exists()
    rebuilt = rescore(prepared, output, tmp_path / "rescore", dataset=data[0])
    assert rebuilt["cells"] == report["cells"]
    assert rebuilt["failure_counts"] == report["failure_counts"]
    identity = json.loads((tmp_path / "rescore/rescore_identity.json").read_text())
    assert identity["rubric_source"] == "frozen_dataset_rebuild"
    assert identity["new_provider_requests"] == 0

    rubric = next(
        row for row in load_jsonl(prepared / "rubrics.jsonl")
        if row["query_id"].endswith(":audit_chain")
    )
    rubric.pop("rubric_hash")
    core = rubric["strict_chain"][:4]
    constraints = [[left, right] for left, right in zip(core, core[1:])]
    rubric.update(
        schema_version="test_core_overlay_v1",
        alternative_evidence_sets=[core],
        causal_mode="partial_order",
        causal_constraints=constraints,
        causal_paths=[{"evidence_ids": core, "constraints": constraints}],
        human_validated=False,
    )
    rubric["rubric_hash"] = stable_digest(rubric)
    overlay = tmp_path / "rubric-overlay.jsonl"
    write_rows(overlay, [rubric])
    rescored = rescore(
        prepared, output, tmp_path / "overlay-rescore",
        dataset=data[0], external_rubrics=overlay,
    )
    assert rescored["provider_requests"] == 0
    overlay_identity = json.loads(
        (tmp_path / "overlay-rescore/rescore_identity.json").read_text()
    )
    assert overlay_identity["rubric_source"] == "external_rubric_overlay"
    assert overlay_identity["external_rubrics_sha256"] == file_sha256(overlay)
    assert overlay_identity["post_hoc_sensitivity"] is True
    assert (tmp_path / "overlay-rescore/rubrics.jsonl").exists()

    with pytest.raises(ValueError, match="requires the frozen dataset"):
        rescore(
            prepared, output, tmp_path / "invalid-overlay-rescore",
            external_rubrics=overlay,
        )

    selected_rubric = next(
        row for row in load_jsonl(prepared / "rubrics.jsonl")
        if row["query_id"].endswith(":audit_chain")
    )
    prefixes, queries, gold_rows = load_dataset(data[0], legacy=False)
    query = next(row for row in queries if row.query_id == selected_rubric["query_id"])
    gold = next(row for row in gold_rows if row.prefix_id == query.prefix_id)
    core = list(gold.ordered_event_ids[:4])
    constraints = [[left, right] for left, right in zip(core, core[1:])]
    migrated_gold = replace(gold, ordered_event_ids=tuple(core), annotation={
        **gold.annotation,
        "audit_chain_rubric": {
            "alternative_evidence_sets": [core],
            "relevant_evidence_ids": list(gold.ordered_event_ids),
            "causal_paths": [{"evidence_ids": core, "constraints": constraints}],
        },
    })
    migrated_query = replace(query, text=query.text + " Begin at the declared public anchor.")
    target_dataset = tmp_path / "gold-migration-dataset"
    shutil.copytree(data[0], target_dataset)
    write_rows(target_dataset / "private/all_gold.jsonl", [
        migrated_gold.to_dict() if row.prefix_id == gold.prefix_id else row.to_dict()
        for row in gold_rows
    ])
    write_rows(target_dataset / "public/queries.jsonl", [
        migrated_query.to_dict() if row.query_id == query.query_id else row.to_dict()
        for row in queries
    ])
    receipt = target_dataset / "audit/gold_migration.jsonl"
    receipt.parent.mkdir()
    write_rows(receipt, [{
        "prefix_id": gold.prefix_id,
        "old_gold_hash": gold.gold_hash,
        "new_gold_hash": migrated_gold.gold_hash,
    }])
    target_manifest = json.loads((data[0] / "manifest.json").read_text())
    target_manifest.update(
        parent_dataset_manifest_sha256=file_sha256(data[0] / "manifest.json"),
        parent_dataset_file_manifest_sha256=file_sha256(data[0] / "file_manifest.jsonl"),
        gold_migration_receipt_sha256=file_sha256(receipt),
        migrated_prefix_ids=[gold.prefix_id],
        reanchored_query_ids=[query.query_id],
        development_only=True,
    )
    write_json(target_dataset / "manifest.json", target_manifest)
    write_file_manifest(target_dataset)
    migrated_report = rescore_gold_migration(
        prepared, output, data[0], target_dataset, tmp_path / "gold-migration-rescore",
    )
    assert migrated_report["new_provider_requests"] == 0
    assert migrated_report["formal_comparison_eligible"] is False
    assert migrated_report["gold_migration_mismatch_counts"]["query_prompt_mismatch"] > 0
    assert (
        migrated_report["gold_migration_mismatch_counts"]
        ["deterministic_contract_changed"]
        > 0
    )
    assert (
        migrated_report["gold_migration_mismatch_counts"]
        ["semantic_judge_contract_invalidated"]
        == 0
    )
    migrated_episode = next(
        row for row in load_jsonl(tmp_path / "gold-migration-rescore/episodes.jsonl")
        if row["query_id"] == query.query_id
    )
    assert migrated_episode["gold_migration"]["deterministic_contract_changed"]
    assert not migrated_episode["gold_migration"]["semantic_judge_contract_changed"]
    assert migrated_episode["gold_migration"]["cached_judge_reused"]
    assert not migrated_episode["gold_migration"]["direct_evaluation_eligible"]
    migration_identity = json.loads(
        (tmp_path / "gold-migration-rescore/gold_migration_rescore_identity.json").read_text()
    )
    assert migration_identity["new_provider_requests"] == 0
    assert migration_identity["migrated_prefix_ids"] == [gold.prefix_id]


class Counter:
    def count(self, value):
        return estimate_tokens(value)

    def request_count(self, value):
        return 16000


def response(text="ok"):
    return {"model": "local-test", "usage": {"prompt_tokens": 32, "completion_tokens": 10},
            "choices": [{"finish_reason": "stop", "message": {"content": text}}]}


@pytest.mark.parametrize("bad", ["network", "usage", "model", "too_many_tokens"])
def test_uncertain_requests_never_replay(tmp_path, config, bad):
    calls = []
    def transport(*args, **kwargs):
        calls.append(args)
        value = response()
        if bad == "network":
            raise TimeoutError()
        if bad == "usage":
            del value["usage"]
        if bad == "model":
            value["model"] = "wrong"
        if bad == "too_many_tokens":
            value["usage"]["prompt_tokens"] = 16001
        return value, .01
    ledger = ServerLedger(tmp_path, config, {"qwen38_14b": Counter()}, set(), transport=transport)
    with pytest.raises(StopRun):
        ledger.call("qwen38_14b", {"messages": []}, job_id="job", kind="answer")
    with pytest.raises(StopRun):
        ledger.call("qwen38_14b", {"messages": []}, job_id="job", kind="answer")
    assert len(calls) == 1
    with pytest.raises(StopRun):
        ServerLedger(tmp_path, config, {"qwen38_14b": Counter()}, {"job"}, transport=transport)


def test_role_routing_thinking_parameters_and_job_resume(tmp_path, config):
    config["models"].append({**config["models"][0], "id": "answer", "served_model": "answer-model",
                             "returned_model_allowlist": ["answer-model"]})
    requests = []
    def transport(endpoint, key, body, **kwargs):
        requests.append(body)
        return {**response(), "model": body["model"]}, .01
    counters = {m["id"]: Counter() for m in config["models"]}
    ledger = ServerLedger(tmp_path, config, counters, set(), transport=transport)
    view = ledger.for_model("answer")
    view.call({"messages": []}, job_id="x", kind="answer")
    view.call({"messages": []}, job_id="x", kind="judge")
    view.call({"messages": []}, job_id="x", kind="judge_format_repair")
    assert [r["model"] for r in requests] == [
        "answer-model", "local-test", "local-test"]
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert "enable_thinking" not in requests[0]
    assert requests[0]["temperature"] == 0
    with pytest.raises(StopRun):
        ServerLedger(tmp_path, config, counters, set(), transport=transport)
    assert len(ServerLedger(tmp_path, config, counters, {"x"}, transport=transport).rows) == 3


def test_thinking_can_be_enabled_explicitly(tmp_path, config):
    config["models"][0]["enable_thinking"] = True
    requests = []

    def transport(endpoint, key, body, **kwargs):
        requests.append(body)
        return response(), .01

    ledger = ServerLedger(tmp_path, config, {"qwen38_14b": Counter()}, set(),
                          transport=transport)
    ledger.call("qwen38_14b", {"messages": []}, job_id="thinking", kind="answer")
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": True}


def test_thinking_can_be_scoped_to_mini_agent_requests(tmp_path, config):
    config["models"][0].update(enable_thinking=True,
                               thinking_kinds=["mini_action", "mini_format_repair"],
                               thinking_sampling={"temperature": .6, "top_p": .95,
                                                  "top_k": 20, "min_p": 0})
    requests = []

    def transport(endpoint, key, body, **kwargs):
        requests.append(body)
        return response(), .01

    ledger = ServerLedger(tmp_path, config, {"qwen38_14b": Counter()}, set(),
                          transport=transport)
    ledger.call("qwen38_14b", {"messages": []}, job_id="judge", kind="judge")
    ledger.call("qwen38_14b", {"messages": []}, job_id="mini", kind="mini_action")
    assert [request["chat_template_kwargs"] for request in requests] == [
        {"enable_thinking": False}, {"enable_thinking": True}]
    assert requests[0]["temperature"] == 0 and "top_k" not in requests[0]
    assert {key: requests[1][key] for key in ("temperature", "top_p", "top_k", "min_p")} == {
        "temperature": .6, "top_p": .95, "top_k": 20, "min_p": 0}


def test_live_is_explicit_and_judge_failure_does_not_block_main_score(
        tmp_path, data, config, monkeypatch):
    prepared, _ = freeze(tmp_path, config, data[0])
    with pytest.raises(ValueError, match="execute"):
        run(prepared, data[0], tmp_path / "blocked", Path.cwd(), mode="live")
    monkeypatch.delenv("TRACEGRAPH_DISABLE_LIVE", raising=False)
    calls = []
    def bad_judge(*args, **kwargs):
        calls.append(1)
        return response("invalid judge"), .01
    report = run(prepared, data[0], tmp_path / "live", Path.cwd(), mode="live", execute=True,
                 transport=bad_judge, counters={"qwen38_14b": Counter()})
    assert not report["judge_gate"]["pass"]
    assert report["stop_reason"] is None
    assert len(calls) > 80 and report["completed_episodes"] == 40


def test_development_gate_override_runs_full_matrix_and_is_permanently_reported(
        tmp_path, data, config, monkeypatch):
    prepared, _ = freeze(tmp_path, config, data[0])
    monkeypatch.delenv("TRACEGRAPH_DISABLE_LIVE", raising=False)

    with pytest.raises(ValueError, match="only for an explicit live"):
        run(prepared, data[0], tmp_path / "offline-override", Path.cwd(),
            assume_gates_passed=True)

    def always_invalid(*args, **kwargs):
        return response("invalid structured output"), .01

    output = tmp_path / "live-override"
    report = run(prepared, data[0], output, Path.cwd(), mode="live", execute=True,
        transport=always_invalid, counters={"qwen38_14b": Counter()},
        assume_gates_passed=True)
    assert report["completed_episodes"] == report["planned_episodes"] == 224
    assert not report["judge_gate"]["pass"]
    assert report["gate_override"] == {
        "enabled": True, "scope": "model_calibration_only",
        "raw_gates_remain_authoritative": True, "development_only": True}
    assert report["interpretation"] == "development_comparison_gate_override"
    assert all(cell["calibration_effective_pass"] for cell in report["cells"])
    assert not any(cell["main_skipped_after_calibration"] for cell in report["cells"])
    report_text = (output / "report.md").read_text(encoding="utf-8")
    assert "显式覆盖了模型校准门禁" in report_text
    assert "规则裁判门禁未覆盖且仍须实际通过" in report_text
    assert "覆盖了规则裁判与模型校准门禁" not in report_text

    rebuilt = rescore(prepared, output, tmp_path / "override-rescore")
    assert rebuilt["gate_override"]["enabled"] is True
    assert rebuilt["interpretation"] == "development_comparison_gate_override"
    identity = json.loads((tmp_path / "override-rescore" / "rescore_identity.json").read_text())
    assert identity["new_provider_requests"] == 0
    assert len(identity["source_episodes_sha256"]) == 64
    assert len(identity["rescored_episodes_sha256"]) == 64


def test_pair_chunks_and_summary_visibility(data):
    prefix = data[1][0]
    for batch in chunks(prefix, estimate_tokens, 64):
        ids = {r["record_id"] for r in batch}
        for event in prefix.events:
            if event["event_id"] in ids and event.get("call_id"):
                assert all(e["event_id"] in ids for e in prefix.events
                           if e.get("call_id") == event["call_id"])
    identifier = prefix.events[0]["event_id"]
    assert record_ids(identifier + "suffix", prefix) == []
    assert record_ids("Evidence: " + identifier, prefix) == [identifier]


@pytest.mark.parametrize("method", ["rolling_summary", "acon_official", "ama_official_bm25"])
def test_actual_method_bridge_uses_public_only_and_records_calls(data, config, method):
    if method == "acon_official":
        pytest.importorskip("jinja2")
        pytest.importorskip("requests")
    workspace = Path(os.environ.get("TRACEGRAPH_FROZEN_ROOT", Path.cwd()))
    source = config["sources"].get("acon" if method == "acon_official" else "ama")
    if method != "rolling_summary" and not (workspace / source["path"]).exists():
        pytest.skip("pinned public sources are optional server assets")
    prefix = data[1][0]
    query = next(q for q in data[2] if q.prefix_id == prefix.prefix_id and q.query_type == "audit_chain")
    calls = []
    identifier = prefix.events[0]["event_id"]
    class FakeLedger:
        config = {"model": "fixture"}
        model_id = "fixture_id"
        ledger = SimpleNamespace(models={"fixture_id": {
            "context_window": 32768, "max_output_tokens": 2048}})
        def call(self, body, *, job_id, kind):
            calls.append((body, kind))
            if method == "ama_official_bm25":
                text = ("**STATE_MEMORY**\nmemory_summary: " + identifier + "\n"
                        "**CAUSAL_GRAPH**\n[]") if kind == "construction" else "SUFFICIENT"
            elif method == "acon_official":
                text = "# History Summary\n" + identifier
            else:
                text = identifier
            return response(text)
    engine = MemoryMethods(config, estimate_tokens, workspace, FakeLedger())
    state = engine.build(prefix, method, 3072, "build")
    assert calls and all(kind == "construction" for _, kind in calls)
    assert all(query.text not in json.dumps(body) for body, _ in calls)
    if method == "ama_official_bm25":
        construction_prompt = calls[0][0]["messages"][-1]["content"]
        assert "must fit 1536 tokens" in construction_prompt
        assert "start with exactly `memory_summary:`" in construction_prompt
        assert state["usage"]["state_memory_target_tokens"] == 1536
        assert state["usage"]["construction_chunking"] == (
            "single_session_when_tokenizer_verified")
        assert state["usage"]["session_size_characters"] == (
            state["usage"]["trajectory_characters"] + 1)
        assert state["usage"]["single_session_prompt_tokens"] > 0
        assert state["usage"]["causal_mode"] is False
        assert state["payload"]["causal_graph"] is None
    assert state["usage"]["hidden_gold_observed"] is False
    bundle = engine.materialize(state, prefix, query, "retrieve")
    assert bundle["ingestion_usage"]["implementation"] == method
    if method == "ama_official_bm25":
        assert any(kind == "retrieval" for _, kind in calls)
        assert bundle["retrieval_usage"]["read_event_ids"]
        assert bundle["retrieval_usage"]["native_context_limit_characters"] == 3072
    changed = copy.deepcopy(state)
    changed["budget"] = 9000
    with pytest.raises(ValueError, match="changed"):
        engine.materialize(changed, prefix, query, "bad")


def test_docker_search_mounts_only_public_script(tmp_path, monkeypatch):
    script = tmp_path / "script.py"
    script.write_text("print('public')")
    calls = []
    def fake_run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=b"public", stderr=b"")
    monkeypatch.setattr("tracegraph.benchmark.server_eval.external.subprocess.run", fake_run)
    docker_search(script, "python@sha256:" + "a" * 64, 5)
    assert "--network=none" in calls[0] and "--read-only" in calls[0]
    assert "--pull=never" in calls[0] and "--cap-drop=ALL" in calls[0]
    assert sum("source=" in s for s in calls[0]) == 1
    assert calls[1][:3] == ["docker", "rm", "-f"]


def test_bwrap_search_mounts_only_public_script_and_system_runtime(tmp_path, monkeypatch):
    script = tmp_path / "script.py"
    script.write_text("print('public')")
    calls = []
    monkeypatch.setattr("tracegraph.benchmark.server_eval.external.shutil.which",
                        lambda name: "/usr/bin/bwrap")
    monkeypatch.setattr("tracegraph.benchmark.server_eval.external.Path.is_file",
                        lambda path: True)
    monkeypatch.setattr("tracegraph.benchmark.server_eval.external.Path.exists",
                        lambda path: True)
    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"public", stderr=b"")
    monkeypatch.setattr("tracegraph.benchmark.server_eval.external.subprocess.run", fake_run)
    bwrap_search(script, 5)
    command, kwargs = calls[0]
    assert "--unshare-all" in command and "--new-session" in command
    assert command.count("--ro-bind") >= 2
    assert str(script.resolve()) in command and "/script.py" in command
    assert kwargs["timeout"] == 5 and kwargs["capture_output"]
    assert kwargs["env"] == {"LANG": "C.UTF-8", "PATH": "/usr/bin"}


def test_prefix_cluster_interval_and_source_integrity(tmp_path):
    rows = [{"query_id": "a", "prefix_id": "p", "score": {"hard_pass": True}},
            {"query_id": "b", "prefix_id": "p", "score": {"hard_pass": True}}]
    refs = {r["query_id"]: {"score": {"hard_pass": False}} for r in rows}
    interval = paired_interval(rows, refs, "hard_pass", 3, draws=20)
    assert interval["prefixes"] == 1 and interval["pairs"] == 2
    assert interval["ci95"] == [1, 1]
    with pytest.raises((OSError, ValueError)):
        verify_source({"path": ".", "files": {"missing": "0" * 64}, "revision": "a" * 40}, tmp_path)


@pytest.mark.parametrize("interrupted", [False, True])
def test_model_calibration_and_interruption_do_not_expand(tmp_path, data, config, monkeypatch, interrupted):
    monkeypatch.delenv("TRACEGRAPH_DISABLE_LIVE", raising=False)
    prepared, _ = freeze(tmp_path, config, data[0])
    calls = []
    def transport(endpoint, key, body, **kwargs):
        calls.append(body)
        if body.get("response_format", {}).get("json_schema", {}).get(
                "name") == "compression_audit_judge_v02":
            payload = json.loads(body["messages"][-1]["content"])
            rubric = {"necessary_facts": payload["necessary_facts"],
                      "contradictory_facts": []}
            phrase = "The earlier attempt succeeded without any failure."
            if phrase in payload["answer"]["a"]:
                rubric["contradictory_facts"] = [phrase]
            return response(json.dumps(rule_judge_fixture(payload["answer"], rubric))), .01
        if interrupted:
            raise TimeoutError()
        return response(json.dumps({"a": "I do not know", "e": [], "t": "historical", "s": False})), .01
    output = tmp_path / "live"
    report = run(prepared, data[0], output, Path.cwd(), mode="live", execute=True,
                 transport=transport, counters={"qwen38_14b": Counter()})
    if interrupted:
        assert report["provider_requests"] == 81
        assert load_jsonl(output / "episodes.jsonl")[0]["incomplete"]
        with pytest.raises(StopRun):
            run(prepared, data[0], output, Path.cwd(), mode="live", execute=True, resume=True,
                transport=transport, counters={"qwen38_14b": Counter()})
        assert len(calls) == 81
    else:
        assert report["cells"][0]["main_skipped_after_calibration"]
        assert report["completed_episodes"] == 40
        assert all(e["phase"] == "calibration" for e in load_jsonl(output / "episodes.jsonl"))


def test_local_tokenizer_pins_all_files_and_uses_no_remote_code(tmp_path, config, monkeypatch):
    import hashlib
    import sys
    tokenizer_dir = tmp_path / "tokenizer"
    tokenizer_dir.mkdir()
    (tokenizer_dir / "tokenizer.json").write_text("{}")
    (tokenizer_dir / "tokenizer_config.json").write_text('{"chat_template":"template"}')
    model = config["models"][0]
    model.update(weights_revision="fixed", tokenizer={"path": str(tokenizer_dir),
        "files": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in tokenizer_dir.iterdir()}})
    calls = []
    class Tokenizer:
        def encode(self, text, **kwargs):
            return list(text)
        def apply_chat_template(self, messages, **kwargs):
            calls.append(kwargs)
            return [1, 2, 3]
    class Auto:
        @staticmethod
        def from_pretrained(path, **kwargs):
            assert kwargs == {"local_files_only": True, "trust_remote_code": False}
            return Tokenizer()
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(AutoTokenizer=Auto))
    tokenizer = LocalTokenizer(model, tmp_path)
    assert tokenizer.count("abc") == 3
    assert tokenizer.request_count({"messages": [], "tools": [{"type": "function"}]}) == 259
    assert calls[0]["enable_thinking"] is False and calls[0]["tools"]
    assert calls[0]["return_dict"] is False
    (tokenizer_dir / "extra.json").write_text("{}")
    with pytest.raises(ValueError, match="complete frozen"):
        LocalTokenizer(model, tmp_path)


def test_real_answer_blind_review_export_import_and_adjudication(tmp_path):
    prepared = tmp_path / "prepared"
    run_root = tmp_path / "run"
    prepared.mkdir()
    run_root.mkdir()
    rubrics, jobs = [], []
    for level in ("R0", "R1", "R2", "R3"):
        for prefix_number in (1, 2):
            prefix_id = f"{level}-prefix-{prefix_number}"
            for query_number in range(5):
                query_id = f"{prefix_id}-query-{query_number}"
                rubric = {"query_id": query_id, "necessary_facts": {"fact": "value"},
                           "rubric_hash": stable_digest(query_id)}
                rubrics.append(rubric)
                episode = {
                    "episode_id": f"full_history-{query_id}", "phase": "calibration",
                    "prefix_id": prefix_id, "query_id": query_id,
                    "query_type": "audit_chain", "method_id": "full_history",
                    "recoverability": level, "status": "complete",
                    "answer": {"a": "The value is present.", "e": ["record-1"],
                               "t": "historical", "s": True},
                    "model_calls": [{"kind": "answer", "attempt": {"request": {
                        "messages": [{"role": "user", "content": json.dumps({
                            "question": "What is the value?",
                            "records": [{"id": "record-1", "value": "present"}],
                        })}],
                    }}}],
                    "judge": {"facts": {"fact": "supported"}},
                    "score": {"judge_protocol_valid": True,
                              "judge_protocol_errors": [],
                              "judge_auxiliary_pass": True, "audit_pass": True},
                }
                jobs.append({"kind": "episode", "result": episode,
                             "result_hash": stable_digest(episode)})
    write_rows(prepared / "rubrics.jsonl", rubrics)
    write_json(prepared / "manifest.json", {"prepared": True})
    write_file_manifest(prepared)
    write_json(run_root / "identity.json", {
        "prepared_hash": file_sha256(prepared / "manifest.json"),
        "mode": "live", "assume_gates_passed": False,
    })
    write_json(run_root / "report.json", {"gate_override": {"enabled": False}})
    write_rows(run_root / "completed_jobs.jsonl", jobs)

    packet = tmp_path / "packet"
    manifest = export_real_answer_review(prepared, run_root, packet)
    assert manifest["case_count"] == 40 and manifest["provider_requests"] == 0
    from tracegraph.benchmark.compression_audit.build import verify_file_manifest
    verify_file_manifest(packet)
    cases = load_jsonl(packet / "reviewer_packet/cases.jsonl")
    assert len(cases) == 40
    assert not ({"episode_id", "prefix_id", "query_type", "method_id", "recoverability",
                 "split", "prior_judge"} & set().union(*(set(row) for row in cases)))
    private = load_jsonl(packet / "organizer_private/private_mapping.jsonl")
    assert {row["split"] for row in private} == {"tuning", "holdout"}
    assert not ({row["prefix_id"] for row in private if row["split"] == "tuning"}
                & {row["prefix_id"] for row in private if row["split"] == "holdout"})

    templates = load_jsonl(packet / "reviewer_packet/reviews.reviewer_a.template.jsonl")
    reviews = []
    for reviewer_id in ("reviewer-a", "reviewer-b"):
        rows = copy.deepcopy(templates)
        for row in rows:
            row.update(reviewer_id=reviewer_id, human_reviewed=True,
                       overall_semantic_pass=True)
            row["facts"] = {name: "supported" for name in row["facts"]}
        path = tmp_path / f"{reviewer_id}.jsonl"
        write_rows(path, rows)
        reviews.append(path)
    report = import_real_answer_reviews(packet, reviews, tmp_path / "agreed")
    assert report["real_answer_judge_gate_pass"]
    assert report["semantic_auxiliary_ready"]
    assert report["main_matrix_eligible"] is None
    assert not report["deterministic_main_blocked_by_judge"]
    assert not report["main_matrix_authorized"]
    assert report["metrics"]["holdout_correct"] == 20

    reviewer_b = load_jsonl(reviews[1])
    disputed_id = reviewer_b[0]["blind_id"]
    reviewer_b[0]["facts"]["fact"] = "missing"
    reviewer_b[0]["overall_semantic_pass"] = False
    write_rows(reviews[1], reviewer_b)
    blocked = import_real_answer_reviews(packet, reviews, tmp_path / "disputed")
    assert not blocked["real_answer_judge_gate_pass"] and blocked["disagreement_count"] == 1
    adjudication = load_jsonl(tmp_path / "disputed/adjudication.template.jsonl")
    assert [row["blind_id"] for row in adjudication] == [disputed_id]
    adjudication[0].update(reviewer_id="reviewer-c", human_reviewed=True,
                           overall_semantic_pass=True)
    adjudication[0]["facts"]["fact"] = "supported"
    adjudication_path = tmp_path / "adjudication.jsonl"
    write_rows(adjudication_path, adjudication)
    adjudicated = import_real_answer_reviews(
        packet, reviews, tmp_path / "adjudicated", adjudication=adjudication_path)
    assert adjudicated["real_answer_judge_gate_pass"]
    assert adjudicated["disagreement_count"] == 0


def test_resource_limits_stop_before_persisting_or_sending(tmp_path, config):
    config["limits"]["input_token_limit"] = 1
    def forbidden(*args, **kwargs):
        pytest.fail("network called despite budget")
    ledger = ServerLedger(tmp_path, config, {"qwen38_14b": Counter()}, set(), transport=forbidden)
    with pytest.raises(StopRun, match="resource"):
        ledger.call("qwen38_14b", {"messages": []}, job_id="job", kind="construction")
    assert not (tmp_path / "provider_attempts.jsonl").exists()


def test_cli_bind_and_prepare_remain_offline(tmp_path, data, config, monkeypatch, capsys):
    import sys
    from tracegraph.benchmark.server_eval.__main__ import main
    config_path = tmp_path / "config.json"
    write_json(config_path, config)
    tokenizer = tmp_path / "tokenizer"
    tokenizer.mkdir()
    (tokenizer / "tokenizer.json").write_text("{}")
    (tokenizer / "tokenizer_config.json").write_text("{}")
    bound = tmp_path / "bound.json"
    monkeypatch.setattr(sys, "argv", ["server_eval", "bind-model", "--config", str(config_path),
        "--model-id", "qwen38_14b", "--base-url", "http://127.0.0.1:8000/v1",
        "--served-model", "local", "--weights-revision", "fixed", "--server-version", "tested",
        "--tokenizer-dir", str(tokenizer), "--output", str(bound)])
    main()
    assert json.loads(capsys.readouterr().out)["provider_requests"] == 0
    assert load_config(bound)["models"][0]["weights_revision"] == "fixed"
    monkeypatch.setattr(sys, "argv", ["server_eval", "prepare", "--config", str(config_path),
        "--dataset", str(data[0]), "--output", str(tmp_path / "prepared")])
    main()
    result = json.loads(capsys.readouterr().out)
    assert result["episode_count"] == 224 and result["provider_requests"] == 0
    assert result["canonical_rubric_self_checks"] > 0
    assert "implementation" not in result


def test_heldout_requires_exposure_registry(tmp_path, data, config):
    config["data"]["split"] = "test"
    with pytest.raises(ValueError, match="prior exposure"):
        freeze(tmp_path, config, data[0])


def test_package_excludes_dotenv_and_contains_verifiable_sources(tmp_path, data):
    import importlib.util
    import zipfile
    import hashlib
    specification = importlib.util.spec_from_file_location("package_server_eval", "scripts/package_server_eval.py")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    # Minimal workspace with an explicitly private .env and a valid local dataset.
    import shutil
    dataset = tmp_path / "data"
    shutil.copytree(data[0], dataset)
    (tmp_path / "configs").mkdir()
    write_json(tmp_path / "configs/server_eval_qwen38.json", {"sources": {}})
    (tmp_path / ".env").write_text("SECRET=must-not-be-packaged")
    output = tmp_path / "bundle.zip"
    result = module.package(tmp_path, dataset, output)
    assert result["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    with zipfile.ZipFile(output) as archive:
        assert ".env" not in archive.namelist()
        manifest = json.loads(archive.read("server_bundle_manifest.json"))
        assert all(hashlib.sha256(archive.read(p)).hexdigest() == h for p, h in manifest["files"].items())
