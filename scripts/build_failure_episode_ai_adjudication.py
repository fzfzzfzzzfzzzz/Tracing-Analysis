#!/usr/bin/env python3
"""Build the frozen development-only AI adjudication for failure episodes.

The decisions in this file are intentionally explicit and reviewable.  They are
not a replacement for the independent human adjudication required by formal v1.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from tracegraph.benchmark.compression_audit.io import load_jsonl
from tracegraph.plain_cli import PlainArgumentParser


ADJUDICATOR = "codex-third-ai-260917-r1"


# candidate prefix -> (source, rationale)
DECISIONS: dict[str, tuple[str, str]] = {
    "1c91155c": (
        "review_a",
        "A keeps the minimal task-level repair/verification closure and correctly "
        "treats the already-repaired constructor failure as history-only (R0).",
    ),
    "1f3abaff": (
        "synthesized",
        "Use A's task-level two-stage repair and R0 classification, but replace its "
        "generic error count with B's exact failing symbol message.",
    ),
    "296308d8": (
        "review_a",
        "A excludes pure file inspection from repair_steps while retaining the failed "
        "patch attempts, successful compatibility edit, and direct verification.",
    ),
    "333b2a9b": (
        "review_b",
        "B gives the smaller sufficient Django repair chain and correctly marks the "
        "non-replayable AMA trajectory as R0.",
    ),
    "3b63f034": (
        "review_a",
        "A preserves the complete import-repair progression with the smaller sufficient "
        "core; its literal error substring is valid.",
    ),
    "3ead3175": (
        "review_a",
        "A captures the three substantive alias-analysis fixes and the final successful "
        "mypy run, with R0 appropriate after the state-changing patch.",
    ),
    "46f238e9": (
        "review_b",
        "B anchors the episode at the first observed execution failure (m0020), not at "
        "the preceding successful edit call.",
    ),
    "4fd98262": (
        "review_b",
        "B is the more compact sufficient code-generation repair chain and uses R0 for "
        "the non-replayable AMA trajectory.",
    ),
    "52eaf9c8": (
        "review_a",
        "A keeps the final two verification commands as distinct stages and ends on the "
        "direct embedded-case success observation.",
    ),
    "5fccecdf": (
        "review_a",
        "A includes the edit confirmation in the core as well as the final successful "
        "reproduction run.",
    ),
    "6f8e78bc": (
        "review_a",
        "A ends the episode with the actual custom-lookups regression action/result pair; "
        "B attaches a later result to an earlier final action.",
    ),
    "7ca0d818": (
        "review_a",
        "A keeps the read-back action that establishes the verbatim replacement target "
        "before the successful edit.",
    ),
    "8de65b36": (
        "review_a",
        "The directly observed cause is a missing external dependency, and A includes "
        "the installation result before final verification.",
    ),
    "94d3bd0f": (
        "review_a",
        "A's stale-reference-hash family is more specific while both sides identify the "
        "same failure, edit, and successful rerun.",
    ),
    "9a912678": (
        "synthesized",
        "Use A's properly paired final edge-case verification, but normalize the "
        "non-replayable AMA trajectory from R2 to R0.",
    ),
    "a794195e": (
        "reject",
        "The initial search response is merely an unhelpful retrieval result, not a "
        "task-level failure; the trajectory is a successful multi-step search without a "
        "failure-repair-success episode.",
    ),
    "a7d14835": (
        "review_a",
        "A uses the task-specific ignored-timezone family and a clean final official-suite "
        "action/result pair.",
    ),
    "ac20aae1": (
        "review_a",
        "A includes the edit confirmation and final direct crop-size success while using "
        "the more specific bounding-box family.",
    ),
    "acb7ecd6": (
        "review_a",
        "A states the precise defect (uninformative validation error) and retains the "
        "minimal edit-plus-rerun closure.",
    ),
    "b18a7ab4": (
        "review_b",
        "B separates the final corrected edit history from the actual final verification "
        "command, so the resolved action/result pair is explicit.",
    ),
    "bfc56d53": (
        "synthesized",
        "Use B's exact timeout signature and compact fallback chain, but normalize the "
        "non-replayable AMA trajectory to R0.",
    ),
    "c44bdab1": (
        "synthesized",
        "B has the cleaner interpolation repair closure; the source state was patched, "
        "so the original failure is history-only and is normalized to R0.",
    ),
    "d00846f1": (
        "review_a",
        "A excludes pure source inspection from repair_steps and preserves the three "
        "state-changing indentation attempts through direct success.",
    ),
    "d1a903bb": (
        "review_a",
        "A correctly classifies the episode as a reproduction/test-patch failure and "
        "retains the directly observed parse error and successful final rerun.",
    ),
    "d4e7c0c0": (
        "reject",
        "The Crafter sequence is ordinary navigation and survival management and ends "
        "without task completion; 'you see nothing' is not a task-level failure anchor.",
    ),
    "de7d8593": (
        "review_b",
        "B represents undo, intermediate rerun, dtype repair, and final verification as "
        "four distinct causally ordered stages.",
    ),
    "e374ac5b": (
        "synthesized",
        "Use B's compact website-access recovery chain, but normalize the non-replayable "
        "AMA trajectory to R0.",
    ),
    "ea9b0e3a": (
        "review_a",
        "A uses R0 for the non-replayable AMA run and retains the workspace/testbed fix "
        "sequence through the comprehensive success check.",
    ),
    "f9ab0c2c": (
        "synthesized",
        "Use A's specific quota-exhaustion family and evidence chain, but normalize the "
        "non-replayable AMA trajectory to R0.",
    ),
    "f9e1b141": (
        "review_b",
        "B includes each parameter-fix stage and its intermediate evidence and correctly "
        "marks the patched constructor failure as R0.",
    ),
}


def _matching_decision(candidate_id: str) -> tuple[str, str]:
    matches = [value for prefix, value in DECISIONS.items() if candidate_id.startswith(prefix)]
    if len(matches) != 1:
        raise ValueError(f"candidate has {len(matches)} decisions: {candidate_id}")
    return matches[0]


def _synthesize(candidate_id: str, case: dict[str, Any]) -> dict[str, Any]:
    if candidate_id.startswith("1f3abaff"):
        episode = copy.deepcopy(case["review_a"]["failure_episode"])
        episode["error_signature"] = case["review_b"]["failure_episode"][
            "error_signature"
        ]
        return episode
    if candidate_id.startswith("9a912678"):
        episode = copy.deepcopy(case["review_a"]["failure_episode"])
    elif candidate_id.startswith(("bfc56d53", "c44bdab1", "e374ac5b")):
        episode = copy.deepcopy(case["review_b"]["failure_episode"])
    elif candidate_id.startswith("f9ab0c2c"):
        episode = copy.deepcopy(case["review_a"]["failure_episode"])
    else:
        raise ValueError(f"no synthesis rule for {candidate_id}")
    episode["recoverability"] = "R0"
    return episode


def build(cases_path: Path, template_path: Path, output_path: Path) -> None:
    if output_path.exists():
        raise FileExistsError(output_path)
    cases = load_jsonl(cases_path)
    templates = load_jsonl(template_path)
    case_map = {str(row["candidate_id"]): row for row in cases}
    if len(case_map) != len(cases):
        raise ValueError("duplicate adjudication cases")
    if len(DECISIONS) != len(cases):
        raise ValueError("decision table size differs from adjudication packet")
    rows: list[dict[str, Any]] = []
    for template in templates:
        row = copy.deepcopy(template)
        candidate_id = str(row["candidate_id"])
        case = case_map[candidate_id]
        selected, rationale = _matching_decision(candidate_id)
        row.update(
            {
                "adjudicator": ADJUDICATOR,
                "adjudicator_kind": "ai",
                "adjudication_status": "completed",
                "selected_source": selected,
                "rationale": rationale,
            }
        )
        if selected in {"review_a", "review_b"}:
            row["failure_episode"] = copy.deepcopy(
                case[selected]["failure_episode"]
            )
        elif selected == "synthesized":
            row["failure_episode"] = _synthesize(candidate_id, case)
        elif selected == "reject":
            row["rejection_reason"] = rationale
        else:
            raise ValueError(f"unsupported decision: {selected}")
        rows.append(row)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
            for row in rows
        ),
        encoding="utf-8",
        newline="\n",
    )


def main() -> int:
    parser = PlainArgumentParser(
        description="生成 development-only failure_episode AI 裁决结果。"
    )
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(args.cases, args.template, args.output)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
