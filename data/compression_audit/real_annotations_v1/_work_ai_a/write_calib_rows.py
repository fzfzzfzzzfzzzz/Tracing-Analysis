"""Write the two calibration annotation rows (case 0 and case 9) for reviewer A."""
from __future__ import annotations

import json
from pathlib import Path

import case_tools as ct

OUT = Path(__file__).resolve().parent / "annotations" / "batch_00_09.jsonl"


def case0() -> dict:
    case = ct.case_by_index(0)
    return {
        "case_index": 0,
        "candidate_id": case["candidate_id"],
        "annotation_status": "annotated",
        "failure_chain": {
            "failure_family": "path_environment",
            "failed_action": "execute_bash",
            "failed_arguments": ct.parse_action_args(case, "t0044:action"),
            "error_signature": "AssertionError: True is not false (FAILED (failures=3), exit code 1)",
            "diagnostic_evidence": (
                "After the fix had been applied, the Django test runner still reports the three "
                "email-change token tests failing with 'AssertionError: True is not false' while "
                "the standalone scripts pass. A python -c probe prints 'Django location: "
                "/testbed/django/__init__.py', and viewing /testbed/django/contrib/auth/tokens.py "
                "lines 79-97 shows _make_hash_value still returns str(user.pk) + user.password + "
                "str(login_timestamp) + str(timestamp) with no email term, proving the edit had "
                "only been applied to the unused /workspace/django__django__3.2 copy."
            ),
            "switch_decision": (
                "Stop iterating on the /workspace checkout: runtests.py imports Django from "
                "/testbed/django, so the same email-hash change must be reapplied directly to "
                "/testbed/django/contrib/auth/tokens.py."
            ),
            "replacement_action": "str_replace_editor",
            "replacement_arguments": ct.parse_action_args(case, "t0055:action"),
            "resolution_evidence": (
                "Rerunning 'python runtests.py auth_tests.test_email_invalidation -v 2' reports "
                "test_token_invalidated_on_case_change, test_token_invalidated_on_email_change and "
                "test_token_invalidated_on_empty_to_real_email_change all ok, 'Ran 4 tests', 'OK', "
                "exit code 0 (previously FAILED (failures=3))."
            ),
            "recoverability": "R0",
            "ordered_source_event_ids": [
                "t0044:action", "t0044:observation", "t0045:action",
                "t0052:observation", "t0054:observation", "t0055:action",
                "t0056:observation",
            ],
            "evidence_source_event_ids_by_field": {
                "failed_action": ["t0044:action"],
                "failure_result": ["t0044:observation"],
                "failure_cause": ["t0052:observation", "t0054:observation"],
                "error_signature": ["t0044:observation"],
                "diagnostic_evidence": ["t0044:observation", "t0045:action", "t0052:observation", "t0054:observation"],
                "switch_decision": ["t0054:observation", "t0055:action"],
                "replacement_action": ["t0055:action"],
                "resolution_evidence": ["t0056:observation"],
            },
        },
    }


def case9() -> dict:
    case = ct.case_by_index(9)
    return {
        "case_index": 9,
        "candidate_id": case["candidate_id"],
        "annotation_status": "annotated",
        "failure_chain": {
            "failure_family": "parameter_schema",
            "failed_action": "str_replace_editor",
            "failed_arguments": ct.parse_action_args(case, "m0004:call:call_dnlCOlKmafxjO1GfzAWpCo7h"),
            "error_signature": "ERROR: The `view_range` parameter is not allowed when `path` points to a directory.",
            "diagnostic_evidence": (
                "The tool rejects the call outright with a schema error: 'ERROR: The `view_range` "
                "parameter is not allowed when `path` points to a directory.' The earlier plain "
                "view of the same path succeeded without view_range, isolating view_range as the "
                "only invalid argument."
            ),
            "switch_decision": (
                "Drop the disallowed view_range argument and repeat the directory view with the "
                "path alone."
            ),
            "replacement_action": "str_replace_editor",
            "replacement_arguments": ct.parse_action_args(case, "m0006:call:call_qkjevQKEcMr1XGlh9YLULddt"),
            "resolution_evidence": (
                "The retried view succeeds and returns the two-level directory listing of "
                "/workspace/modin-project__modin__0.25."
            ),
            "recoverability": "R1",
            "ordered_source_event_ids": [
                "m0002:call:call_93tjbOSNoOZNhoJqbupdHKKe",
                "m0003:result:call_93tjbOSNoOZNhoJqbupdHKKe",
                "m0004:call:call_dnlCOlKmafxjO1GfzAWpCo7h",
                "m0005:result:call_dnlCOlKmafxjO1GfzAWpCo7h",
                "m0006:call:call_qkjevQKEcMr1XGlh9YLULddt",
                "m0007:result:call_qkjevQKEcMr1XGlh9YLULddt",
            ],
            "evidence_source_event_ids_by_field": {
                "failed_action": ["m0004:call:call_dnlCOlKmafxjO1GfzAWpCo7h"],
                "failure_result": ["m0005:result:call_dnlCOlKmafxjO1GfzAWpCo7h"],
                "failure_cause": ["m0005:result:call_dnlCOlKmafxjO1GfzAWpCo7h"],
                "error_signature": ["m0005:result:call_dnlCOlKmafxjO1GfzAWpCo7h"],
                "diagnostic_evidence": [
                    "m0003:result:call_93tjbOSNoOZNhoJqbupdHKKe",
                    "m0005:result:call_dnlCOlKmafxjO1GfzAWpCo7h",
                ],
                "switch_decision": ["m0006:call:call_qkjevQKEcMr1XGlh9YLULddt"],
                "replacement_action": ["m0006:call:call_qkjevQKEcMr1XGlh9YLULddt"],
                "resolution_evidence": ["m0007:result:call_qkjevQKEcMr1XGlh9YLULddt"],
            },
        },
    }


OUT.parent.mkdir(exist_ok=True)
rows = [case0(), case9()]
with OUT.open("w", encoding="utf-8") as fh:
    for row in rows:
        fh.write(json.dumps(row, ensure_ascii=False) + "\n")
print("wrote", OUT, len(rows), "rows")
