from __future__ import annotations

import json
from pathlib import Path

from tracegraph.phase6_live import (
    LIVE_METHOD_IDS,
    choose_pilot_prefix_ids,
    parse_submit_answer,
    prepare_request_template,
    score_live_answer,
    theoretical_maximum_cost_cny,
    validate_live_config,
)


ROOT = Path(__file__).resolve().parents[1]


def _prefix() -> dict:
    return {
        "graph": {
            "nodes": [
                {
                    "node_id": "semantic-current-id",
                    "node_type": "observation",
                    "step_id": 2,
                    "content": {"status": "current", "detail": "x" * 1000},
                    "metadata": {},
                },
                {
                    "node_id": "semantic-history-id",
                    "node_type": "error",
                    "step_id": 1,
                    "content": {"error": "shell_syntax_error", "detail": "y" * 1000},
                    "metadata": {"guard_text": "Avoid powershell: shell_syntax_error"},
                },
            ]
        }
    }


def _projection() -> dict:
    return {
        "raw_event_ids": ["semantic-current-id"],
        "injected_event_ids": [],
        "represented_event_ids": ["semantic-history-id"],
        "masked_event_ids": [],
        "current_fact_ids": ["semantic-current-id"],
        "historical_fact_ids": ["semantic-history-id"],
        "lifecycle": {
            "records": [
                {"event_id": "semantic-current-id", "state": "active"},
                {"event_id": "semantic-history-id", "state": "dormant"},
            ]
        },
    }


def test_frozen_sample_has_two_prefixes_per_family_and_balanced_variants() -> None:
    prefix_ids = [
        f"{family}:{variant}"
        for family in (
            "F1_shell_switch",
            "F2_failed_approach",
            "F3_goal_resume",
            "F4_explainable_process",
            "F5_state_supersession",
            "F6_side_effect_audit",
        )
        for variant in ("small_cheap", "small_costly", "large_cheap", "large_costly")
    ]
    selected = choose_pilot_prefix_ids(prefix_ids)
    assert len(selected) == 12
    variants = [item.split(":", 1)[1] for item in selected]
    assert {variant: variants.count(variant) for variant in set(variants)} == {
        "small_cheap": 3,
        "small_costly": 3,
        "large_cheap": 3,
        "large_costly": 3,
    }


def test_request_uses_opaque_ids_and_fixed_handoff_is_suffix_independent() -> None:
    first, mapping = prepare_request_template(
        _prefix(), _projection(), {"text": "first request"}, "M6_fixed_handoff"
    )
    second, _ = prepare_request_template(
        _prefix(), _projection(), {"text": "different future"}, "M6_fixed_handoff"
    )
    first_payload = json.loads(first["messages"][1]["content"])
    second_payload = json.loads(second["messages"][1]["content"])
    assert first_payload["records"] == second_payload["records"]
    assert set(mapping.values()) == {"R001", "R002"}
    assert "semantic-history-id" not in first["messages"][1]["content"]
    assert "x" * 100 not in first["messages"][1]["content"]


def test_parse_and_score_tool_answer() -> None:
    response = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "submit_answer",
                                "arguments": json.dumps(
                                    {
                                        "answer": (
                                            "PowerShell failed and Bash succeeded, resolving the issue."
                                        ),
                                        "evidence_record_ids": ["R001", "R002"],
                                        "fact_scope": "historical",
                                        "would_repeat_side_effect": False,
                                    }
                                ),
                            }
                        }
                    ]
                }
            }
        ]
    }
    answer = parse_submit_answer(response)
    trial = {
        "opaque_event_ids": {
            "R001": "failed-result",
            "R002": "successful-result",
        }
    }
    fork = {
        "prefix_id": "F1_shell_switch:small_cheap",
        "fork_type": "REACTIVATE",
        "required_subgraph_event_ids": ["failed-result", "successful-result"],
        "required_anchor_ids": ["failed-result"],
        "expected_answer_facts": [
            "PowerShell syntax failed; Bash-compatible syntax resolved it."
        ],
    }
    score = score_live_answer(answer, trial, fork)
    assert score["answer_success"] is True
    assert score["required_evidence_recall"] == 1.0
    assert score["required_anchor_cited"] is True


def test_live_config_matches_authorization_and_theoretical_cost_is_below_cap() -> None:
    config = json.loads(
        (ROOT / "configs" / "phase6_live_qwen38_27b_v1.json").read_text(encoding="utf-8")
    )
    validate_live_config(config)
    assert tuple(config["methods"]) == LIVE_METHOD_IDS
    assert theoretical_maximum_cost_cny(config) < 100.0
