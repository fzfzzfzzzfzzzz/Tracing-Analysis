from __future__ import annotations

import copy

import pytest

from tracegraph.benchmark.compression_audit.io import stable_digest
from tracegraph.benchmark.compression_audit.provisional import merge_single_ai_reviews


def _candidate(candidate_id: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "source": "swe_gym",
        "repository": "owner/repo",
        "task_id": "task-1",
        "split": "dev",
        "trajectory_revision": "revision",
        "prefix": {"events": [
            {
                "source_event_id": "failed-call",
                "call_id": "call-failed",
                "kind": "tool_call",
                "tool_name": "shell",
                "content": {"name": "shell", "arguments": {"command": "bad"}},
            },
            {
                "source_event_id": "failed-result",
                "call_id": "call-failed",
                "kind": "tool_result",
                "content": "bad argument",
            },
            {
                "source_event_id": "diagnostic",
                "kind": "assistant_message",
                "content": "the argument is invalid",
            },
            {
                "source_event_id": "replacement-call",
                "call_id": "call-replacement",
                "kind": "tool_call",
                "tool_name": "editor",
                "content": {"name": "editor", "arguments": {"path": "fixed.py"}},
            },
            {
                "source_event_id": "replacement-result",
                "call_id": "call-replacement",
                "kind": "tool_result",
                "content": "success",
            },
        ]},
    }


def _review(candidate: dict, status: str = "annotated") -> dict:
    row = {key: candidate[key] for key in (
        "candidate_id", "source", "repository", "task_id", "split",
        "trajectory_revision",
    )}
    row.update(
        annotator="reviewer_b_ai_glm52",
        annotation_status=status,
        failure_chain={
            "recoverability": "R1",
            "failed_action": "shell command that failed",
            "failed_arguments": {"command": "paraphrased"},
            "replacement_action": "editor change",
            "replacement_arguments": {"path": "paraphrased"},
            "evidence_source_event_ids_by_field": {
                "failed_action": ["failed-call"],
                "replacement_action": ["replacement-call"],
            },
        },
    )
    if status == "rejected":
        row["reject_reason"] = "incomplete chain"
    return row


def test_merge_single_ai_reviews_separates_accepted_and_rejected():
    candidates = [_candidate("a"), _candidate("b")]
    reviews = [_review(candidates[0]), _review(candidates[1], "rejected")]
    accepted, rejected = merge_single_ai_reviews(candidates, reviews)
    assert [row["candidate_id"] for row in accepted] == ["a"]
    assert accepted[0]["annotation_status"] == "adjudicated"
    assert accepted[0]["failure_chain"]["failed_action"] == "shell"
    assert accepted[0]["failure_chain"]["failed_arguments"] == {"command": "bad"}
    assert accepted[0]["failure_chain"]["replacement_action"] == "editor"
    assert accepted[0]["failure_chain"]["replacement_arguments"] == {"path": "fixed.py"}
    assert accepted[0]["single_ai_protocol_normalization"]["failed_action"][
        "review_value_changed"
    ]
    assert rejected[0]["candidate_id"] == "b"


def test_merge_single_ai_reviews_rejects_metadata_drift():
    candidate = _candidate("a")
    review = _review(candidate)
    changed = copy.deepcopy(review)
    changed["split"] = "test"
    with pytest.raises(ValueError, match="metadata differs"):
        merge_single_ai_reviews([candidate], [changed])


def _adjudication(candidate_id: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "prefix_id": f"real:swe_gym:{stable_digest(candidate_id)[:16]}",
        "adjudicator": "third_adjudicator_glm53_260916",
        "adjudication_status": "adjudicated",
        "selected_failed_action_source_event_id": "failed-call",
        "selected_replacement_action_source_event_id": "replacement-call",
        "failure_family": "parameter_schema",
        "error_signature": "bad argument",
        "diagnostic_evidence": "the argument is invalid",
        "switch_decision": "switch to the editor",
        "resolution_evidence": "the replacement succeeded",
        "ordered_source_event_ids": [
            "failed-call", "failed-result", "diagnostic", "replacement-call",
            "replacement-result",
        ],
        "evidence_source_event_ids_by_field": {
            "failed_action": ["failed-call"],
            "failure_result": ["failed-result"],
            "error_signature": ["failed-result"],
            "failure_cause": ["failed-result", "diagnostic"],
            "diagnostic_evidence": ["diagnostic"],
            "switch_decision": ["diagnostic"],
            "replacement_action": ["replacement-call"],
            "resolution_evidence": ["replacement-result"],
        },
        "recoverability": "R1",
        "reject_reason": "",
    }


def test_merge_single_ai_reviews_applies_third_ai_adjudication():
    candidate = _candidate("a")
    accepted, rejected = merge_single_ai_reviews(
        [candidate], [_review(candidate)], [_adjudication("a")]
    )
    assert not rejected
    row = accepted[0]
    assert row["development_label_source"] == "third_ai_adjudication"
    assert row["annotator"] == "third_adjudicator_glm53_260916"
    assert row["failure_chain"]["failed_action"] == "shell"
    assert row["failure_chain"]["failed_arguments"] == {"command": "bad"}
    assert row["failure_chain"]["replacement_action"] == "editor"
    assert row["failure_chain"]["replacement_arguments"] == {"path": "fixed.py"}


def test_merge_single_ai_reviews_accepts_four_event_core_chain():
    candidate = _candidate("a")
    adjudication = _adjudication("a")
    adjudication["ordered_source_event_ids"] = [
        "failed-call", "failed-result", "replacement-call", "replacement-result",
    ]
    accepted, rejected = merge_single_ai_reviews(
        [candidate], [_review(candidate)], [adjudication]
    )
    assert not rejected
    assert accepted[0]["failure_chain"]["ordered_source_event_ids"] == (
        adjudication["ordered_source_event_ids"]
    )


def test_merge_single_ai_reviews_rejects_nonverbatim_adjudicated_error():
    candidate = _candidate("a")
    adjudication = _adjudication("a")
    adjudication["error_signature"] = "invented error"
    with pytest.raises(ValueError, match="failed result"):
        merge_single_ai_reviews([candidate], [_review(candidate)], [adjudication])
