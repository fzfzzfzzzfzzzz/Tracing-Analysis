"""Build compact per-case dossiers for failure_episode annotation agents."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

BASE = Path(r"E:\科研\Tools Tracing\审阅\failure_episode_v1_260916_r1")
WORK = BASE / "_ai_annotation_r1"
DOSSIER_DIR = WORK / "dossiers"
CASES = BASE / "reviewer_a" / "cases.jsonl"

ERROR_RE = re.compile(
    r"error|Error|ERROR|FAILED|Failed|failed|Traceback|exit code [1-9]"
    r"|AssertionError|Exception|No such file|not found|ENOENT|SyntaxError"
    r"|IndentationError|ModuleNotFoundError|AttributeError|TypeError|ValueError"
)


def dump_content(event: dict) -> str:
    return json.dumps(event.get("content"), ensure_ascii=False, default=str)


def trunc(text: str, head: int, tail: int = 0) -> str:
    if len(text) <= head + tail:
        return text
    mid = f" ...[TRUNCATED {len(text) - head - tail} chars]... " if tail else f" ...[TRUNC {len(text)-head}]"
    return text[:head] + mid + (text[-tail:] if tail else "")


def build_dossier(case: dict, index: int) -> str:
    prefix = case["prefix"]
    events = prefix["events"]
    out = []
    out.append(f"=== CASE {index} ===")
    out.append(f"candidate_id: {case['candidate_id']}")
    out.append(
        f"source: {case['source']} | repository: {case['repository']} | "
        f"task_id: {case['task_id']} | split: {case['split']}"
    )
    out.append(f"trajectory_revision: {case['trajectory_revision']}")
    out.append(f"event_count: {len(events)}")
    cf = str(prefix.get("current_fact") or "")
    if cf:
        out.append(f"current_fact: {trunc(cf, 400)}")
    env = prefix.get("environment_snapshot") or {}
    qa = env.get("qa_pairs") or []
    if qa:
        out.append(f"environment_qa_pairs ({len(qa)}):")
        for pair in qa[:14]:
            if isinstance(pair, dict):
                q = trunc(str(pair.get("question") or pair.get("q") or ""), 140)
                a = trunc(str(pair.get("answer") or pair.get("a") or ""), 140)
                out.append(f"  Q: {q}")
                out.append(f"  A: {a}")
    out.append("--- EVENTS ---")
    n = len(events)
    for i, ev in enumerate(events):
        eid = ev.get("source_event_id") or ""
        kind = ev.get("kind") or ""
        call_id = ev.get("call_id") or ""
        tool = ev.get("tool_name") or ""
        tok = ev.get("token_count")
        dump = dump_content(ev)
        is_err = bool(ERROR_RE.search(dump)) and kind == "tool_result"
        if n - i <= 3:
            body = trunc(dump, 1600, 400)
        elif kind == "tool_result":
            body = trunc(dump, 450, 250)
        elif kind == "tool_call":
            body = trunc(dump, 450)
        else:
            body = trunc(dump, 300)
        flag = "!! " if is_err else ""
        out.append(
            f"{flag}#{i} {eid} kind={kind} call_id={call_id} tool={tool} tok={tok}"
        )
        out.append(f"    C: {body}")
    return "\n".join(out) + "\n"


def main() -> None:
    DOSSIER_DIR.mkdir(parents=True, exist_ok=True)
    raw = CASES.read_bytes()
    cases = [json.loads(line) for line in raw.split(b"\n") if line.strip()]
    index = {}
    for i, case in enumerate(cases, 1):
        text = build_dossier(case, i)
        name = f"case_{i:03d}_{case['candidate_id'][:8]}.txt"
        (DOSSIER_DIR / name).write_text(text, encoding="utf-8")
        index[case["candidate_id"]] = name
    (WORK / "case_index.json").write_text(
        json.dumps(index, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    total = sum(p.stat().st_size for p in DOSSIER_DIR.glob("*.txt"))
    print(f"wrote {len(index)} dossiers, total {total/1024:.0f} KB, avg {total/1024/len(index):.0f} KB")


if __name__ == "__main__":
    sys.exit(main())
