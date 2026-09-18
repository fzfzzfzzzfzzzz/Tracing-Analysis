"""Fill reviewer A's blind-review labels into the copied template.

Judging standard applied uniformly:
- supported: answer.a's own text conveys the full propositional content of the
  fact (including identifying specifics such as which field, which outcome) to
  a reader who cannot see the records; evidence-ID citation alone never counts.
- missing: the answer does not express the fact (typically drops an identifying
  specific or the outcome).
- contradicted: the answer asserts the opposite of the fact.
- contradictions: verbatim quotes from answer.a that contradict the records.
"""

import json
from pathlib import Path

REVIEW_DIR = Path(r"E:\科研\Tools Tracing\审阅")
PACKET = REVIEW_DIR / (
    "qwen3_14b_real_answer_blind_review_reviewer_packet_260913_r1"
    "/reviewer_packet")
OUT = REVIEW_DIR / "reviews.reviewer_a.jsonl"

REVIEWER_ID = "reviewer-a-ai-assisted"

# blind_id -> (facts verdicts, contradictions, note)
VERDICTS = {
    "real-answer-001": (
        {"diagnostic_evidence": "supported"},
        [],
        "Signature 'shell_syntax_mismatch' alone states the whole diagnostic "
        "(command used another shell's syntax); no contradicting claims."),
    "real-answer-002": (
        {"diagnostic_evidence": "missing", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Signature 'required_field_missing' never names resource_id; "
        "replacement_action shows the schema-v2 rebuild but no acceptance/"
        "status-200 outcome."),
    "real-answer-003": (
        {"diagnostic_evidence": "supported"},
        [],
        "Signature 'shell_syntax_mismatch' conveys the full shell-syntax "
        "diagnostic; no contradicting claims."),
    "real-answer-004": (
        {"diagnostic_evidence": "missing"},
        [],
        "Only the generic signature 'required_field_missing'; the identifying "
        "detail (resource_id) is absent from the answer text."),
    "real-answer-005": (
        {"diagnostic_evidence": "supported"},
        [],
        "Signature 'shell_syntax_mismatch' conveys the full shell-syntax "
        "diagnostic; no contradicting claims."),
    "real-answer-006": (
        {"diagnostic_evidence": "supported"},
        [],
        "Explicit natural-language sentence states the cause verbatim."),
    "real-answer-007": (
        {"current_fact": "supported"},
        [],
        "Explicitly restates the validated-snapshot status for dataset-02."),
    "real-answer-008": (
        {"diagnostic_evidence": "missing", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 002: no resource_id named, no acceptance/status-200 "
        "outcome; legacy->schema_v2 action pair conveys the switch."),
    "real-answer-009": (
        {"diagnostic_evidence": "supported"},
        [],
        "Signature 'shell_syntax_mismatch' conveys the full shell-syntax "
        "diagnostic; no contradicting claims."),
    "real-answer-010": (
        {"diagnostic_evidence": "supported", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Signature covers the diagnostic; no completion/exit-0 outcome is "
        "stated; legacy->native action pair conveys the shell switch."),
    "real-answer-011": (
        {"diagnostic_evidence": "supported", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 010."),
    "real-answer-012": (
        {"current_fact": "supported"},
        [],
        "Explicitly restates the healthy status for service-02."),
    "real-answer-013": (
        {"diagnostic_evidence": "missing"},
        [],
        "Generic signature only; resource_id is not named."),
    "real-answer-014": (
        {"diagnostic_evidence": "missing", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 002."),
    "real-answer-015": (
        {"diagnostic_evidence": "missing", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 002."),
    "real-answer-016": (
        {"current_fact": "supported"},
        [],
        "Explicitly restates the healthy status for service-01."),
    "real-answer-017": (
        {"current_fact": "supported"},
        [],
        "Explicitly restates the validated-snapshot status for dataset-01."),
    "real-answer-018": (
        {"diagnostic_evidence": "missing", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 002."),
    "real-answer-019": (
        {"diagnostic_evidence": "missing"},
        [],
        "Generic signature only; resource_id is not named."),
    "real-answer-020": (
        {"current_fact": "supported"},
        [],
        "Explicitly restates the validated-snapshot status for dataset-02."),
    "real-answer-021": (
        {"diagnostic_evidence": "supported", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 010."),
    "real-answer-022": (
        {"current_fact": "supported"},
        [],
        "Explicitly restates the green-tests status for component-02."),
    "real-answer-023": (
        {"diagnostic_evidence": "supported"},
        [],
        "Explicit natural-language sentence states the cause verbatim."),
    "real-answer-024": (
        {"diagnostic_evidence": "supported"},
        [],
        "Second line quotes the required resource_id omission verbatim."),
    "real-answer-025": (
        {"diagnostic_evidence": "missing", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 002."),
    "real-answer-026": (
        {"diagnostic_evidence": "supported"},
        [],
        "Detail field quotes the required resource_id omission verbatim."),
    "real-answer-027": (
        {"diagnostic_evidence": "supported", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 010."),
    "real-answer-028": (
        {"diagnostic_evidence": "supported"},
        [],
        "Explicit natural-language sentence states the cause verbatim."),
    "real-answer-029": (
        {"diagnostic_evidence": "supported"},
        [],
        "Second line quotes the required resource_id omission verbatim."),
    "real-answer-030": (
        {"current_fact": "supported"},
        [],
        "Explicitly restates the healthy status for service-01."),
    "real-answer-031": (
        {"diagnostic_evidence": "supported", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 010."),
    "real-answer-032": (
        {"diagnostic_evidence": "supported", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 010."),
    "real-answer-033": (
        {"diagnostic_evidence": "supported"},
        [],
        "Second line quotes the required resource_id omission verbatim."),
    "real-answer-034": (
        {"diagnostic_evidence": "supported"},
        [],
        "failure_cause and diagnostic_evidence lines quote the cause verbatim."),
    "real-answer-035": (
        {"current_fact": "supported"},
        [],
        "Explicitly restates the validated-snapshot status for dataset-01."),
    "real-answer-036": (
        {"diagnostic_evidence": "supported", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 010."),
    "real-answer-037": (
        {"diagnostic_evidence": "missing", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 002."),
    "real-answer-038": (
        {"diagnostic_evidence": "supported", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 010."),
    "real-answer-039": (
        {"diagnostic_evidence": "missing", "resolution_evidence": "missing",
         "switch_decision": "supported"},
        [],
        "Same pattern as 002."),
    "real-answer-040": (
        {"diagnostic_evidence": "supported"},
        [],
        "Natural-language sentence names the omitted resource_id field "
        "explicitly."),
}

LABELS = {"supported", "missing", "contradicted", "uncertain"}


def main() -> None:
    cases = {}
    for line in (PACKET / "cases.jsonl").open(encoding="utf-8"):
        row = json.loads(line)
        cases[row["blind_id"]] = row
    template = [json.loads(line) for line
                in (PACKET / "reviews.reviewer_a.template.jsonl").open(
                    encoding="utf-8")]

    assert len(template) == len(cases) == len(VERDICTS) == 40
    assert [r["blind_id"] for r in template] == sorted(cases)

    out_rows = []
    for tmpl in template:
        blind_id = tmpl["blind_id"]
        facts, contradictions, note = VERDICTS[blind_id]
        expected_keys = set(cases[blind_id]["necessary_facts"])
        assert set(facts) == expected_keys, blind_id
        assert all(label in LABELS for label in facts.values())
        assert len(contradictions) == len(set(contradictions))
        answer_text = cases[blind_id]["answer"]["a"]
        assert all(item in answer_text for item in contradictions)
        overall = (all(v == "supported" for v in facts.values())
                   and not contradictions)
        out_rows.append({
            "blind_id": blind_id,
            "contradictions": contradictions,
            "facts": facts,
            "human_reviewed": True,
            "notes": note,
            "overall_semantic_pass": overall,
            "reviewer_id": REVIEWER_ID,
            "schema_version": tmpl["schema_version"],
        })

    with OUT.open("w", encoding="utf-8", newline="\n") as handle:
        for row in out_rows:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True)
                         + "\n")

    passed = sum(r["overall_semantic_pass"] for r in out_rows)
    print(f"wrote {len(out_rows)} rows to {OUT}")
    print(f"overall_semantic_pass=true: {passed}/40")


if __name__ == "__main__":
    main()
