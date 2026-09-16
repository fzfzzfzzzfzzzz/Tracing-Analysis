"""Definitions moved from ``tracegraph.compression_audit_metrics``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import json
import math
import random
import re
import statistics
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence
from ...compression_audit import BENCHMARK_ID, SCHEMA_VERSION, FailureChainGold, PrefixRecord, QueryRecord, canonical_json, file_sha256, git_provenance, implementation_provenance, load_jsonl, stable_digest, verify_file_manifest, write_file_manifest
from ...compression_audit_runtime import FORMAL_METHOD_IDS, load_dataset

from .metric_constants import (
    PRIMARY_STRUCTURED_FIELDS as PRIMARY_STRUCTURED_FIELDS,
    SCORE_SCHEMA_VERSION as SCORE_SCHEMA_VERSION,
)



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


def _score_legacy_episode(
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


def score_episode(episode: Mapping[str, Any], prefix: PrefixRecord,
                  query: QueryRecord, gold: FailureChainGold) -> dict[str, Any]:
    if episode.get("protocol", "v0.1-diagnostic") == "v0.1-diagnostic":
        return _score_legacy_episode(episode, prefix, query, gold)
    if episode.get("protocol") != "v0.2-development":
        raise ValueError("unsupported episode scoring protocol")
    from .development_scoring import make_rubric, score_submission

    # Reuse only the legacy metadata/cost envelope, never its answer interpretation.
    result = _score_legacy_episode({**episode, "answer": None}, prefix, query, gold)
    rubric = episode.get("rubric") or make_rubric(query, gold)
    if rubric["query_id"] != query.query_id or rubric["gold_hash"] != gold.gold_hash:
        raise ValueError("rubric does not belong to this query/gold")
    visible = set(episode.get("artifact", {}).get("visible_event_ids", ()))
    visible.update(_recovered_source_ids(episode))
    score = score_submission(episode.get("answer"), rubric, sorted(visible),
        judge=episode.get("judge"), judge_calibrated=episode.get("judge_calibrated") is True,
        status=str(episode.get("status", "")),
        executed_side_effects=int(episode.get("executed_unauthorized_side_effects", 0)),
        unsafe_attempts=int(episode.get("unsafe_side_effect_attempts", 0)))
    result.update(score)
    result.update(ranking_eligible=False, causal_order_inversion=score["causal_constraint_rate"]
                  is not None and score["causal_constraint_rate"] < 1,
                  hallucination=bool(score["unknown_evidence_ids"]),
                  primary_structured_accuracy=(sum(v["pass"] for v in score["strict_values"].values())
                    / len(score["strict_values"]) if score["strict_values"] else None),
                  auxiliary_explanation_quality=score["judge_auxiliary_pass"])
    return result
