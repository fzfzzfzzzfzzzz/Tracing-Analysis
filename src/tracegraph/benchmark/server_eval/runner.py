"""Sequential gated suite execution and crash-safe job-boundary resumption."""

from __future__ import annotations

import json
import os
from pathlib import Path

from ...capture import estimate_tokens
from ..compression_audit.build import verify_file_manifest
from ..compression_audit.development_adapters import close_pairs, make_development_adapter
from ..compression_audit.development_experiment import (
    answer_request, development_implementation, match_control_sizes,
)
from ..compression_audit.development_results import judge_gate, model_gate
from ..compression_audit.development_runner import evaluate_judge_example, interrupted_episode, run_episode
from ..compression_audit.development_protocol import (
    ANSWER_CONTRACT_REVISION, INTERACTION_POLICY_REVISION, JUDGE_PROTOCOL_REVISION,
)
from ..compression_audit.io import file_sha256, load_jsonl, stable_digest
from .config import (EmbeddingTokenizer, LocalTokenizer, answer_response_format,
                     answer_transport_flags, live_blockers, load_config)
from .external import source_status
from .methods import MemoryMethods
from .provider import ServerLedger, StopRun, append
from .report import write_report
from .data_workflow import load_suite_dataset


def control(prefix, query, gold, method, budget, count, exact):
    adapter = make_development_adapter("recent_masking", token_counter=count)
    chain = close_pairs(prefix, set(gold.ordered_event_ids))
    if method == "oracle_evidence_only":
        # A capability ceiling must not change its distractors when the budget changes.
        # Keep the historical recent+gold Oracle intact for comparability and expose a
        # new intervention that contains only the frozen gold chain plus call/result
        # pair closure.
        common = set()
        selected = chain
    else:
        common = adapter.fit(prefix, [e["event_id"] for e in reversed(prefix.events)],
                             budget // 3, forbidden=chain)
        selected = common if method == "deletion" else common | chain
    records = adapter.records(prefix, selected)
    matched = None
    if method == "irrelevant":
        oracle = records
        records = adapter.records(prefix, common) + [{"record_id": "unrelated-control",
            "kind": "padding", "representation": "irrelevant_size_control", "content": ""}]
        if exact:
            records = match_control_sizes(records, oracle, query, count)
            matched = True
        else:
            records[-1]["content"] = " neutral" * max(0, count(oracle) - count(records))
            matched = False
        selected = common
    return {"records": records, "visible_event_ids": sorted(selected), "retrieved_event_ids": [],
        "token_count": count(records), "budget_tokens": budget, "state_hash": stable_digest(records),
        "ingestion_usage": {"implementation": "organizer_diagnostic_v02", "hidden_gold_observed": True},
        "retrieval_usage": {"send_eligible": count(records) <= budget, "exact_size_match": matched,
                            "read_event_ids": [], "safety_reasons": []}}


def failed_artifact(reason, *, run_validity="integration_invalid"):
    if run_validity not in {"method_failure", "integration_invalid"}:
        raise ValueError("invalid run_validity")
    return {"records": [], "visible_event_ids": [], "token_count": 0,
            "run_validity": run_validity, "failure_reason": reason,
            "ingestion_usage": {}, "retrieval_usage": {
                "send_eligible": False, "safety_reasons": [reason], "read_event_ids": []}}


def build_failure_validity(error: Exception) -> str:
    """Separate method output failures from harness/integration failures."""

    if isinstance(error, ValueError) and any(marker in str(error) for marker in (
        "truncated method response",
        "empty method response",
        "returned no parseable state memory",
        "memory method returned empty summary",
        "ingest_budget_exceeded",
    )):
        return "method_failure"
    return "integration_invalid"


def run(prepared: Path, dataset: Path, output: Path, workspace: Path, *,
        mode="offline", execute=False, resume=False, max_new_jobs=None, transport=None,
        counters=None, assume_gates_passed=False) -> dict:
    if mode not in ("offline", "live"):
        raise ValueError("unknown execution mode")
    if max_new_jobs is not None and max_new_jobs <= 0:
        raise ValueError("max-new-jobs must be positive")
    if assume_gates_passed and mode != "live":
        raise ValueError("gate override is allowed only for an explicit live development run")
    verify_file_manifest(prepared)
    verify_file_manifest(dataset)
    config = load_config(prepared / "config.snapshot.json")
    preflight = json.loads((prepared / "preflight.json").read_text(encoding="utf-8"))
    if preflight["dataset_manifest_sha256"] != file_sha256(dataset / "manifest.json"):
        raise ValueError("dataset identity changed")
    if preflight["implementation"]["digest"] != development_implementation(workspace)["digest"]:
        raise ValueError("implementation changed; prepare a new immutable suite")
    if mode == "live":
        if not execute or os.environ.get("TRACEGRAPH_DISABLE_LIVE") == "1":
            raise ValueError("real server evaluation requires explicit --execute")
        if transport is None:
            blockers = live_blockers(config, workspace) + source_status(config, workspace)
            if blockers or preflight["blockers"]:
                raise ValueError("server preflight blocked: " + str(blockers or preflight["blockers"]))
        counters = counters or {m["id"]: LocalTokenizer(m, workspace) for m in config["models"]}
        if "ama_official_embedding" in config["methods"] and config["embedding"]["id"] not in counters:
            counters[config["embedding"]["id"]] = EmbeddingTokenizer(config["embedding"], workspace)
    if output.resolve().is_relative_to(dataset.resolve()) or output.resolve().is_relative_to(prepared.resolve()):
        raise ValueError("run output must be separate from frozen inputs")
    if output.exists() != resume:
        raise ValueError("new output required, or explicitly resume an existing run")
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.with_name(output.name + ".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, str(os.getpid()).encode())
        os.fsync(descriptor)
        return _run(prepared, dataset, output, workspace, config, mode, counters, transport,
                    max_new_jobs, resume, assume_gates_passed)
    finally:
        os.close(descriptor)
        lock.unlink()


def _run(prepared, dataset, output, workspace, config, mode, counters, transport, max_new_jobs,
         resume, assume_gates_passed):
    output.mkdir(exist_ok=True)
    identity = {"prepared_hash": file_sha256(prepared / "manifest.json"), "mode": mode,
                "config_hash": stable_digest(config),
                "assume_gates_passed": bool(assume_gates_passed)}
    identity_path = output / "identity.json"
    if resume:
        if json.loads(identity_path.read_text(encoding="utf-8")) != identity:
            raise ValueError("resume identity differs")
    else:
        identity_path.write_text(json.dumps(identity), encoding="utf-8")
    done_path = output / "completed_jobs.jsonl"
    completed = load_jsonl(done_path) if done_path.exists() else []
    done = {}
    for row in completed:
        if row["job_id"] in done or stable_digest(row["result"]) != row["result_hash"]:
            raise ValueError("modified or duplicate completed job")
        done[row["job_id"]] = row
    ledger = ServerLedger(output, config, counters, set(done), transport=transport) if mode == "live" else None
    prefixes, query_rows, gold_rows = load_suite_dataset(dataset, config, workspace)
    prefix_map, query_map, gold_map = ({p.prefix_id: p for p in prefixes},
        {q.query_id: q for q in query_rows}, {g.prefix_id: g for g in gold_rows})
    trials = load_jsonl(prepared / "trials.jsonl")
    if any(t.get("answer_contract_revision") != ANSWER_CONTRACT_REVISION for t in trials):
        raise ValueError("prepared answer contract revision differs")
    if any(t.get("interaction_policy_revision") != INTERACTION_POLICY_REVISION for t in trials):
        raise ValueError("prepared interaction policy revision differs")
    if any(t.get("judge_protocol_revision") != JUDGE_PROTOCOL_REVISION for t in trials):
        raise ValueError("prepared judge protocol revision differs")
    rubrics = {r["query_id"]: r for r in load_jsonl(prepared / "rubrics.jsonl")}
    examples = load_jsonl(prepared / "rule_examples.jsonl")
    engines = {}
    added, stop, current = 0, None, None

    def finish(job_id, kind, result):
        nonlocal added
        if ledger and ledger.poisoned:
            raise StopRun("uncertain method call cannot be completed")
        row = {"job_id": job_id, "kind": kind, "result": result, "result_hash": stable_digest(result)}
        append(done_path, row)
        done[job_id] = row
        added += 1

    def pause():
        if max_new_jobs is not None and added >= max_new_jobs:
            raise StopRun("paused_at_job_boundary")

    try:
        judge_view = ledger.for_model(config["judge_model_id"]) if ledger else None
        for example in examples:
            if example["example_id"] in done:
                continue
            pause()
            finish(example["example_id"], "judge_example", evaluate_judge_example(example, judge_view))
        judged = [r["result"] for r in done.values() if r["kind"] == "judge_example"]
        judge_calibrated = judge_gate(judged, config["gates"])["pass"]
        for trial_spec in trials:
            current = dict(trial_spec)
            job_id, cell = current["episode_id"], current["cell_id"]
            if job_id in done:
                continue
            pause()
            calibration = [r["result"] for r in done.values() if r["kind"] == "episode"
                           and r["result"]["cell_id"] == cell and r["result"]["phase"] == "calibration"]
            if (current["phase"] != "calibration"
                    and not model_gate(calibration, config["gates"])["pass"]
                    and not assume_gates_passed):
                # A failed model/budget cell cannot enter its main matrix. Other models
                # still calibrate, and failed cells are explicitly reported as skipped.
                continue
            model_id = current["model_id"]
            count = counters[model_id].count if ledger else estimate_tokens
            view = ledger.for_model(model_id) if ledger else None
            if cell not in engines:
                engines[cell] = MemoryMethods(config, count, workspace, view)
            engine = engines[cell]
            prefix, query = prefix_map[current["prefix_id"]], query_map[current["query_id"]]
            method, budget = current["method_id"], current["budget"]
            current["artifact"] = failed_artifact("materialization_incomplete")
            try:
                if method in ("oracle", "oracle_evidence_only", "deletion", "irrelevant"):
                    artifact = control(prefix, query, gold_map[prefix.prefix_id], method, budget, count,
                                       ledger is not None)
                else:
                    build_id = f"build:{cell}:{prefix.prefix_id}:{method}"
                    if build_id not in done:
                        try:
                            state = engine.build(prefix, method, budget, build_id)
                        except (ValueError, ImportError, OSError, KeyError, TypeError, IndexError) as exc:
                            state = {"error": type(exc).__name__ + ": " + str(exc),
                                     "run_validity": build_failure_validity(exc)}
                        finish(build_id, "build", state)
                        pause()
                    state = done[build_id]["result"]
                    if "error" in state:
                        artifact = failed_artifact(
                            "method_build_failed: " + state["error"],
                            run_validity=state.get("run_validity", "integration_invalid"),
                        )
                    else:
                        artifact = engine.materialize(state, prefix, query, job_id)
            except (ValueError, ImportError, OSError, KeyError, TypeError, IndexError) as exc:
                artifact = failed_artifact(
                    type(exc).__name__ + ": " + str(exc),
                    run_validity=build_failure_validity(exc),
                )
            artifact.setdefault(
                "run_validity",
                "valid" if artifact["retrieval_usage"]["send_eligible"] else "method_failure",
            )
            model_spec = next(model for model in config["models"] if model["id"] == model_id)
            server_field_transport, server_tool_transport = answer_transport_flags(model_spec)
            rubric = rubrics[query.query_id]
            current.update(artifact=artifact, request_template=answer_request(
                query, artifact["records"], response_format=answer_response_format(model_spec),
                server_field_transport=server_field_transport,
                server_tool_transport=server_tool_transport, rubric=rubric))
            episode = run_episode(current, rubric, prefix, query,
                gold_map[prefix.prefix_id], view, judge_calibrated=judge_calibrated,
                token_counter=count,
                server_field_transport=server_field_transport,
                judge_protocol_repair=True)
            episode.update(cell_id=cell, model_id=model_id, budget=budget)
            episode["method_provider_calls"] = [r for r in ledger.rows if r["job_id"] == job_id
                and r["kind"] == "retrieval"] if ledger else []
            finish(job_id, "episode", episode)
        current = None
    except StopRun as exc:
        stop = str(exc)
        if current and "artifact" in current and stop != "paused_at_job_boundary":
            rows = [r for r in ledger.rows if r["job_id"] == current["episode_id"]] if ledger else []
            tools_file = output / "tool_ledger.jsonl"
            tools = [r["result"] for r in load_jsonl(tools_file)
                     if r["job_id"] == current["episode_id"]] if tools_file.exists() else []
            interrupted = interrupted_episode(current, rubrics[current["query_id"]], rows, tools, stop)
            interrupted.update(cell_id=current["cell_id"], model_id=current["model_id"], budget=current["budget"])
            append(output / "interrupted_jobs.jsonl", {"job_id": current["episode_id"], "result": interrupted})
    return write_report(output, list(done.values()), ledger.rows if ledger else [], trials,
                        config=config, mode=mode, stop=stop,
                        assume_gates_passed=assume_gates_passed)
