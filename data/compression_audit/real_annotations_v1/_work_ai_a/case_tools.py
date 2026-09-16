"""Shared tooling for AI-assisted annotation of compression_audit_v1 reviewer A packet.

Provides case loading, digest rendering, and full-content zoom by event ID.
Scratch work only; never writes into the reviewer packet except the final
reviews.completed.jsonl assembly step.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

PACKET = Path(__file__).resolve().parents[1] / "_packet" / "reviewer_a"
CASES = PACKET / "cases.jsonl"

_ERROR_RE = re.compile(
    r"(Traceback|Traceback \(most recent call last\)|\bERROR\b|\bError\b|FAILED|"
    r"Failures?[:\s]|error:|Exception|\bdenied\b|No such file|not found|"
    r"No matches found|Invalid|invalid|cannot|Cannot|Could not|could not|"
    r"Unexpected|unexpected|missing|Missing|undefined|\bfail(ed|ure|s)?\b)",
)


def load_cases() -> list[dict]:
    cases = []
    with CASES.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def case_by_index(index: int) -> dict:
    return load_cases()[index]


def find_case(candidate_id: str) -> dict:
    for case in load_cases():
        if case["candidate_id"] == candidate_id:
            return case
    raise KeyError(candidate_id)


def _one_line(text: str, limit: int) -> str:
    text = str(text).replace("\\n", " ").replace("\n", " ⏎ ").replace("\r", " ")
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        return text[:limit] + f" …(+{len(text) - limit}ch)"
    return text


def _action_verb(action_str: str) -> str:
    m = re.match(r"\s*([a-zA-Z_][\w.]*):", str(action_str))
    return m.group(1) if m else ""


def event_summary(ev: dict, result_limit: int = 220, arg_limit: int = 200) -> str:
    kind = ev.get("kind", "")
    eid = ev.get("source_event_id", "?")
    content = ev.get("content")
    if kind == "tool_call":
        args = {}
        name = ev.get("tool_name") or ""
        if isinstance(content, dict):
            args = content.get("arguments", {}) or {}
            name = content.get("name") or name
        if "action" in args and isinstance(args["action"], str):
            verb = _action_verb(args["action"])
            body = _one_line(args["action"], arg_limit)
            flag = " !!!" if _ERROR_RE.search(args["action"]) else ""
            return f"{eid} | CALL {name}/{verb}: {body}{flag}"
        compact = _one_line(json.dumps(args, ensure_ascii=False), arg_limit)
        flag = " !!!" if _ERROR_RE.search(compact) else ""
        return f"{eid} | CALL {name} args={compact}{flag}"
    if kind == "tool_result":
        body = _one_line(content, result_limit)
        flag = " !!!ERR!!!" if _ERROR_RE.search(body) else ""
        return f"{eid} | OBS : {body}{flag}"
    if kind == "assistant_message":
        return f"{eid} | ASST: {_one_line(content, 260)}"
    if kind == "user_message":
        return f"{eid} | USER: {_one_line(content, 500)}"
    if kind == "system_message":
        return f"{eid} | SYS : {_one_line(content, 80)}"
    return f"{eid} | {kind}: {_one_line(content, 200)}"


def case_header(case: dict, index: int) -> str:
    ev = case["prefix"]["events"]
    return (
        f"CASE index={index} candidate_id={case['candidate_id']}\n"
        f"source={case['source']} repository={case['repository']} task_id={case['task_id']} "
        f"split={case['split']} events={len(ev)}\n"
        + "=" * 100
    )


def render_digest(case: dict, index: int) -> str:
    lines = [case_header(case, index)]
    for ev in case["prefix"]["events"]:
        lines.append(event_summary(ev))
    return "\n".join(lines) + "\n"


def find_event(case: dict, event_id: str) -> dict:
    for ev in case["prefix"]["events"]:
        if ev.get("source_event_id") == event_id:
            return ev
    raise KeyError(event_id)


def render_zoom(case: dict, event_ids: list[str], width: int = 100000) -> str:
    out = []
    for eid in event_ids:
        ev = find_event(case, eid)
        content = ev.get("content")
        kind = ev.get("kind")
        if isinstance(content, dict):
            body = json.dumps(content, ensure_ascii=False, indent=1)
        else:
            body = str(content)
        out.append(f"===== {eid} | {kind} | tool={ev.get('tool_name')} =====")
        out.append(body[:width])
        if len(body) > width:
            out.append(f"…(truncated +{len(body) - width}ch)")
    return "\n".join(out) + "\n"


def parse_action_args(case: dict, event_id: str) -> dict:
    """Return the inner arguments dict of an action event for annotation reuse."""
    ev = find_event(case, event_id)
    content = ev.get("content")
    if not isinstance(content, dict):
        raise ValueError(f"{event_id} is not a tool_call")
    args = dict(content.get("arguments", {}) or {})
    action = args.get("action")
    if isinstance(action, str):
        verb = _action_verb(action)
        body = action[len(verb) + 1:] if verb else action
        body = body.strip()
        if body.startswith("{"):
            try:
                inner = json.loads(body)
                if isinstance(inner, dict):
                    return {"_verb": verb, **inner}
            except json.JSONDecodeError:
                pass
        return {"_verb": verb, "raw": body}
    return args


def main(argv: list[str]) -> int:
    cmd = argv[1]
    if cmd == "args":
        idx = int(argv[2])
        case = case_by_index(idx)
        for eid in argv[3:]:
            print(f"===== {eid}")
            print(json.dumps(parse_action_args(case, eid), ensure_ascii=False, indent=1))
    elif cmd == "digest":
        idx = int(argv[2])
        case = case_by_index(idx)
        sys.stdout.write(render_digest(case, idx))
    elif cmd == "digestrange":
        lo, hi = int(argv[2]), int(argv[3])
        outdir = Path(argv[4])
        outdir.mkdir(parents=True, exist_ok=True)
        for idx in range(lo, hi):
            case = case_by_index(idx)
            path = outdir / f"case_{idx:03d}_{case['source']}_{case['task_id']}.txt"
            path.write_text(render_digest(case, idx), encoding="utf-8")
            print(path)
    elif cmd == "zoom":
        idx = int(argv[2])
        case = case_by_index(idx)
        sys.stdout.write(render_zoom(case, argv[3:]))
    elif cmd == "meta":
        for idx, case in enumerate(load_cases()):
            ev = case["prefix"]["events"]
            print(
                idx, case["source"], case["repository"], case["task_id"],
                case["split"], len(ev), case["candidate_id"][:12],
            )
    else:
        print("unknown command", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
