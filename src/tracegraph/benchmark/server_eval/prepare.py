"""Freeze model/method/budget matrices before any method can see an outcome."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from ..compression_audit.build import verify_file_manifest, write_file_manifest
from ..compression_audit.development_experiment import (
    QUERY_TYPES, answer_request, challenge_examples, development_implementation, event_record,
    select_population, write_json, write_rows,
)
from ..compression_audit.development_protocol import (
    ANSWER_CONTRACT_REVISION, INTERACTION_POLICY_REVISION, JUDGE_PROTOCOL_REVISION,
    compact_format_repair_message,
)
from ..compression_audit.development_scoring import (
    assert_canonical_rubric_scores, judge_format_repair_request, judge_request,
)
from .rubrics import import_rubrics
from ..compression_audit.io import file_sha256, load_jsonl, stable_digest
from .config import (
    LocalTokenizer, answer_response_format, answer_transport_flags, live_blockers, load_config,
)
from .external import source_status
from .candidate import NAME as CANDIDATE
from .data_workflow import load_suite_dataset


def prepare(config_path: Path, dataset: Path, output: Path, workspace: Path) -> dict:
    config = load_config(config_path)
    if output.exists() or output.resolve().is_relative_to(dataset.resolve()):
        raise ValueError("use a new output directory outside the frozen dataset")
    verify_file_manifest(dataset)
    prefixes, query_rows, gold_rows = load_suite_dataset(dataset, config, workspace)
    by_id = {p.prefix_id: p for p in prefixes}
    queries = {(q.prefix_id, q.query_type): q for q in query_rows}
    gold = {g.prefix_id: g for g in gold_rows}
    population = select_population(list(prefixes), config["seed"])
    exclusions = set(config["data"].get("calibration_exclude_prefix_ids", []))
    explicit_calibration = config["data"].get("calibration_prefix_ids", [])
    if explicit_calibration:
        unknown_calibration = set(explicit_calibration) - set(by_id)
        if unknown_calibration:
            raise ValueError("explicit calibration contains unknown prefix IDs")
        if (any(by_id[prefix_id].split != "dev" for prefix_id in explicit_calibration)
                or Counter(by_id[prefix_id].recoverability
                           for prefix_id in explicit_calibration)
                != Counter({"R0": 2, "R1": 2, "R2": 2, "R3": 2})):
            raise ValueError(
                "explicit calibration requires two development prefixes per recoverability level"
            )
        population["calibration"] = list(explicit_calibration)
    if set(population["calibration"]) & exclusions:
        raise ValueError("fresh calibration overlaps an explicitly exposed prefix")
    calibration_only = config["data"].get("calibration_only", False)
    if calibration_only:
        population["main"] = []
        population["diagnostic"] = []
    ids = config["data"].get("prefix_ids", [])
    if config["data"]["split"] == "dev" and ids:
        # A development-only canary may freeze an explicit, already exposed subset.
        # Its identities remain in the config snapshot and prepared manifest; this
        # mechanism cannot be used to claim held-out or independent validation.
        unknown = set(ids) - set(by_id)
        if unknown:
            raise ValueError("development canary contains unknown prefix IDs")
        if any(by_id[prefix_id].split != "dev" for prefix_id in ids):
            raise ValueError("development canary may contain development prefixes only")
        if set(ids) & set(population["calibration"]):
            raise ValueError("development canary overlaps calibration population")
        population["main"] = ids
        population["diagnostic"] = ids[:8]
    elif config["data"]["split"] != "dev":
        # Explicit held-out population only. Existing dev observations cannot become
        # independent simply by assigning another split label.
        exposure_file = config["data"].get("exposure_manifest")
        if not ids or len(set(ids)) != len(ids) or not exposure_file:
            raise ValueError("held-out runs require frozen prefix IDs and prior exposure manifest")
        exposed = load_jsonl(workspace / exposure_file)
        fingerprints = {r["public_content_hash"] for r in exposed}
        for prefix_id in ids:
            prefix = by_id[prefix_id]
            fingerprint = stable_digest([dict(e["content"]) if isinstance(e["content"], dict)
                                         else e["content"] for e in prefix.events])
            if (prefix.split != config["data"]["split"] or fingerprint in fingerprints
                    or prefix_id in population["calibration"]):
                raise ValueError("held-out population overlaps prior exposure or calibration")
        population["main"] = ids
        population["diagnostic"] = ids[:8]
    trials = []
    capability_only = config.get("capability_attribution_only", False)
    oracle_gate_only = config.get("oracle_gate_only", False)
    oracle_context_mode = config.get("oracle_context_mode", "recent_plus_gold")
    diagnostic_oracle = (
        "oracle_evidence_only" if oracle_context_mode == "evidence_only" else "oracle"
    )
    for model in config["models"]:
        for budget in config["history_budgets"]:
            cell = f"{model['id']}-b{budget}"
            specs = []
            for prefix in population["calibration"]:
                specs += [("calibration", prefix, q, "full_history") for q in QUERY_TYPES]
                specs += [("calibration", prefix, q, "oracle") for q in QUERY_TYPES[:2]]
            for prefix in population["main"]:
                required_audit = QUERY_TYPES[:2]
                missing_audit = [q for q in required_audit if (prefix, q) not in queries]
                if missing_audit:
                    raise ValueError(
                        f"main prefix lacks required audit queries: {prefix}/{missing_audit}"
                    )
                available_queries = [q for q in QUERY_TYPES if (prefix, q) in queries]
                if capability_only:
                    if not oracle_gate_only:
                        specs += [("main", prefix, "audit_chain", "full_history")]
                else:
                    specs += [("main", prefix, q, m)
                              for q in available_queries for m in config["methods"]]
            for prefix in population["diagnostic"]:
                if capability_only:
                    specs += [("diagnostic", prefix, "audit_chain", diagnostic_oracle)]
                else:
                    specs += [("diagnostic", prefix, "audit_chain", m)
                              for m in ("deletion", "oracle", "irrelevant")]
                if config["interactive"] and not capability_only:
                    specs += [("interactive", prefix, "interactive_reacquisition", m)
                              for m in config["methods"]]
            for phase, prefix_id, qt, method in specs:
                query = queries[(prefix_id, qt)]
                trials.append({"episode_id": f"{cell}:{phase}:{query.query_id}:{method}",
                    "cell_id": cell, "model_id": model["id"], "budget": budget,
                    "phase": phase, "prefix_id": prefix_id, "query_id": query.query_id,
                    "query_type": qt, "track": query.track, "method_id": method,
                    "recoverability": by_id[prefix_id].recoverability,
                    "answer_contract_revision": ANSWER_CONTRACT_REVISION,
                    "interaction_policy_revision": INTERACTION_POLICY_REVISION,
                    "judge_protocol_revision": JUDGE_PROTOCOL_REVISION,
                    "max_model_turns": 4 if phase == "interactive" else 2})
    selected_queries = {t["query_id"] for t in trials}
    rubric_spec = config["data"].get("rubrics")
    rubric_path = workspace / rubric_spec["path"] if rubric_spec else None
    if rubric_path and file_sha256(rubric_path) != rubric_spec["sha256"]:
        raise ValueError("external rubric file changed")
    imported = import_rubrics(rubric_path, query_rows, by_id, gold)
    rubric_by_query = {rubric["query_id"]: rubric for rubric in imported.values()}
    rubrics = [imported[q.query_id] for q in query_rows if q.query_id in selected_queries]
    query_by_id = {q.query_id: q for q in query_rows}
    for rubric in rubrics:
        prefix = by_id[query_by_id[rubric["query_id"]].prefix_id]
        assert_canonical_rubric_scores(rubric, [e["event_id"] for e in prefix.events])
    examples = challenge_examples(population["calibration"], queries, gold, by_id)
    build_keys = {(t["cell_id"], t["prefix_id"], t["method_id"]) for t in trials
                  if t["method_id"] in ("rolling_summary", "acon_official", "ama_official_bm25", "ama_official_embedding", CANDIDATE)}
    retrieve_count = sum(t["method_id"] in ("ama_official_bm25", "ama_official_embedding") for t in trials)
    request_bound = (80 + sum(t["max_model_turns"] + 2 for t in trials)
                     + len(build_keys) * config["limits"]["build_calls_per_prefix"]
                     + retrieve_count * config["limits"]["retrieve_calls_per_query"])
    embedding_bound = (sum(min(len(by_id[p].events), config["limits"].get("embedding_calls_per_prefix", 512))
        for _, p, m in build_keys if m == "ama_official_embedding")
        + sum(t["method_id"] == "ama_official_embedding" for t in trials))
    request_bound += embedding_bound
    blockers = live_blockers(config, workspace) + source_status(config, workspace)
    full_history_context_checks = []
    if "full_history" in config["methods"]:
        for model in config["models"]:
            try:
                counter = LocalTokenizer(model, workspace)
            except (ValueError, OSError, ImportError, KeyError):
                continue
            for trial in trials:
                if trial["phase"] != "main" or trial["method_id"] != "full_history":
                    continue
                prefix = by_id[trial["prefix_id"]]
                query = query_by_id[trial["query_id"]]
                rubric = rubric_by_query[query.query_id]
                server_field_transport, server_tool_transport = answer_transport_flags(model)
                body = answer_request(
                    query,
                    [event_record(event) for event in prefix.events],
                    response_format=answer_response_format(model),
                    server_field_transport=server_field_transport,
                    server_tool_transport=server_tool_transport,
                    rubric=rubric,
                )
                input_tokens = counter.request_count(body)
                repair_messages = [
                    compact_format_repair_message(
                        "missing required labelled lines",
                        missing_exact=sorted(rubric["strict_values"]),
                        missing_semantic=sorted(rubric["necessary_facts"]),
                    ),
                    compact_format_repair_message(
                        "structured response was truncated with finish_reason=length"
                    ),
                ]
                repair_input_tokens = max(counter.request_count({
                    **body,
                    "messages": [*body["messages"], {"role": "user", "content": message}],
                }) for message in repair_messages)
                max_input_tokens = max(input_tokens, repair_input_tokens)
                records = [event_record(event) for event in prefix.events]
                judge_empty_answer_input_tokens = counter.request_count(judge_request(
                    {"a": "", "e": [], "t": rubric["expected_scope"], "s": False},
                    rubric,
                    records,
                ))
                judge_repair_empty_answer_input_tokens = counter.request_count(
                    judge_format_repair_request(
                        {"a": "", "e": [], "t": rubric["expected_scope"], "s": False},
                        rubric,
                        records,
                        "judge response violates the strict output contract",
                    )
                )
                # The answer may consume its full output allowance before being
                # JSON-serialized into the judge prompt. Reserve twice that many
                # tokens for escaping/transport expansion, plus the judge output.
                judge_answer_serialization_reserve_tokens = 2 * model["max_output_tokens"]
                judge_total_tokens = (
                    max(judge_empty_answer_input_tokens,
                        judge_repair_empty_answer_input_tokens)
                    + judge_answer_serialization_reserve_tokens
                    + model["max_output_tokens"]
                )
                answer_total_tokens = max_input_tokens + model["max_output_tokens"]
                total_tokens = max(answer_total_tokens, judge_total_tokens)
                full_history_context_checks.append({
                    "model_id": model["id"],
                    "prefix_id": prefix.prefix_id,
                    "query_id": query.query_id,
                    "query_type": query.query_type,
                    "input_tokens": input_tokens,
                    "repair_input_tokens": repair_input_tokens,
                    "max_input_tokens": max_input_tokens,
                    "answer_total_tokens": answer_total_tokens,
                    "judge_empty_answer_input_tokens": judge_empty_answer_input_tokens,
                    "judge_repair_empty_answer_input_tokens": (
                        judge_repair_empty_answer_input_tokens
                    ),
                    "judge_answer_serialization_reserve_tokens": (
                        judge_answer_serialization_reserve_tokens
                    ),
                    "judge_total_tokens": judge_total_tokens,
                    "reserved_output_tokens": model["max_output_tokens"],
                    "total_tokens": total_tokens,
                    "context_window": model["context_window"],
                    "fits": total_tokens <= model["context_window"],
                })
    overflow_count = sum(not row["fits"] for row in full_history_context_checks)
    if overflow_count:
        blockers.append(f"full_history_context_overflow:{overflow_count}")
    if request_bound > config["limits"]["request_limit"]:
        blockers.append("request_bound_exceeds_suite_limit")
    if request_bound * 2048 > config["limits"]["output_token_limit"]:
        blockers.append("output_bound_exceeds_suite_limit")
    max_context = max(m["context_window"] for m in config["models"])
    if request_bound * max_context > config["limits"]["input_token_limit"]:
        blockers.append("input_bound_exceeds_suite_limit")
    summary = {"schema_version": config["schema_version"], "episode_count": len(trials),
        "phase_counts": dict(Counter(t["phase"] for t in trials)),
        "main_query_type_counts": dict(Counter(
            t["query_type"] for t in trials
            if t["phase"] == "main" and t["method_id"] == config["methods"][0]
        )),
        "full_history_context_check_count": len(full_history_context_checks),
        "full_history_context_overflow_count": overflow_count,
        "cell_count": len(config["models"]) * len(config["history_budgets"]),
        "embedding_request_upper_bound": embedding_bound, "request_upper_bound": request_bound, "construction_jobs": len(build_keys),
        "dynamic_request_policy": "freeze recipes now; persist exact body before every send",
        "live_ready": not blockers, "blockers": blockers, "provider_requests": 0,
        "calibration_only": calibration_only,
        "capability_attribution_only": capability_only,
        "oracle_gate_only": oracle_gate_only,
        "oracle_context_mode": oracle_context_mode,
        "explicit_development_subset": bool(
            config["data"]["split"] == "dev" and ids and not calibration_only),
        "main_prefix_count": len(population["main"]),
        "answer_contract_revision": ANSWER_CONTRACT_REVISION,
        "interaction_policy_revision": INTERACTION_POLICY_REVISION,
        "judge_protocol_revision": JUDGE_PROTOCOL_REVISION,
        "calibration_exclusion_count": len(exclusions),
        "development_only": True, "independent_validation": False,
        "canonical_rubric_self_checks": len(rubrics),
        "dataset_manifest_sha256": file_sha256(dataset / "manifest.json"),
        "implementation": development_implementation(workspace)}
    output.mkdir(parents=True)
    write_json(output / "config.snapshot.json", config)
    write_json(output / "population.json", population)
    write_json(output / "preflight.json", summary)
    write_rows(output / "trials.jsonl", trials)
    write_rows(output / "rubrics.jsonl", rubrics)
    write_rows(output / "rule_examples.jsonl", examples)
    write_rows(output / "request_recipes.jsonl", [
        {"query_id": q.query_id, "empty_context_template": answer_request(
            q, [], rubric=rubric_by_query[q.query_id]
        )}
        for q in query_rows if q.query_id in selected_queries])
    write_rows(output / "full_history_context_checks.jsonl", full_history_context_checks)
    write_json(output / "manifest.json", {**summary, "artifacts": write_file_manifest(output)})
    return summary
