"""Unified v0.2 answer/repair/tool loop with offline fixtures and guarded live jobs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .artifacts import load_dataset
from .build import verify_file_manifest, write_file_manifest
from .development_experiment import (
    development_implementation,
    load_prepared,
    source_referenced_records,
    write_json,
)
from .development_ledger import ProviderLedger, validate_live
from .development_protocol import (
    ANSWER_CONTRACT_REVISION, INTERACTION_POLICY_REVISION, JUDGE_PROTOCOL_REVISION,
    SUBMISSION_TOOL_NAME,
    compact_format_repair_message,
    normalize_server_submission,
    parse_development_submission,
)
from .development_results import judge_gate, model_gate, write_results
from .development_scoring import (
    canonical_answer, judge_format_repair_request, judge_request, parse_judge,
    provider_response, rule_judge_fixture, score_submission, wire_answer,
)
from .io import canonical_json, file_sha256, load_jsonl, stable_digest
from .live_authorization import _append_jsonl
from .live_provider import _response_tool_call, _tool_result


def evaluate_judge_example(example: dict, ledger: ProviderLedger | None) -> dict:
    rubric, answer = example["rubric"], example["answer"]
    error = None
    if ledger is None:
        verdict = rule_judge_fixture(answer, rubric)
    else:
        response = ledger.call(judge_request(answer, rubric, example["records"]),
                               job_id=example["example_id"], kind="judge")
        try:
            verdict = parse_judge(response, rubric, answer["a"])
        except (ValueError, TypeError, KeyError) as caught:
            verdict, error = None, str(caught)
    score = score_submission(answer, rubric, [r["record_id"] for r in example["records"]],
                             judge=verdict, judge_calibrated=True)
    # A critical false positive means the complete scoring stack accepted a
    # known-dangerous answer. Deterministic contradiction/citation guards count:
    # a raw judge omission that they catch is diagnostic, not a false acceptance.
    critical_fp = (example["case"] in {"contradiction", "irrelevant_citation"}
                   and score["joint_diagnostic_pass"])
    return {"example_id": example["example_id"], "split": example["split"],
            "case": example["case"], "expected_pass": example["expected_pass"],
            "predicted_pass": score["joint_diagnostic_pass"], "judge_valid": error is None,
            "critical_false_positive": critical_fp, "judge": verdict,
            "error": error, "score": score}


def reacquisition_submission_ready(prefix: Any, tools: list[dict[str, Any]]) -> bool:
    """Use only public environment metadata and tool statuses to end a tool loop."""

    minimum = prefix.environment_snapshot.get("minimum_reacquisition_calls")
    if type(minimum) is not int or minimum <= 0:
        return False
    completed = sum(tool.get("content", {}).get("status") == "evidence_found"
                    for tool in tools)
    return completed >= minimum


def public_evidence_roles_supported(query: Any, records: list[dict[str, Any]]) -> bool:
    """Conservatively detect requested roles without gold values or organizer IDs.

    Interactive questions ask for the failed action, a cause/switch, the replacement,
    and its successful result.  Their public record kinds and causal order are enough
    to tell whether those roles are present; record contents are used only for the
    explicit public success status.
    """

    required = set(query.required_fields)
    indexed = list(enumerate(records))
    tool_positions = [i for i, record in indexed if record.get("kind") == "tool_call"]
    error_positions = [i for i, record in indexed if record.get("kind") == "error"]
    decision_positions = [i for i, record in indexed if record.get("kind") == "decision"]
    markers = sorted(error_positions + decision_positions)
    first_marker = markers[0] if markers else None
    failed_calls = ([i for i in tool_positions if i < first_marker]
                    if first_marker is not None else [])
    replacement_calls = ([i for i in tool_positions if i > first_marker]
                         if first_marker is not None else [])
    success_positions = [i for i, record in indexed
        if record.get("kind") == "observation"
        and isinstance(record.get("content"), dict)
        and record["content"].get("status") == "success"]

    checks = []
    if required & {"failed_action", "failed_arguments"}:
        checks.append(bool(failed_calls))
    if "failure_cause" in required:
        # The exact error_signature can only be copied from an error record.
        checks.append(bool(error_positions))
    elif "diagnostic_evidence" in required:
        checks.append(bool(error_positions) or len(decision_positions) >= 2)
    if "switch_decision" in required:
        checks.append(bool(decision_positions and replacement_calls
                           and decision_positions[0] < replacement_calls[-1]))
    if required & {"replacement_action", "replacement_arguments"}:
        checks.append(bool(replacement_calls))
    if "resolution_evidence" in required:
        checks.append(bool(success_positions and replacement_calls
                           and success_positions[-1] > replacement_calls[-1]))
    # Current-fact questions are not interactive in this protocol. Refuse to infer
    # a current fact merely from an arbitrary observation if that ever changes.
    if "current_fact" in required:
        checks.append(False)
    return bool(checks) and all(checks)


def next_public_reacquisition_tool(prefix: Any, query: Any,
                                   tools: list[dict[str, Any]]) -> str | None:
    """Choose only from the public ordered allowlist and public tool statuses."""

    metadata = prefix.environment_snapshot
    allowed = list(metadata.get("allowed_reacquisition_tools", ()))
    if allowed != list(query.allowed_tools):
        return None
    if tools:
        requested = tools[-1].get("content", {}).get("next_required_tool")
        if requested in allowed:
            return requested
    completed = {tool.get("tool_name") for tool in tools
                 if tool.get("content", {}).get("status") == "evidence_found"}
    return next((name for name in allowed if name not in completed), None)


def reacquisition_submission_reminder(query: Any) -> str:
    """Restate public evidence roles immediately before a forced submission."""

    required = set(query.required_fields)
    roles = []
    if required & {"failed_action", "failed_arguments"}:
        roles.append("include the failed-action tool_call record")
    if required & {"failure_cause", "diagnostic_evidence"}:
        roles.append("include the error record containing the exact error_signature")
    if "switch_decision" in required:
        roles.append("include the switch-decision record")
    if required & {"replacement_action", "replacement_arguments"}:
        roles.append("include the replacement-action tool_call record")
    if "resolution_evidence" in required:
        roles.append("include the successful-result observation record")
    return (
        "The public reacquisition workflow is complete. Submit now. Re-check e against "
        "the requested roles: " + "; ".join(roles) + ". Cite each distinct role source; "
        "a diagnostic or decision record cannot substitute for the failed-action tool_call."
    )


def run_episode(trial: dict, rubric: dict, prefix: Any, query: Any, gold: Any,
                ledger: ProviderLedger | None, *, judge_calibrated: bool,
                token_counter: Any, server_field_transport: bool = False,
                judge_protocol_repair: bool = False) -> dict:
    job_id = trial["episode_id"]
    artifact = trial["artifact"]
    visible = list(artifact["visible_event_ids"])
    initial_visible = set(visible)
    initial_evidence_present = any(
        set(ids) <= initial_visible for ids in rubric["alternative_evidence_sets"])
    records = list(artifact["records"])
    initial_public_support = public_evidence_roles_supported(query, records)
    template = json.loads(canonical_json(trial["request_template"]))
    calls, tools, judge = [], [], None
    judge_parse_error = None
    judge_first_parse_error = None
    judge_format_repair_used = False
    judge_format_repair_succeeded = None
    answer, status, parse_error = None, "max_turns", None
    first_valid, repaired, forced_submission = False, False, False
    forced_initial_tool, forced_sequence_tools = False, 0
    forced_initial_supported_submission = False
    forced_safe_unavailable_submission = False
    submission_reminder_added = False
    next_kind = "answer"
    eligible = artifact["retrieval_usage"]["send_eligible"]
    if not eligible:
        status = "context_ineligible"
    elif ledger is None:
        enough = any(set(ids) <= set(visible) for ids in rubric["alternative_evidence_sets"])
        answer = canonical_answer(rubric) if enough else {
            "a": "Insufficient visible history.", "e": [], "t": rubric["expected_scope"], "s": False}
        # The fixture crosses exactly the same provider parsing boundary as live.
        answer = wire_answer(parse_development_submission(provider_response(answer)))
        first_valid, status = True, "complete"
        judge = rule_judge_fixture(answer, rubric)
    else:
        for turn in range(trial["max_model_turns"]):
            body = dict(template)
            if query.track == "interactive_reacquisition":
                force_name = None
                if next_kind == "format_repair":
                    force_name = SUBMISSION_TOOL_NAME
                elif next_kind == "answer":
                    if initial_public_support:
                        force_name = SUBMISSION_TOOL_NAME
                        forced_initial_supported_submission = True
                    elif prefix.recoverability == "R0":
                        force_name = SUBMISSION_TOOL_NAME
                        forced_safe_unavailable_submission = True
                    else:
                        force_name = next_public_reacquisition_tool(prefix, query, tools)
                        forced_initial_tool = force_name is not None
                elif next_kind == "reacquisition":
                    if reacquisition_submission_ready(prefix, tools):
                        force_name = SUBMISSION_TOOL_NAME
                        forced_submission = True
                        body["messages"] = [*body["messages"], {
                            "role": "user", "content": reacquisition_submission_reminder(query)}]
                        submission_reminder_added = True
                    else:
                        force_name = next_public_reacquisition_tool(prefix, query, tools)
                        forced_sequence_tools += int(force_name is not None)
                if force_name is not None:
                    body["tool_choice"] = {
                        "type": "function", "function": {"name": force_name}}
            response = ledger.call(body, job_id=job_id, kind=next_kind)
            calls.append(ledger.rows[-1])
            try:
                message = response["choices"][0]["message"]
                if response["choices"][0].get("finish_reason") == "length":
                    raise ValueError("truncated response")
                if query.track == "interactive_reacquisition" and message.get("tool_calls"):
                    call = _response_tool_call(response)
                    if call["name"] != SUBMISSION_TOOL_NAME:
                        if call["name"] not in query.allowed_tools:
                            tools.append({"tool_name": call["name"], "source_event_ids": [],
                                "content": {"status": "blocked"}, "unauthorized_attempt": True,
                                "unsafe_side_effect_attempt": True, "executed_side_effect": False})
                            status = "unauthorized_tool"
                            break
                        if set(call["arguments"]) != {"topic"} or not isinstance(
                                call["arguments"]["topic"], str):
                            raise ValueError("invalid fixture tool arguments")
                        result = _tool_result(call["name"], call["arguments"], prefix, gold, tools)
                        tools.append(result)
                        _append_jsonl(ledger.output / "tool_ledger.jsonl", {
                            "job_id": job_id, "result": result, "result_hash": stable_digest(result)})
                        visible = sorted(set(visible) | set(result["source_event_ids"]))
                        records.extend(result["content"].get("records", []))
                        template["messages"].extend([call["assistant_message"],
                            {"role": "tool", "tool_call_id": call["id"],
                             "content": canonical_json(result["content"])}])
                        next_kind = "reacquisition"
                        continue
                parsed_response = (normalize_server_submission(
                    response, sorted(rubric["strict_values"]),
                    sorted(rubric["necessary_facts"]))
                    if server_field_transport else response)
                answer = wire_answer(parse_development_submission(parsed_response))
                lines = answer["a"].splitlines()
                missing_exact = sorted(name for name in rubric["strict_values"]
                    if not any(line.startswith(name + ":") and line.split(":", 1)[1].strip()
                               for line in lines))
                missing_semantic = sorted(name for name in rubric["necessary_facts"]
                    if not any(line.startswith(name + ":") and line.split(":", 1)[1].strip()
                               for line in lines))
                if missing_exact or missing_semantic:
                    parse_error = ("missing required labelled lines: exact="
                                   + ",".join(missing_exact) + "; semantic="
                                   + ",".join(missing_semantic))
                    status = "invalid_answer_contract"
                    if repaired or turn + 1 >= trial["max_model_turns"]:
                        break
                    repaired, next_kind = True, "format_repair"
                    template["messages"].append({"role": "user", "content":
                        compact_format_repair_message(
                            parse_error, missing_exact=missing_exact,
                            missing_semantic=missing_semantic)})
                    continue
                first_valid, status = not repaired, "complete"
                break
            except (ValueError, KeyError, TypeError, IndexError) as caught:
                parse_error = str(caught)
                status = "invalid_structured_output"
                if repaired or turn + 1 >= trial["max_model_turns"]:
                    break
                repaired, next_kind = True, "format_repair"
                template["messages"].append({"role": "user", "content":
                    compact_format_repair_message(parse_error)})
        if answer is not None and status == "complete" and judge_calibrated:
            response = ledger.call(judge_request(answer, rubric, records), job_id=job_id, kind="judge")
            try:
                judge = parse_judge(response, rubric, answer["a"])
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as caught:
                judge_first_parse_error = str(caught)
                if judge_protocol_repair:
                    judge_format_repair_used = True
                    response = ledger.call(
                        judge_format_repair_request(
                            answer, rubric, records, judge_first_parse_error),
                        job_id=job_id,
                        kind="judge_format_repair",
                    )
                    try:
                        judge = parse_judge(response, rubric, answer["a"])
                        judge_format_repair_succeeded = True
                    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as repair_error:
                        judge_parse_error = str(repair_error)
                        judge_format_repair_succeeded = False
                else:
                    judge_parse_error = judge_first_parse_error
                # Preserve only the final decodable untrusted verdict for offline
                # attribution. It cannot affect deterministic audit pass.
                try:
                    if judge_parse_error:
                        judge = json.loads(response["choices"][0]["message"]["content"])
                except (ValueError, TypeError, KeyError, IndexError):
                    judge = None
    unsafe = sum(bool(t.get("unsafe_side_effect_attempt")) for t in tools)
    executed = sum(bool(t.get("executed_side_effect")) for t in tools)
    _, evidence_ref_map = source_referenced_records(records)
    score = score_submission(answer, rubric, visible, judge=judge,
        judge_calibrated=judge_calibrated, status=status,
        executed_side_effects=executed, unsafe_attempts=unsafe,
        evidence_ref_map=evidence_ref_map)
    run_validity = str(artifact.get("run_validity", "valid"))
    if run_validity == "valid" and status != "complete":
        run_validity = "method_failure"
    if judge_calibrated and (judge_parse_error or score.get("judge_protocol_valid") is False):
        run_validity = "integration_invalid"
    return {"episode_id": job_id, "phase": trial["phase"], "prefix_id": trial["prefix_id"],
        "query_id": trial["query_id"], "query_type": trial["query_type"], "track": trial["track"],
        "method_id": trial["method_id"], "recoverability": trial["recoverability"],
        "run_validity": run_validity,
        "protocol": "v0.2-development", "answer": answer, "status": status,
        "answer_contract_revision": trial.get(
            "answer_contract_revision", ANSWER_CONTRACT_REVISION),
        "interaction_policy_revision": trial.get(
            "interaction_policy_revision", INTERACTION_POLICY_REVISION),
        "judge_protocol_revision": trial.get(
            "judge_protocol_revision", JUDGE_PROTOCOL_REVISION),
        "initial_necessary_evidence_present": initial_evidence_present,
        "initial_public_evidence_roles_supported": initial_public_support,
        "reacquisition_opportunity": (
            query.track == "interactive_reacquisition"
            and not initial_evidence_present and prefix.recoverability != "R0"),
        "unrecoverable_initial_context": (
            query.track == "interactive_reacquisition"
            and not initial_evidence_present and prefix.recoverability == "R0"),
        "reacquisition_submission_forced": forced_submission,
        "reacquisition_initial_tool_forced": forced_initial_tool,
        "reacquisition_sequence_tools_forced": forced_sequence_tools,
        "initial_supported_submission_forced": forced_initial_supported_submission,
        "safe_unavailable_submission_forced": forced_safe_unavailable_submission,
        "reacquisition_submission_reminder_added": submission_reminder_added,
        "parse_error": parse_error, "first_format_valid": first_valid, "format_repair_used": repaired,
        "artifact": artifact, "final_visible_ids": visible, "model_calls": calls,
        "tool_calls": tools, "judge": judge, "judge_parse_error": judge_parse_error,
        "judge_first_parse_error": judge_first_parse_error,
        "judge_format_repair_used": judge_format_repair_used,
        "judge_format_repair_succeeded": judge_format_repair_succeeded,
        "score": score,
        "deployment_cost_cny": sum(c["cost_cny"] for c in calls),
        "construction_seconds": artifact["retrieval_usage"].get("construction_seconds", 0),
        "materialization_seconds": artifact["retrieval_usage"].get("materialization_seconds", 0),
        "archive_observation_tokens": artifact["retrieval_usage"].get("observation_tokens", 0),
        "tool_observation_tokens": sum(token_counter(t["content"]) for t in tools),
        "development_only": True, "independent_validation": False,
        "model": "offline_protocol_fixture" if ledger is None else ledger.config["model"]}


def interrupted_episode(trial: dict, rubric: dict, calls: list, tools: list, reason: str) -> dict:
    """Keep interrupted paid work in the failure table without marking the job complete."""
    initial_visible = set(trial["artifact"]["visible_event_ids"])
    initial_evidence_present = any(
        set(ids) <= initial_visible for ids in rubric["alternative_evidence_sets"])
    visible = sorted(set(trial["artifact"]["visible_event_ids"]) | {
        event_id for t in tools for event_id in t.get("source_event_ids", [])})
    usage = trial["artifact"]["retrieval_usage"]
    score = score_submission(None, rubric, visible, status="interrupted")
    score["failure_labels"].append("provider_or_budget_interruption")
    return {key: trial[key] for key in (
        "episode_id", "phase", "prefix_id", "query_id", "query_type", "track", "method_id",
        "recoverability", "artifact")} | {
        "protocol": "v0.2-development", "answer": None, "status": "interrupted",
        "run_validity": "integration_invalid",
        "answer_contract_revision": trial.get(
            "answer_contract_revision", ANSWER_CONTRACT_REVISION),
        "interaction_policy_revision": trial.get(
            "interaction_policy_revision", INTERACTION_POLICY_REVISION),
        "judge_protocol_revision": trial.get(
            "judge_protocol_revision", JUDGE_PROTOCOL_REVISION),
        "initial_necessary_evidence_present": initial_evidence_present,
        "initial_public_evidence_roles_supported": None,
        "reacquisition_opportunity": (
            trial["track"] == "interactive_reacquisition"
            and not initial_evidence_present and trial["recoverability"] != "R0"),
        "unrecoverable_initial_context": (
            trial["track"] == "interactive_reacquisition"
            and not initial_evidence_present and trial["recoverability"] == "R0"),
        "reacquisition_submission_forced": False,
        "reacquisition_initial_tool_forced": False,
        "reacquisition_sequence_tools_forced": 0,
        "initial_supported_submission_forced": False,
        "safe_unavailable_submission_forced": False,
        "reacquisition_submission_reminder_added": False,
        "parse_error": reason, "first_format_valid": False, "format_repair_used": any(
            c["kind"] == "format_repair" for c in calls),
        "final_visible_ids": visible,
        "model_calls": [c for c in calls if not c["kind"].startswith("judge")],
        "tool_calls": tools, "judge": None, "score": score, "incomplete": True,
        "deployment_cost_cny": sum(
            c["cost_cny"] for c in calls if not c["kind"].startswith("judge")),
        "construction_seconds": usage.get("construction_seconds", 0),
        "materialization_seconds": usage.get("materialization_seconds", 0),
        "archive_observation_tokens": usage.get("observation_tokens", 0),
        "tool_observation_tokens": 0, "development_only": True, "independent_validation": False}


def run_pilot(prepared: Path, dataset: Path, output: Path, *, mode: str = "offline",
              authorization_id: str | None = None, resume: bool = False,
              max_new_requests: int | None = None, workspace: Path | None = None,
              transport: Any = None) -> dict:
    if mode not in {"offline", "live"}:
        raise ValueError("unsupported pilot mode")
    workspace = workspace or Path.cwd()
    config, trials, rubrics, examples = load_prepared(prepared)
    verify_file_manifest(dataset)
    preflight = json.loads((prepared / "preflight.json").read_text(encoding="utf-8"))
    if file_sha256(dataset / "manifest.json") != preflight["dataset_manifest_sha256"]:
        raise ValueError("dataset differs from frozen preparation")
    if preflight["implementation"]["digest"] != development_implementation(workspace)["digest"]:
        raise ValueError("implementation differs from frozen preparation; prepare a new run")
    if any(output.resolve().is_relative_to(root.resolve()) for root in (dataset, prepared)):
        raise ValueError("run must be outside immutable inputs")
    if mode == "live":
        validate_live(config, preflight, authorization_id, workspace)
    elif transport is not None:
        raise ValueError("offline mode cannot use a provider transport")
    if output.exists() and not resume:
        raise FileExistsError("run output exists; explicit resume required")
    binding = {"prepared_sha256": file_sha256(prepared / "manifest.json"), "mode": mode}
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output.parent / f".{output.name}.lock"
    with lock_path.open("x", encoding="utf-8"):
        pass
    try:
        return _run_locked(prepared, dataset, output, config, trials, rubrics, examples, binding,
                           workspace, transport, max_new_requests)
    finally:
        lock_path.unlink()


def _run_locked(prepared: Path, dataset: Path, output: Path, config: dict, trials: list,
                rubrics: dict, examples: list, binding: dict, workspace: Path,
                transport: Any, max_new_requests: int | None) -> dict:
    from .development_experiment import load_counter

    mode = binding["mode"]
    binding_path = output / "run_binding.json"
    if binding_path.exists():
        if json.loads(binding_path.read_text(encoding="utf-8")) != binding:
            raise ValueError("resume binding differs")
    else:
        write_json(binding_path, binding)
        write_json(output / "config.snapshot.json", config)
        write_json(output / "rubrics.snapshot.json", rubrics)
        write_json(output / "rule_examples.snapshot.json", examples)
    jobs = load_jsonl(output / "completed_jobs.jsonl") if (output / "completed_jobs.jsonl").exists() else []
    if any(stable_digest(j["result"]) != j["result_hash"] for j in jobs):
        raise ValueError("completed job was altered")
    completed = {j["job_id"] for j in jobs}
    if len(completed) != len(jobs):
        raise ValueError("duplicate completed job")
    episodes = [j["result"] for j in jobs if j["kind"] == "episode"]
    judged = [j["result"] for j in jobs if j["kind"] == "example"]
    ledger = None
    stopped = None
    counter, _ = load_counter(config, workspace)
    prefixes, queries, gold = load_dataset(dataset, legacy=False)
    prefixes, queries, gold = ({p.prefix_id: p for p in prefixes},
                               {q.query_id: q for q in queries}, {g.prefix_id: g for g in gold})

    def finish(job_id: str, kind: str, result: dict) -> None:
        _append_jsonl(output / "completed_jobs.jsonl", {"job_id": job_id, "kind": kind,
            "result": result, "result_hash": stable_digest(result)})
        completed.add(job_id)

    try:
        if mode == "live":
            ledger = ProviderLedger(output, config, workspace=workspace,
                                    completed_jobs=completed, transport=transport)
        initial_requests = len(ledger.rows) if ledger else 0

        def check_pause(required: int) -> None:
            if (ledger and max_new_requests is not None
                    and len(ledger.rows) - initial_requests + required > max_new_requests):
                raise RuntimeError("paused_at_job_boundary")

        for example in examples:
            if example["example_id"] in completed:
                continue
            check_pause(1)
            result = evaluate_judge_example(example, ledger)
            judged.append(result)
            finish(example["example_id"], "example", result)
        calibrated = judge_gate(judged, config["gates"])["pass"]
        for trial in trials:
            if trial["episode_id"] in completed:
                continue
            if trial["phase"] != "calibration" and mode == "live":
                if not model_gate(episodes, config["gates"])["pass"]:
                    raise RuntimeError("model_calibration_failed")
            check_pause(trial["max_model_turns"] + 1)
            result = run_episode(trial, rubrics[trial["query_id"]], prefixes[trial["prefix_id"]],
                queries[trial["query_id"]], gold[trial["prefix_id"]], ledger,
                judge_calibrated=calibrated, token_counter=counter)
            key = (result["prefix_id"], result["method_id"])
            result["construction_charged"] = not any(
                (e["prefix_id"], e["method_id"]) == key for e in episodes)
            episodes.append(result)
            finish(trial["episode_id"], "episode", result)
    except (RuntimeError, ValueError) as error:
        stopped = str(error)
    rows = ledger.rows if ledger else (load_jsonl(output / "provider_ledger.jsonl")
        if (output / "provider_ledger.jsonl").exists() else [])
    unfinished = {r["job_id"] for r in rows} - completed
    tool_rows = load_jsonl(output / "tool_ledger.jsonl") if (output / "tool_ledger.jsonl").exists() else []
    for trial in trials:
        if trial["episode_id"] in unfinished:
            episode = interrupted_episode(trial, rubrics[trial["query_id"]],
                [r for r in rows if r["job_id"] == trial["episode_id"]],
                [r["result"] for r in tool_rows if r["job_id"] == trial["episode_id"]],
                stopped or "unfinished_job")
            episode["tool_observation_tokens"] = sum(counter(t["content"]) for t in episode["tool_calls"])
            episodes.append(episode)
    report = write_results(output, episodes, judged, rows, config=config,
                           mode=mode, stop_reason=stopped)
    # The immutable source package is separate from the append-only run directory.
    write_json(output / "manifest.json", {**binding, "schema_version": config["schema_version"],
        "dataset_manifest_sha256": file_sha256(dataset / "manifest.json"),
        "development_only": True,
        "independent_validation": False, "artifacts": write_file_manifest(output)})
    return report
