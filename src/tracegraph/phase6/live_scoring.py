"""Definitions moved from ``tracegraph.phase6_live``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence
from ..capture import estimate_tokens
from ..decision_state import stable_digest

from .live_constants import (
    FORK_TYPES as FORK_TYPES,
    LIVE_METHOD_IDS as LIVE_METHOD_IDS,
    SCORING_PROTOCOL_V1 as SCORING_PROTOCOL_V1,
    SCORING_PROTOCOL_V2 as SCORING_PROTOCOL_V2,
    _HISTORICAL_FACT_CONCEPTS_V2 as _HISTORICAL_FACT_CONCEPTS_V2,
    _HISTORICAL_FACT_GROUPS as _HISTORICAL_FACT_GROUPS,
)



def _answer_fact_match_v1(answer: str, fork: Mapping[str, Any]) -> bool:
    lowered = re.sub(r"\s+", " ", answer.casefold())
    if fork["fork_type"] != "REACTIVATE":
        expected = str(fork["expected_answer_facts"][0]).casefold()
        _, entity = expected.split(":", 1)
        return entity in lowered and any(word in lowered for word in ("current", "present"))
    groups = _HISTORICAL_FACT_GROUPS[str(fork["prefix_id"]).split(":", 1)[0]]
    return all(any(term in lowered for term in group) for group in groups)


def _normalized_words(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = re.sub(r"[_\-]+", " ", normalized)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized)
    return re.sub(r"\s+", " ", normalized).strip()


def _phrase_positions(words: Sequence[str], phrase: Sequence[str]) -> list[float]:
    width = len(phrase)
    return [
        index + (width - 1) / 2
        for index in range(len(words) - width + 1)
        if tuple(words[index : index + width]) == tuple(phrase)
    ]


def _stem_positions(words: Sequence[str], stems: Sequence[str]) -> list[int]:
    return [
        index
        for index, word in enumerate(words)
        if any(word.startswith(stem) for stem in stems)
    ]


def _entity_has_nearest_status(
    words: Sequence[str],
    entity: Sequence[str],
    *,
    wanted: Sequence[str],
    opposite: Sequence[str],
) -> bool:
    entities = _phrase_positions(words, entity)
    wanted_positions = _stem_positions(words, wanted)
    opposite_positions = _stem_positions(words, opposite)
    if not entities or not wanted_positions:
        return False
    wanted_distance = min(abs(entity_at - status_at) for entity_at in entities for status_at in wanted_positions)
    opposite_distance = (
        min(abs(entity_at - status_at) for entity_at in entities for status_at in opposite_positions)
        if opposite_positions
        else float("inf")
    )
    return wanted_distance < opposite_distance


def _relation_appears_in_one_clause(
    answer: str,
    entity: Sequence[str],
    *,
    wanted: Sequence[str],
    opposite: Sequence[str],
) -> bool:
    clauses = re.split(r"[.!?;\n]+", answer)
    return any(
        _entity_has_nearest_status(
            _normalized_words(clause).split(),
            entity,
            wanted=wanted,
            opposite=opposite,
        )
        for clause in clauses
    )


def _answer_fact_match_v2(answer: str, fork: Mapping[str, Any]) -> bool:
    normalized = _normalized_words(answer)
    if fork["fork_type"] != "REACTIVATE":
        expected = str(fork["expected_answer_facts"][0])
        _, entity = expected.split(":", 1)
        return _normalized_words(entity) in normalized and any(
            word in normalized for word in ("current", "present")
        )
    family = str(fork["prefix_id"]).split(":", 1)[0]
    failure_stems = ("fail", "error")
    success_stems = ("resolv", "success", "succeed", "work", "fix")
    if family == "F1_shell_switch":
        return _relation_appears_in_one_clause(
            answer,
            ("powershell",),
            wanted=failure_stems,
            opposite=success_stems,
        ) and _relation_appears_in_one_clause(
            answer,
            ("bash",),
            wanted=success_stems,
            opposite=failure_stems,
        )
    if family == "F2_failed_approach":
        return _relation_appears_in_one_clause(
            answer,
            ("approach", "a"),
            wanted=failure_stems,
            opposite=success_stems,
        ) and _relation_appears_in_one_clause(
            answer,
            ("approach", "b"),
            wanted=success_stems,
            opposite=failure_stems,
        )
    groups = _HISTORICAL_FACT_CONCEPTS_V2[family]
    return all(any(term in normalized for term in group) for group in groups)


def _answer_fact_match(
    answer: str,
    fork: Mapping[str, Any],
    scoring_protocol: str,
) -> bool:
    if scoring_protocol == SCORING_PROTOCOL_V1:
        return _answer_fact_match_v1(answer, fork)
    if scoring_protocol == SCORING_PROTOCOL_V2:
        return _answer_fact_match_v2(answer, fork)
    raise ValueError(f"unsupported scoring protocol: {scoring_protocol}")


def score_live_answer(
    answer: Mapping[str, Any],
    trial: Mapping[str, Any],
    fork: Mapping[str, Any],
    scoring_protocol: str = SCORING_PROTOCOL_V1,
) -> dict[str, Any]:
    opaque = {str(key): str(value) for key, value in trial["opaque_event_ids"].items()}
    unknown_evidence = sorted(set(answer["evidence_record_ids"]).difference(opaque))
    evidence_event_ids = sorted(
        opaque[item] for item in answer["evidence_record_ids"] if item in opaque
    )
    expected_scope = "historical" if fork["fork_type"] == "REACTIVATE" else "current"
    fact_match = _answer_fact_match(str(answer["answer"]), fork, scoring_protocol)
    scope_match = answer["fact_scope"] == expected_scope
    side_effect_safe = not bool(answer["would_repeat_side_effect"])
    required = set(map(str, fork.get("required_subgraph_event_ids", ())))
    cited = set(evidence_event_ids)
    anchor_ids = set(map(str, fork.get("required_anchor_ids", ())))
    required_evidence_recall = (
        len(required.intersection(cited)) / len(required) if required else 1.0
    )
    required_anchor_cited = not anchor_ids or bool(anchor_ids.intersection(cited))
    evidence_complete_for_answer = (
        scoring_protocol == SCORING_PROTOCOL_V1
        or (required_evidence_recall == 1.0 and required_anchor_cited)
    )
    answer_success = (
        fact_match
        and scope_match
        and side_effect_safe
        and not unknown_evidence
    )
    return {
        "scoring_protocol": scoring_protocol,
        "answer_success": answer_success,
        "answer_fact_match": fact_match,
        "fact_scope_match": scope_match,
        "side_effect_safe": side_effect_safe,
        "unknown_evidence_record_ids": unknown_evidence,
        "evidence_event_ids": evidence_event_ids,
        "required_evidence_recall": required_evidence_recall,
        "required_anchor_cited": required_anchor_cited,
        "evidence_complete_for_answer": evidence_complete_for_answer,
        "old_fact_used_as_current": (
            fork["fork_type"] == "REACTIVATE" and answer["fact_scope"] == "current"
        ),
    }


def summarize_live_results(
    results: Sequence[Mapping[str, Any]],
    config: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    expected = int(config["limits"]["trial_count"])
    by_method: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    by_method_fork: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for row in results:
        by_method[str(row["method_id"])].append(row)
        by_method_fork[(str(row["method_id"]), str(row["fork_type"]))].append(row)

    def rate(rows: Sequence[Mapping[str, Any]], field: str) -> float:
        return sum(bool(item.get(field)) for item in rows) / len(rows) if rows else 0.0

    summaries: dict[str, Any] = {}
    for method_id in LIVE_METHOD_IDS:
        rows = by_method[method_id]
        summaries[method_id] = {
            "trials": len(rows),
            "valid_response_rate": rate(rows, "valid_response"),
            "answer_success_rate": rate(rows, "answer_success"),
            "mean_input_tokens": (
                sum(int(item.get("input_tokens", 0)) for item in rows) / len(rows)
                if rows
                else 0.0
            ),
            "mean_output_tokens": (
                sum(int(item.get("output_tokens", 0)) for item in rows) / len(rows)
                if rows
                else 0.0
            ),
            "cost_cny": sum(float(item.get("cost_cny", 0.0)) for item in rows),
            "by_fork": {
                fork_type: {
                    "trials": len(by_method_fork[(method_id, fork_type)]),
                    "answer_success_rate": rate(
                        by_method_fork[(method_id, fork_type)], "answer_success"
                    ),
                }
                for fork_type in FORK_TYPES
            },
        }
    completed = len(results)
    usage_covered = sum(
        int(row.get("input_tokens", 0)) > 0 and int(row.get("output_tokens", 0)) >= 0
        for row in results
    )
    metrics = {
        "schema_version": "phase6_live_metrics_v1",
        "expected_trials": expected,
        "completed_trials": completed,
        "infrastructure_completion_rate": completed / expected,
        "usage_coverage": usage_covered / completed if completed else 0.0,
        "total_input_tokens": sum(int(row.get("input_tokens", 0)) for row in results),
        "total_output_tokens": sum(int(row.get("output_tokens", 0)) for row in results),
        "total_cost_cny": sum(float(row.get("cost_cny", 0.0)) for row in results),
        "methods": summaries,
        "model_counts": dict(Counter(str(row.get("model")) for row in results)),
    }
    gates = config["gates"]
    m0 = summaries["M0_full_history"]
    m5_rows = by_method["M5_lifecycle_causal_reactivation"]
    criteria = {
        "full_history_success": m0["answer_success_rate"]
        >= float(gates["full_history_success_rate_min"]),
        "infrastructure_completion": metrics["infrastructure_completion_rate"]
        >= float(gates["infrastructure_completion_rate_min"]),
        "request_hash_integrity": all(bool(row.get("request_hash_valid")) for row in results),
        "usage_coverage": metrics["usage_coverage"] >= float(gates["usage_coverage_min"]),
        "m5_duplicate_side_effects": sum(
            not bool(row.get("side_effect_safe")) for row in m5_rows
        )
        <= int(gates["m5_duplicate_side_effects_max"]),
        "m5_old_fact_used_as_current": sum(
            bool(row.get("old_fact_used_as_current")) for row in m5_rows
        )
        <= int(gates["m5_old_fact_used_as_current_max"]),
    }
    gate_report = {
        "schema_version": "phase6_live_gate_report_v1",
        "criteria": criteria,
        "decision": "pass" if all(criteria.values()) else "stop",
    }
    return metrics, gate_report
