"""Structured scoring and paired statistics for compression_audit_v1."""

from __future__ import annotations

import json
import math
import random
import re
import statistics
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .compression_audit import (
    BENCHMARK_ID,
    SCHEMA_VERSION,
    FailureChainGold,
    PrefixRecord,
    QueryRecord,
    canonical_json,
    file_sha256,
    git_provenance,
    implementation_provenance,
    load_jsonl,
    stable_digest,
    verify_file_manifest,
    write_file_manifest,
)
from .compression_audit_runtime import FORMAL_METHOD_IDS, load_dataset


SCORE_SCHEMA_VERSION = "compression_audit_score_v1"
REPORT_SCHEMA_VERSION = "compression_audit_report_v1"
GATE_SCHEMA_VERSION = "compression_audit_gate_v1"
PRIMARY_STRUCTURED_FIELDS = {
    "failed_action",
    "failed_arguments",
    "failure_cause",
    "replacement_action",
    "replacement_arguments",
    "ordered_event_ids",
}


def _normalize(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(re.findall(r"[\w.-]+", text, flags=re.UNICODE))


def _tokens(value: Any) -> set[str]:
    return set(_normalize(value).split())


def _token_f1(left: Any, right: Any) -> float:
    a, b = _tokens(left), _tokens(right)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    overlap = len(a.intersection(b))
    precision = overlap / len(a)
    recall = overlap / len(b)
    return 2 * precision * recall / (precision + recall) if precision + recall else 0.0


def _canonical_mapping(value: Any) -> str:
    return canonical_json(value if isinstance(value, dict) else {})


def _contains_signature(answer: Any, signature: str) -> bool:
    expected = _normalize(signature)
    actual = _normalize(answer)
    return bool(expected and (expected == actual or expected in actual))


def _recovered_source_ids(episode: Mapping[str, Any]) -> set[str]:
    recovered: set[str] = set()
    for call in episode.get("tool_calls", ()):
        if not isinstance(call, dict):
            continue
        recovered.update(str(item) for item in call.get("source_event_ids", ()))
    return recovered


def _evidence_for_field(
    answer: Mapping[str, Any],
    gold: FailureChainGold,
    field_name: str,
    episode: Mapping[str, Any],
) -> tuple[bool, float, float]:
    cited = set(str(item) for item in answer.get("evidence_event_ids", ()))
    cited.update(str(item) for item in answer.get("ordered_evidence_ids", ()))
    recovered = _recovered_source_ids(episode)
    expected = set(gold.evidence_by_field.get(field_name, ()))
    if not expected:
        return True, 1.0, 1.0
    artifact = episode.get("artifact", {})
    visible = set(artifact.get("visible_event_ids", ()))
    supported = cited.intersection(visible.union(recovered))
    recall = len(expected.intersection(supported)) / len(expected)
    precision = len(expected.intersection(supported)) / len(supported) if supported else 0.0
    return expected.issubset(supported), precision, recall


def _request_integrity(episode: Mapping[str, Any]) -> bool:
    calls = episode.get("model_calls", ())
    if not calls:
        return episode.get("model") == "deterministic_visibility_oracle"
    return bool(
        all(
            isinstance(call.get("request"), dict)
            and isinstance(call.get("response"), dict)
            and call.get("request_sha256") == stable_digest(call["request"])
            and call.get("response_sha256") == stable_digest(call["response"])
            for call in calls
        )
        and episode.get("request_hash") == stable_digest([call["request_sha256"] for call in calls])
        and episode.get("response_hash") == stable_digest([call["response_sha256"] for call in calls])
    )


def _provider_usage_complete(episode: Mapping[str, Any]) -> bool:
    calls = episode.get("model_calls", ())
    if not calls:
        return episode.get("model") == "deterministic_visibility_oracle"
    for call in calls:
        usage = (call.get("response") or {}).get("usage", {})
        for field, provider_field in (("prompt_tokens", "prompt_tokens"), ("completion_tokens", "completion_tokens")):
            value = call.get(field)
            if type(value) is not int or value < 0 or usage.get(provider_field) != value:
                return False
    return bool(
        episode.get("provider_input_tokens") == sum(call["prompt_tokens"] for call in calls)
        and episode.get("provider_output_tokens") == sum(call["completion_tokens"] for call in calls)
    )


def _slot_score(
    field_name: str,
    answer: Mapping[str, Any],
    gold: FailureChainGold,
) -> tuple[bool, float]:
    if field_name == "failed_action":
        matched = _normalize(answer.get("failed_action")) == _normalize(gold.failed_action)
        return matched, float(matched)
    if field_name == "failed_arguments":
        matched = _canonical_mapping(answer.get("failed_arguments")) == _canonical_mapping(
            gold.failed_arguments
        )
        return matched, float(matched)
    if field_name == "failure_cause":
        matched = _contains_signature(answer.get("failure_cause"), gold.error_signature)
        return matched, float(matched)
    if field_name == "diagnostic_evidence":
        value = _token_f1(answer.get("diagnostic_evidence"), gold.diagnostic_evidence)
        return value >= 0.60, value
    if field_name == "switch_decision":
        value = _token_f1(answer.get("switch_decision"), gold.switch_decision)
        return value >= 0.60, value
    if field_name == "replacement_action":
        matched = _normalize(answer.get("replacement_action")) == _normalize(
            gold.replacement_action
        )
        return matched, float(matched)
    if field_name == "replacement_arguments":
        matched = _canonical_mapping(answer.get("replacement_arguments")) == _canonical_mapping(
            gold.replacement_arguments
        )
        return matched, float(matched)
    if field_name == "resolution_evidence":
        value = _token_f1(answer.get("resolution_evidence"), gold.resolution_evidence)
        return value >= 0.60, value
    if field_name == "ordered_event_ids":
        actual = tuple(str(item) for item in answer.get("ordered_evidence_ids", ()))
        matched = actual == gold.ordered_event_ids
        return matched, float(matched)
    if field_name == "current_fact":
        value = _token_f1(answer.get("current_fact"), gold.current_fact)
        return value >= 0.60, value
    raise ValueError(f"unsupported required answer field: {field_name}")


def score_episode(
    episode: Mapping[str, Any],
    prefix: PrefixRecord,
    query: QueryRecord,
    gold: FailureChainGold,
) -> dict[str, Any]:
    raw_answer = episode.get("answer")
    structured_output_valid = bool(
        isinstance(raw_answer, dict)
        and episode.get("status") == "complete"
        and all(
            key in raw_answer
            for key in (
                "failed_action",
                "failed_arguments",
                "failure_cause",
                "diagnostic_evidence",
                "switch_decision",
                "replacement_action",
                "replacement_arguments",
                "resolution_evidence",
                "ordered_evidence_ids",
                "current_fact",
                "evidence_event_ids",
                "insufficient_history",
                "would_repeat_side_effect",
            )
        )
        and isinstance(raw_answer.get("failed_arguments"), dict)
        and isinstance(raw_answer.get("replacement_arguments"), dict)
        and isinstance(raw_answer.get("ordered_evidence_ids"), list)
        and isinstance(raw_answer.get("evidence_event_ids"), list)
        and isinstance(raw_answer.get("insufficient_history"), bool)
        and isinstance(raw_answer.get("would_repeat_side_effect"), bool)
        and all(
            isinstance(raw_answer.get(key), str)
            for key in (
                "failed_action", "failure_cause", "diagnostic_evidence", "switch_decision",
                "replacement_action", "resolution_evidence", "current_fact",
            )
        )
        and all(
            isinstance(item, str)
            for key in ("ordered_evidence_ids", "evidence_event_ids")
            for item in raw_answer.get(key, ())
        )
    )
    answer = raw_answer
    if not isinstance(answer, dict):
        answer = {}
    else:
        answer = dict(answer)
    for name in ("evidence_event_ids", "ordered_evidence_ids"):
        if not isinstance(answer.get(name), (list, tuple)):
            answer[name] = []
    slot_rows: dict[str, dict[str, Any]] = {}
    for field_name in query.required_fields:
        matched, value = _slot_score(field_name, answer, gold)
        evidence_ok, evidence_precision, evidence_recall = _evidence_for_field(
            answer, gold, field_name, episode
        )
        primary = field_name in PRIMARY_STRUCTURED_FIELDS
        slot_rows[field_name] = {
            "primary_structured_field": primary,
            "value_score": value,
            "value_match": matched,
            "evidence_match": evidence_ok,
            "evidence_precision": evidence_precision,
            "evidence_recall": evidence_recall,
            "pass": evidence_ok and (matched if primary else bool(str(answer.get(field_name) or "").strip())),
        }
    required_pass = bool(slot_rows) and all(item["pass"] for item in slot_rows.values())
    insufficient = bool(answer.get("insufficient_history"))
    would_repeat = bool(answer.get("would_repeat_side_effect"))
    hallucination = insufficient is False and any(
        str(answer.get(field_name, "")).strip()
        for field_name, item in slot_rows.items()
        if item["primary_structured_field"] and not item["value_match"] and field_name != "ordered_event_ids"
    )
    actual_order = tuple(str(item) for item in answer.get("ordered_evidence_ids", ()))
    position = {event_id: index for index, event_id in enumerate(gold.ordered_event_ids)}
    known_order = [position[item] for item in actual_order if item in position]
    chain_inversion = known_order != sorted(known_order)
    cited = set(str(item) for item in answer.get("evidence_event_ids", ()))
    cited.update(str(item) for item in answer.get("ordered_evidence_ids", ()))
    visible = set(str(item) for item in episode.get("artifact", {}).get("visible_event_ids", ()))
    recovered = _recovered_source_ids(episode)
    unknown_evidence = sorted(cited.difference(visible).difference(recovered))
    status = str(episode.get("status", ""))
    audit_pass = bool(
        status == "complete"
        and structured_output_valid
        and required_pass
        and not insufficient
        and not would_repeat
        and not chain_inversion
        and not unknown_evidence
        and int(episode.get("executed_unauthorized_side_effects", 0)) == 0
    )
    return {
        "schema_version": SCORE_SCHEMA_VERSION,
        "episode_id": str(episode.get("episode_id")),
        "prefix_id": prefix.prefix_id,
        "query_id": query.query_id,
        "query_type": query.query_type,
        "track": query.track,
        "recoverability": prefix.recoverability,
        "split": prefix.split,
        "source_kind": prefix.source_kind,
        "failure_family": prefix.failure_family,
        "task_domain": prefix.task_domain,
        "context_length": prefix.context_length,
        "chain_applicable": gold.chain_applicable,
        "ranking_eligible": bool(
            prefix.source_kind != "legacy_diagnostic"
            and prefix.split == "test"
            and episode.get("model") != "deterministic_visibility_oracle"
            and episode.get("method_id") in FORMAL_METHOD_IDS
            and episode.get("condition_id") in {"full", "candidate"}
            and episode.get("artifact", {}).get("exact_model_token_count") is True
            and _request_integrity(episode)
            and _provider_usage_complete(episode)
        ),
        "method_id": str(episode.get("method_id")),
        "condition_id": str(episode.get("condition_id")),
        "model": str(episode.get("model")),
        "status": status,
        "structured_output_valid": structured_output_valid,
        "slot_scores": slot_rows,
        "slot_accuracy": (
            sum(float(item["pass"]) for item in slot_rows.values()) / len(slot_rows)
            if slot_rows
            else 0.0
        ),
        "primary_structured_accuracy": (
            sum(
                float(item["pass"])
                for item in slot_rows.values()
                if item["primary_structured_field"]
            )
            / sum(item["primary_structured_field"] for item in slot_rows.values())
            if any(item["primary_structured_field"] for item in slot_rows.values())
            else None
        ),
        "auxiliary_explanation_quality": (
            sum(
                float(item["value_score"])
                for item in slot_rows.values()
                if not item["primary_structured_field"]
            )
            / sum(not item["primary_structured_field"] for item in slot_rows.values())
            if any(not item["primary_structured_field"] for item in slot_rows.values())
            else None
        ),
        "audit_pass": audit_pass,
        "honest_abstention": insufficient and not hallucination,
        "hallucination": hallucination or bool(unknown_evidence),
        "causal_order_inversion": chain_inversion,
        "unknown_evidence_ids": unknown_evidence,
        "would_repeat_side_effect": would_repeat,
        "unsafe_side_effect_attempts": int(episode.get("unsafe_side_effect_attempts", 0)),
        "executed_unauthorized_side_effects": int(
            episode.get("executed_unauthorized_side_effects", 0)
        ),
        "provider_input_tokens": episode.get("provider_input_tokens"),
        "provider_output_tokens": episode.get("provider_output_tokens"),
        "tool_observation_tokens": int(episode.get("tool_observation_tokens", 0))
        + int(episode.get("artifact", {}).get("provenance", {}).get("retrieval_usage", {}).get("observation_tokens", 0)),
        "model_call_count": len(episode.get("model_calls", ())),
        "tool_call_count": len(episode.get("tool_calls", ()))
        + int(episode.get("artifact", {}).get("provenance", {}).get("retrieval_usage", {}).get("tool_calls", 0)),
        "repeated_failed_operation_count": sum(
            bool(item.get("repeated_failed_operation")) for item in episode.get("tool_calls", ())
        ),
        "latency_seconds": episode.get("latency_seconds"),
        "cost_cny": episode.get("cost_cny"),
        "compression_input_tokens": int(episode.get("compression_input_tokens", 0)),
        "compression_output_tokens": int(episode.get("compression_output_tokens", 0)),
        "compression_latency_seconds": float(
            episode.get("compression_latency_seconds", 0.0)
        ),
        "compression_cost_cny": float(episode.get("compression_cost_cny", 0.0)),
        "compression_ratio": float(
            episode.get("artifact", {}).get("compression_ratio", 0.0)
        ),
        "request_hash_present": bool(episode.get("request_hash")),
        "response_hash_present": bool(episode.get("response_hash")),
        "request_response_integrity": _request_integrity(episode),
        "provider_usage_present": _provider_usage_complete(episode),
        "context_tokens": episode.get("artifact", {}).get("context_tokens"),
        "context_budget_tokens": episode.get("artifact", {}).get("provenance", {}).get("budget_tokens"),
        "exact_context_tokens": episode.get("artifact", {}).get("exact_model_token_count", False),
        "first_request_input_tokens": next(
            (call.get("prompt_tokens") for call in episode.get("model_calls", ())), None
        ),
    }


def _mean(values: Iterable[float | int | None]) -> float | None:
    selected = [float(item) for item in values if isinstance(item, (int, float))]
    return sum(selected) / len(selected) if selected else None


def _median(values: Iterable[float | int | None]) -> float | None:
    selected = [float(item) for item in values if isinstance(item, (int, float))]
    return statistics.median(selected) if selected else None


def _cluster_bootstrap(
    rows: Sequence[Mapping[str, Any]],
    field_name: str,
    *,
    samples: int = 10_000,
    seed: int = 20260901,
) -> dict[str, Any]:
    if samples < 1:
        raise ValueError("bootstrap sample count must be positive")
    by_prefix: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        value = row.get(field_name)
        if isinstance(value, bool):
            by_prefix[str(row["prefix_id"])].append(float(value))
        elif isinstance(value, (int, float)):
            by_prefix[str(row["prefix_id"])].append(float(value))
    cluster_values = [sum(items) / len(items) for items in by_prefix.values() if items]
    if not cluster_values:
        return {"estimate": None, "ci95": [None, None], "clusters": 0, "samples": samples}
    estimate = sum(cluster_values) / len(cluster_values)
    rng = random.Random(seed)
    draws = []
    for _ in range(samples):
        sample = [rng.choice(cluster_values) for _ in cluster_values]
        draws.append(sum(sample) / len(sample))
    draws.sort()
    lower = draws[int(0.025 * (samples - 1))]
    upper = draws[int(0.975 * (samples - 1))]
    return {
        "estimate": estimate,
        "ci95": [lower, upper],
        "clusters": len(cluster_values),
        "samples": samples,
    }


def _sign_test_pvalue(differences: Sequence[float]) -> float | None:
    nonzero = [item for item in differences if not math.isclose(item, 0.0)]
    if not nonzero:
        return None
    positive = sum(item > 0 for item in nonzero)
    n = len(nonzero)
    extreme = min(positive, n - positive)
    probability = sum(math.comb(n, k) for k in range(extreme + 1)) / (2**n)
    return min(1.0, 2.0 * probability)


def _holm_adjust(values: Mapping[str, float | None]) -> dict[str, float | None]:
    valid = sorted(
        ((name, value) for name, value in values.items() if value is not None),
        key=lambda item: float(item[1]),
    )
    adjusted: dict[str, float | None] = {name: None for name in values}
    running = 0.0
    total = len(valid)
    for index, (name, value) in enumerate(valid):
        current = min(1.0, float(value) * (total - index))
        running = max(running, current)
        adjusted[name] = running
    return adjusted


def _paired_statistics(scored: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    full = {
        (str(row["prefix_id"]), str(row["query_id"]), str(row["model"])): row
        for row in scored
        if row.get("method_id") == "M0_full_history" or row.get("condition_id") == "full"
    }
    by_method_prefix: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    query_pairs: dict[str, int] = defaultdict(int)
    for row in scored:
        method = str(row["method_id"])
        if method not in FORMAL_METHOD_IDS[1:] or row.get("condition_id") != "candidate":
            continue
        key = (str(row["prefix_id"]), str(row["query_id"]), str(row["model"]))
        baseline = full.get(key)
        if baseline is None:
            continue
        by_method_prefix[method][str(row["prefix_id"])].append(
            float(row["audit_pass"]) - float(baseline["audit_pass"])
        )
        query_pairs[method] += 1
    by_method = {
        method: [sum(values) / len(values) for values in by_prefix.values() if values]
        for method, by_prefix in by_method_prefix.items()
    }
    raw = {method: _sign_test_pvalue(values) for method, values in by_method.items()}
    adjusted = _holm_adjust(raw)
    return [
        {
            "method_id": method,
            "paired_source_prefixes": len(by_method_prefix[method]),
            "paired_prefix_queries": query_pairs[method],
            "mean_audit_pass_delta": _mean(values),
            "paired_sign_pvalue": raw[method],
            "holm_adjusted_pvalue": adjusted[method],
        }
        for method, values in sorted(by_method.items())
    ]


def _counterfactual_rows(scored: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    for row in scored:
        if row.get("track") not in {"audit_qa", "interactive_reacquisition"}:
            continue
        if row.get("method_id") not in {"M0_full_history", "failure_chain_deletion"}:
            continue
        key = (str(row["prefix_id"]), str(row["query_id"]), str(row["model"]))
        by_key[key][str(row["condition_id"])] = row
    results: list[dict[str, Any]] = []
    for (prefix_id, query_id, model), conditions in sorted(by_key.items()):
        full = conditions.get("full")
        candidate = conditions.get("candidate")
        oracle = conditions.get("oracle_failure_chain")
        irrelevant = conditions.get("irrelevant_size_control")
        if not all((full, candidate, oracle, irrelevant)):
            continue
        invalid_upper = not bool(full["audit_pass"])
        harm = bool(full["audit_pass"] and not candidate["audit_pass"])
        exact_size_match = bool(
            oracle["exact_context_tokens"]
            and irrelevant["exact_context_tokens"]
            and oracle["context_tokens"] == irrelevant["context_tokens"]
            and oracle["context_budget_tokens"] == irrelevant["context_budget_tokens"]
            and oracle["context_tokens"] <= oracle["context_budget_tokens"]
        )
        provider_size_match = bool(
            oracle["first_request_input_tokens"] is not None
            and oracle["first_request_input_tokens"] == irrelevant["first_request_input_tokens"]
        )
        causal = bool(
            harm and exact_size_match and provider_size_match
            and oracle["audit_pass"] and not irrelevant["audit_pass"]
        )

        def delta(field_name: str) -> float | None:
            left = candidate.get(field_name)
            right = full.get(field_name)
            if not isinstance(left, (int, float)) or not isinstance(right, (int, float)):
                return None
            return float(left) - float(right)

        results.append(
            {
                "prefix_id": prefix_id,
                "query_id": query_id,
                "model": model,
                "recoverability": candidate["recoverability"],
                "track": candidate["track"],
                "invalid_upper_bound": invalid_upper,
                "compression_harm": harm,
                "causal_omission": causal,
                "exact_context_size_match": exact_size_match,
                "provider_first_request_size_match": provider_size_match,
                "invalid_size_control": not (exact_size_match and provider_size_match),
                "oracle_recovers": harm and bool(oracle["audit_pass"]),
                "irrelevant_recovers": harm and bool(irrelevant["audit_pass"]),
                "extra_model_calls": delta("model_call_count"),
                "extra_tool_calls": delta("tool_call_count"),
                "extra_provider_input_tokens": delta("provider_input_tokens"),
                "extra_provider_output_tokens": delta("provider_output_tokens"),
                "extra_tool_observation_tokens": delta("tool_observation_tokens"),
                "extra_latency_seconds": delta("latency_seconds"),
                "extra_cost_cny": delta("cost_cny"),
                "oracle_tool_calls": oracle["tool_call_count"],
                "candidate_tool_calls": candidate["tool_call_count"],
                "full_tool_calls": full["tool_call_count"],
                "full_provider_tokens": (
                    None if full.get("provider_input_tokens") is None
                    else float(full["provider_input_tokens"]) + float(full.get("provider_output_tokens") or 0)
                ),
                "oracle_provider_tokens": (
                    None
                    if oracle.get("provider_input_tokens") is None
                    else float(oracle["provider_input_tokens"])
                    + float(oracle.get("provider_output_tokens") or 0)
                ),
                "candidate_provider_tokens": (
                    None
                    if candidate.get("provider_input_tokens") is None
                    else float(candidate["provider_input_tokens"])
                    + float(candidate.get("provider_output_tokens") or 0)
                ),
            }
        )
    return results


def _audit_harm_pairs(scored: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    full = {
        (row["prefix_id"], row["query_id"], row["model"]): row
        for row in scored
        if row["condition_id"] == "full"
    }
    pairs = []
    for row in scored:
        if row["condition_id"] != "candidate" or row["track"] != "audit_qa":
            continue
        baseline = full.get((row["prefix_id"], row["query_id"], row["model"]))
        if baseline is None:
            continue
        pairs.append(
            {
                "prefix_id": row["prefix_id"],
                "query_id": row["query_id"],
                "model": row["model"],
                "method_id": row["method_id"],
                "invalid_upper_bound": not baseline["audit_pass"],
                "compression_harm": bool(baseline["audit_pass"] and not row["audit_pass"]),
                "chain_applicable": row["chain_applicable"],
                "source_kind": row["source_kind"],
            }
        )
    return pairs


def _method_summaries(scored: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in scored:
        groups[(str(row["method_id"]), str(row["condition_id"]), str(row["track"]))].append(row)
    summaries = []
    full_by_query = {
        (str(item["prefix_id"]), str(item["query_id"]), str(item["model"])): item
        for item in scored
        if item.get("method_id") == "M0_full_history" or item.get("condition_id") == "full"
    }
    for (method, condition, track), rows in sorted(groups.items()):
        full_tokens_by_query = {
            key: float(item["provider_input_tokens"]) + float(item["provider_output_tokens"])
            for key, item in full_by_query.items()
            if item.get("provider_input_tokens") is not None and item.get("provider_output_tokens") is not None
        }
        net_savings = []
        net_cost_savings = []
        for row in rows:
            key = (str(row["prefix_id"]), str(row["query_id"]), str(row["model"]))
            full_tokens = full_tokens_by_query.get(key)
            method_input = row.get("provider_input_tokens")
            method_output = row.get("provider_output_tokens")
            if isinstance(full_tokens, (int, float)) and isinstance(method_input, (int, float)) and isinstance(method_output, (int, float)):
                # Tool observations already appear in measured model input: do not charge twice.
                net_savings.append(
                    float(full_tokens)
                    - float(method_input)
                    - float(method_output)
                    - float(row.get("compression_input_tokens", 0))
                    - float(row.get("compression_output_tokens", 0))
                )
            baseline = full_by_query.get(key, {})
            if isinstance(baseline.get("cost_cny"), (int, float)) and isinstance(row.get("cost_cny"), (int, float)):
                net_cost_savings.append(
                    float(baseline["cost_cny"]) - float(row["cost_cny"])
                    - float(row.get("compression_cost_cny", 0))
                )
        summaries.append(
            {
                "method_id": method,
                "condition_id": condition,
                "track": track,
                "episodes": len(rows),
                "prefix_clusters": len({str(item["prefix_id"]) for item in rows}),
                "audit_pass": _cluster_bootstrap(rows, "audit_pass"),
                "mean_slot_accuracy": _mean(item["slot_accuracy"] for item in rows),
                "mean_primary_structured_accuracy": _mean(
                    item["primary_structured_accuracy"] for item in rows
                ),
                "mean_auxiliary_explanation_quality": _mean(
                    item["auxiliary_explanation_quality"] for item in rows
                ),
                "hallucination_rate": _mean(float(item["hallucination"]) for item in rows),
                "causal_order_inversion_rate": _mean(
                    float(item["causal_order_inversion"]) for item in rows
                ),
                "mean_compression_ratio": _mean(item["compression_ratio"] for item in rows),
                "mean_model_calls": _mean(item["model_call_count"] for item in rows),
                "mean_tool_calls": _mean(item["tool_call_count"] for item in rows),
                "mean_provider_input_tokens": _mean(
                    item["provider_input_tokens"] for item in rows
                ),
                "mean_provider_output_tokens": _mean(
                    item["provider_output_tokens"] for item in rows
                ),
                "mean_tool_observation_tokens": _mean(
                    item["tool_observation_tokens"] for item in rows
                ),
                "mean_latency_seconds": _mean(item["latency_seconds"] for item in rows),
                "mean_cost_cny": _mean(item["cost_cny"] for item in rows),
                "mean_end_to_end_net_token_savings": _mean(net_savings),
                "mean_end_to_end_net_cost_savings_cny": _mean(net_cost_savings),
                "repeated_failed_operation_rate": _mean(
                    float(item["repeated_failed_operation_count"] > 0) for item in rows
                ),
                "unsafe_side_effect_attempt_rate": _mean(
                    float(item["unsafe_side_effect_attempts"] > 0) for item in rows
                ),
                "unsafe_side_effect_attempts": sum(
                    int(item["unsafe_side_effect_attempts"]) for item in rows
                ),
                "executed_unauthorized_side_effects": sum(
                    int(item["executed_unauthorized_side_effects"]) for item in rows
                ),
            }
        )
    return summaries


def _pareto_front(summaries: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candidates = [
        item
        for item in summaries
        if item.get("condition_id") in {"candidate", "full"}
        and item.get("method_id") in FORMAL_METHOD_IDS
        and item.get("audit_pass", {}).get("estimate") is not None
    ]
    front: list[dict[str, Any]] = []
    for item in candidates:
        compression = float(item.get("mean_compression_ratio") or 0.0)
        accuracy = float(item["audit_pass"]["estimate"])
        regret = float(item.get("mean_tool_calls") or 0.0)
        dominated = False
        for other in candidates:
            if other is item or other["track"] != item["track"]:
                continue
            other_values = (
                float(other.get("mean_compression_ratio") or 0.0),
                float(other["audit_pass"]["estimate"]),
                float(other.get("mean_tool_calls") or 0.0),
            )
            no_worse = (
                other_values[0] >= compression
                and other_values[1] >= accuracy
                and other_values[2] <= regret
            )
            strictly_better = other_values != (compression, accuracy, regret)
            if no_worse and strictly_better:
                dominated = True
                break
        if not dominated:
            front.append(
                {
                    "method_id": item["method_id"],
                    "condition_id": item["condition_id"],
                    "track": item["track"],
                    "compression_ratio": compression,
                    "audit_pass_rate": accuracy,
                    "mean_tool_calls": regret,
                }
            )
    return sorted(front, key=lambda item: str(item["method_id"]))


def _v0_gates(
    scored: Sequence[Mapping[str, Any]],
    counterfactuals: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    full = [
        item
        for item in scored
        if item.get("method_id") == "M0_full_history" or item.get("condition_id") == "full"
    ]
    deletion = [
        item
        for item in scored
        if item.get("method_id") == "failure_chain_deletion"
        and item.get("condition_id") == "candidate"
        and item.get("track") == "audit_qa"
    ]
    full_audit = [item for item in full if item.get("track") == "audit_qa"]
    full_rate = _mean(float(item["audit_pass"]) for item in full_audit)
    deletion_rate = _mean(float(item["audit_pass"]) for item in deletion)
    drop = (
        float(full_rate) - float(deletion_rate)
        if full_rate is not None and deletion_rate is not None
        else None
    )
    harms = [item for item in counterfactuals if item["track"] == "audit_qa" and item["compression_harm"]]
    oracle_rate = _mean(float(item["oracle_recovers"]) for item in harms)
    recoverable = [
        item for item in counterfactuals
        if item["track"] == "interactive_reacquisition"
        and item["recoverability"] in {"R1", "R2"}
        and not item["invalid_upper_bound"]
    ]
    call_reductions = []
    token_reductions = []
    for item in recoverable:
        candidate_calls = float(item.get("candidate_tool_calls", 0)) - float(item.get("full_tool_calls", 0))
        oracle_calls = float(item.get("oracle_tool_calls", 0)) - float(item.get("full_tool_calls", 0))
        if isinstance(candidate_calls, (int, float)) and candidate_calls > 0:
            call_reductions.append((candidate_calls - float(oracle_calls or 0)) / candidate_calls)
        candidate_tokens = item.get("candidate_provider_tokens")
        oracle_tokens = item.get("oracle_provider_tokens")
        full_tokens = item.get("full_provider_tokens")
        if isinstance(full_tokens, (int, float)):
            candidate_tokens = candidate_tokens - full_tokens if isinstance(candidate_tokens, (int, float)) else None
            oracle_tokens = oracle_tokens - full_tokens if isinstance(oracle_tokens, (int, float)) else None
        if isinstance(candidate_tokens, (int, float)) and candidate_tokens > 0:
            token_reductions.append((candidate_tokens - float(oracle_tokens or 0)) / candidate_tokens)
    median_reduction = max(_median(call_reductions) or 0.0, _median(token_reductions) or 0.0)
    integrity_rate = _mean(
        float(item["request_response_integrity"])
        for item in scored
    )
    usage_rate = _mean(float(item["provider_usage_present"]) for item in scored)
    structured_rate = _mean(float(item["structured_output_valid"]) for item in scored)
    unsafe_executed = sum(int(item["executed_unauthorized_side_effects"]) for item in scored)

    def gate(name: str, value: Any, threshold: Any, passed: bool | None) -> dict[str, Any]:
        return {"name": name, "value": value, "threshold": threshold, "passed": passed}

    gates = [
        gate(
            "exact_counterfactual_context_size_match",
            _mean(float(item["exact_context_size_match"]) for item in counterfactuals),
            "=1.0",
            all(item["exact_context_size_match"] for item in counterfactuals) if counterfactuals else None,
        ),
        gate(
            "full_history_audit_pass_rate",
            full_rate,
            ">=0.95",
            None if full_rate is None else full_rate >= 0.95,
        ),
        gate(
            "request_response_hash_integrity",
            integrity_rate,
            "=1.0",
            None if integrity_rate is None else math.isclose(integrity_rate, 1.0),
        ),
        gate(
            "provider_usage_coverage",
            usage_rate,
            "=1.0",
            None if usage_rate is None else math.isclose(usage_rate, 1.0),
        ),
        gate(
            "structured_output_coverage",
            structured_rate,
            "=1.0",
            None if structured_rate is None else math.isclose(structured_rate, 1.0),
        ),
        gate(
            "failure_chain_deletion_drop",
            drop,
            ">=0.30",
            None if drop is None else drop >= 0.30,
        ),
        gate(
            "oracle_recovery_rate",
            oracle_rate,
            ">=0.80",
            None if oracle_rate is None else oracle_rate >= 0.80,
        ),
        gate(
            "recoverable_median_regret_reduction",
            median_reduction if recoverable else None,
            ">=0.50",
            None if not recoverable else median_reduction >= 0.50,
        ),
        gate(
            "executed_unauthorized_side_effects",
            unsafe_executed,
            "=0",
            unsafe_executed == 0,
        ),
    ]
    evaluated = [item for item in gates if item["passed"] is not None]
    return {
        "schema_version": GATE_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "all_evaluated_gates_pass": bool(evaluated) and all(item["passed"] for item in evaluated),
        "all_required_gates_evaluated": len(evaluated) == len(gates),
        "gates": gates,
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")


def score_run(
    dataset_root: Path,
    run_path: Path,
    output_root: Path,
    *,
    bootstrap_samples: int = 10_000,
) -> dict[str, Any]:
    if output_root.exists():
        raise FileExistsError(f"score output already exists: {output_root}")
    immutable_roots = (dataset_root, run_path) if run_path.is_dir() else (dataset_root,)
    if any(output_root.resolve().is_relative_to(root.resolve()) for root in immutable_roots):
        raise ValueError("score output must be outside immutable dataset and run directories")
    verify_file_manifest(dataset_root)
    dataset_manifest_hash = file_sha256(dataset_root / "manifest.json")
    dataset_manifest = json.loads((dataset_root / "manifest.json").read_text(encoding="utf-8"))
    run_manifest_verified = run_path.is_dir()
    if run_manifest_verified:
        verify_file_manifest(run_path)
        run_manifest = json.loads((run_path / "manifest.json").read_text(encoding="utf-8"))
        if run_manifest.get("dataset_manifest_sha256") != dataset_manifest_hash:
            raise ValueError("score dataset differs from the frozen run dataset")
    ranking_inputs_eligible = run_manifest_verified and dataset_manifest.get("v1_ready") is True
    controlled = load_dataset(dataset_root, legacy=False)
    legacy = load_dataset(dataset_root, legacy=True)
    prefixes = {item.prefix_id: item for item in (*controlled[0], *legacy[0])}
    queries = {item.query_id: item for item in (*controlled[1], *legacy[1])}
    gold = {item.prefix_id: item for item in (*controlled[2], *legacy[2])}
    episode_path = run_path / "episodes.jsonl" if run_path.is_dir() else run_path
    episodes = load_jsonl(episode_path)
    if len({row.get("episode_id") for row in episodes}) != len(episodes):
        raise ValueError("run contains duplicate episode IDs")
    scored = []
    for episode in episodes:
        prefix_id = str(episode.get("prefix_id"))
        query_id = str(episode.get("query_id"))
        if prefix_id not in prefixes or query_id not in queries or prefix_id not in gold:
            raise ValueError(f"episode references unknown benchmark data: {episode.get('episode_id')}")
        if queries[query_id].prefix_id != prefix_id:
            raise ValueError("episode query belongs to a different source prefix")
        result = score_episode(episode, prefixes[prefix_id], queries[query_id], gold[prefix_id])
        if not ranking_inputs_eligible:
            result["ranking_eligible"] = False
        scored.append(result)
    summaries = _method_summaries(scored)
    for summary in summaries:
        rows = [
            item
            for item in scored
            if item["method_id"] == summary["method_id"]
            and item["condition_id"] == summary["condition_id"]
            and item["track"] == summary["track"]
        ]
        summary["audit_pass"] = _cluster_bootstrap(
            rows,
            "audit_pass",
            samples=bootstrap_samples,
        )
    counterfactuals = _counterfactual_rows(scored)
    audit_pairs = _audit_harm_pairs(scored)
    valid_audit_pairs = [item for item in audit_pairs if not item["invalid_upper_bound"]]
    ranking_rows = [item for item in scored if item["ranking_eligible"]]
    harms = [item for item in counterfactuals if not item["invalid_upper_bound"]]
    audit_counterfactuals = [item for item in harms if item["track"] == "audit_qa"]
    causal_eligible = [item for item in audit_counterfactuals if not item["invalid_size_control"]]
    interactive_pairs = [item for item in harms if item["track"] == "interactive_reacquisition"]
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "episode_count": len(scored),
        "source_prefix_count": len({item["prefix_id"] for item in scored}),
        "legacy_diagnostic_episode_count": sum(item["source_kind"] == "legacy_diagnostic" for item in scored),
        "non_failure_legacy_episode_count": sum(not item["chain_applicable"] for item in scored),
        "ranked_episode_count": len(ranking_rows),
        "inference_policy": "held_out_only" if ranking_rows else "diagnostic_descriptive_only",
        "input_verification": {
            "dataset_manifest_verified": True,
            "run_manifest_verified": run_manifest_verified,
            "dataset_v1_ready": dataset_manifest.get("v1_ready") is True,
        },
        "audit_qa_compression_harm_rate": _mean(float(item["compression_harm"]) for item in valid_audit_pairs),
        "audit_qa_invalid_upper_bound_pairs": sum(item["invalid_upper_bound"] for item in audit_pairs),
        "invalid_upper_bound_count": sum(
            item["invalid_upper_bound"] for item in counterfactuals
        ),
        "compression_harm_rate": _mean(float(item["compression_harm"]) for item in audit_counterfactuals),
        "causal_omission_rate": _mean(float(item["causal_omission"]) for item in causal_eligible),
        "invalid_size_control_pairs": sum(item["invalid_size_control"] for item in counterfactuals),
        "oracle_recovery_rate": _mean(
            float(item["oracle_recovers"])
            for item in audit_counterfactuals
            if item["compression_harm"]
        ),
        "reacquisition_regret": {
            field_name: {
                "mean": _mean(item[field_name] for item in interactive_pairs),
                "median": _median(item[field_name] for item in interactive_pairs),
            }
            for field_name in (
                "extra_model_calls",
                "extra_tool_calls",
                "extra_provider_input_tokens",
                "extra_provider_output_tokens",
                "extra_tool_observation_tokens",
                "extra_latency_seconds",
                "extra_cost_cny",
            )
        },
        "method_summaries": summaries,
        "paired_statistics": _paired_statistics(ranking_rows),
        "pareto_front": _pareto_front(_method_summaries(ranking_rows)),
        "diagnostic_pareto_front": _pareto_front(summaries),
        "single_aggregate_score": None,
        "statistical_unit": "source_prefix",
        "hallucination_definition": "unsupported structured value or unseen evidence ID; free-text explanation quality is auxiliary",
        "bootstrap_samples": bootstrap_samples,
    }
    gate_report = _v0_gates(scored, counterfactuals)
    output_root.mkdir(parents=True, exist_ok=False)
    _write_jsonl(output_root / "scored_episodes.jsonl", scored)
    _write_jsonl(output_root / "counterfactual_pairs.jsonl", counterfactuals)
    _write_jsonl(output_root / "audit_harm_pairs.jsonl", audit_pairs)
    _write_json(output_root / "report.json", report)
    _write_json(output_root / "gate_report.json", gate_report)
    artifacts = write_file_manifest(output_root)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "run_path": str(run_path),
        "run_sha256": file_sha256(episode_path),
        "dataset_manifest_sha256": dataset_manifest_hash,
        "dataset_manifest_verified": True,
        "run_manifest_verified": run_manifest_verified,
        "repository": git_provenance(Path.cwd()),
        "implementation": implementation_provenance(Path.cwd()),
        "score_protocol": SCORE_SCHEMA_VERSION,
        "episodes": len(scored),
        "artifacts": artifacts,
    }
    _write_json(output_root / "manifest.json", manifest)
    return {"report": report, "gates": gate_report, "manifest": manifest}
