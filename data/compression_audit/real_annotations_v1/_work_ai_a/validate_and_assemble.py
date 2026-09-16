"""Validate all annotation batch rows and assemble reviews.completed.jsonl.

Checks (mirrors real_validation.adjudicated_complete where applicable):
- full coverage of the 100 template rows, candidate_id exact match
- annotated rows: 12 template keys, required non-empty fields, recoverability
  in R0-R3, ordered_source_event_ids >=5 unique ascending and existing,
  evidence_source_event_ids_by_field with the six required keys, all evidence
  IDs existing, action/argument consistency with the referenced call events
- rejected rows: non-empty reject_reason
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import case_tools as ct

WORK = Path(__file__).resolve().parent
ANNOT_DIR = WORK / "annotations"
TEMPLATE = ct.PACKET / "reviews.template.jsonl"

FAMILIES = {
    "shell_syntax", "parameter_schema", "permission_policy", "stale_state",
    "dependency_version", "patch_test", "path_environment", "timeout_resource",
    "partial_side_effect", "multi_failure_recovery",
}
REQUIRED_EVIDENCE_KEYS = {
    "failed_action", "failure_cause", "diagnostic_evidence",
    "switch_decision", "replacement_action", "resolution_evidence",
}
REQUIRED_TEXT_FIELDS = (
    "failure_family", "failed_action", "error_signature", "diagnostic_evidence",
    "switch_decision", "replacement_action", "resolution_evidence",
    "recoverability",
)
TEMPLATE_KEYS = {
    "failure_family", "failed_action", "failed_arguments", "error_signature",
    "diagnostic_evidence", "evidence_source_event_ids_by_field",
    "ordered_source_event_ids", "recoverability", "replacement_action",
    "replacement_arguments", "resolution_evidence", "switch_decision",
}

problems: list[str] = []
rows: dict[int, dict] = {}
status_counts = {"annotated": 0, "rejected": 0}

for path in sorted(ANNOT_DIR.glob("batch_*.jsonl")):
    with path.open(encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                problems.append(f"{path}:{lineno}: invalid JSON: {exc}")
                continue
            idx = row.get("case_index")
            if not isinstance(idx, int) or idx in rows:
                problems.append(f"{path}:{lineno}: bad or duplicate case_index {idx!r}")
                continue
            rows[idx] = row

with TEMPLATE.open(encoding="utf-8") as fh:
    template_rows = [json.loads(line) for line in fh if line.strip()]

if len(template_rows) != 100:
    problems.append(f"template rows {len(template_rows)} != 100")

for idx, trow in enumerate(template_rows):
    ann = rows.get(idx)
    if ann is None:
        problems.append(f"case {idx}: missing annotation")
        continue
    if ann.get("candidate_id") != trow.get("candidate_id"):
        problems.append(f"case {idx}: candidate_id mismatch")
    status = ann.get("annotation_status")
    if status not in status_counts:
        problems.append(f"case {idx}: bad annotation_status {status!r}")
        continue
    status_counts[status] += 1
    if status == "rejected":
        if not str(ann.get("reject_reason") or "").strip():
            problems.append(f"case {idx}: rejected without reject_reason")
        continue

    chain = ann.get("failure_chain")
    if not isinstance(chain, dict):
        problems.append(f"case {idx}: failure_chain missing")
        continue
    if set(chain) != TEMPLATE_KEYS:
        problems.append(
            f"case {idx}: failure_chain keys differ: extra={sorted(set(chain) - TEMPLATE_KEYS)} "
            f"missing={sorted(TEMPLATE_KEYS - set(chain))}"
        )
        continue
    for field in REQUIRED_TEXT_FIELDS:
        if not str(chain.get(field) or "").strip():
            problems.append(f"case {idx}: empty {field}")
    if chain.get("recoverability") not in ("R0", "R1", "R2", "R3"):
        problems.append(f"case {idx}: recoverability {chain.get('recoverability')!r}")
    if chain.get("failure_family") not in FAMILIES:
        problems.append(f"case {idx}: failure_family {chain.get('failure_family')!r}")

    case = ct.case_by_index(idx)
    events = {ev["source_event_id"]: ev for ev in case["prefix"]["events"]}
    order = {eid: i for i, eid in enumerate(events)}

    ordered = chain.get("ordered_source_event_ids") or []
    if not isinstance(ordered, list) or len(ordered) < 5:
        problems.append(f"case {idx}: ordered_source_event_ids len {len(ordered)} < 5")
    if len(set(ordered)) != len(ordered):
        problems.append(f"case {idx}: ordered ids not unique")
    for eid in ordered:
        if eid not in events:
            problems.append(f"case {idx}: ordered id not in events: {eid}")
    positions = [order.get(eid, -1) for eid in ordered]
    if positions != sorted(positions):
        problems.append(f"case {idx}: ordered ids not in trajectory order")

    by_field = chain.get("evidence_source_event_ids_by_field")
    if not isinstance(by_field, dict):
        problems.append(f"case {idx}: evidence_source_event_ids_by_field missing")
        by_field = {}
    for key in REQUIRED_EVIDENCE_KEYS:
        vals = by_field.get(key)
        if not vals or not isinstance(vals, list):
            problems.append(f"case {idx}: evidence key {key} empty")
    for key, vals in by_field.items():
        for eid in vals or []:
            if eid not in events:
                problems.append(f"case {idx}: evidence[{key}] id missing: {eid}")

    # action/argument consistency with referenced call events
    def check_action_side(field: str, arg_field: str, action_name_field: str) -> None:
        ids = by_field.get(field) or []
        call_ids = [e for e in ids if e in events and events[e]["kind"] == "tool_call"]
        if not call_ids:
            problems.append(f"case {idx}: {field} evidence has no tool_call event")
            return
        claimed = chain.get(arg_field)
        if not isinstance(claimed, dict):
            problems.append(f"case {idx}: {arg_field} not a dict")
            return
        matches = []
        for eid in call_ids:
            parsed = ct.parse_action_args(case, eid)
            if claimed == parsed:
                matches.append((eid, parsed))
        if not matches:
            problems.append(
                f"case {idx}: {arg_field} matches none of the parsed args of {call_ids}"
            )
            return
        eid, parsed = matches[-1]
        name = str(chain.get(action_name_field) or "")
        if case["source"] == "ama_bench":
            verb = parsed.get("_verb", "")
            if name and verb and name != verb:
                problems.append(f"case {idx}: {action_name_field}={name!r} != verb {verb!r}")
        else:
            tool = events[eid].get("tool_name") or (events[eid].get("content") or {}).get("name")
            if name and tool and name != tool:
                problems.append(f"case {idx}: {action_name_field}={name!r} != tool {tool!r}")

    check_action_side("failed_action", "failed_arguments", "failed_action")
    check_action_side("replacement_action", "replacement_arguments", "replacement_action")

    # resolution evidence should reference at least one observation/result event
    res_ids = by_field.get("resolution_evidence") or []
    if res_ids and not any(
        e in events and events[e]["kind"] in ("tool_result",) for e in res_ids
    ):
        problems.append(f"case {idx}: resolution_evidence lacks an observation event")

print(f"covered {len(rows)}/100 | annotated={status_counts['annotated']} "
      f"rejected={status_counts['rejected']} | problems={len(problems)}")
for p in problems:
    print("PROBLEM:", p)

if "--write" in sys.argv and not problems:
    annotator = "ai_substitute_glm_260915"
    out_path = ct.PACKET / "reviews.completed.jsonl"
    with out_path.open("w", encoding="utf-8", newline="\n") as out:
        for idx, trow in enumerate(template_rows):
            ann = rows[idx]
            out_row = dict(trow)
            out_row["annotation_status"] = ann["annotation_status"]
            out_row["annotator"] = annotator
            if ann["annotation_status"] == "annotated":
                out_row["failure_chain"] = ann["failure_chain"]
            else:
                out_row["failure_chain"] = {
                    k: ({} if k.endswith("arguments") else ([] if "ids" in k else ""))
                    for k in TEMPLATE_KEYS
                }
                out_row["reject_reason"] = ann.get("reject_reason", "")
            out.write(json.dumps(out_row, ensure_ascii=False) + "\n")
    print("wrote", out_path)
