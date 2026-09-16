"""Helpers for failure_episode annotation agents (dossier + row IO + self-check).

Run from anywhere: absolute paths are anchored to this file's directory.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

WORK = Path(__file__).resolve().parent
BASE = WORK.parent
CASES_PATH = BASE / "reviewer_a" / "cases.jsonl"
TEMPLATE_PATH = BASE / "reviewer_a" / "reviews.template.jsonl"

_cases: dict[str, dict] | None = None
_templates: dict[str, dict] | None = None


def _load_bytes(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_bytes().split(b"\n") if line.strip()]


def cases() -> dict[str, dict]:
    global _cases
    if _cases is None:
        _cases = {c["candidate_id"]: c for c in _load_bytes(CASES_PATH)}
    return _cases


def templates() -> dict[str, dict]:
    global _templates
    if _templates is None:
        _templates = {t["candidate_id"]: t for t in _load_bytes(TEMPLATE_PATH)}
    return _templates


def events(cid: str) -> list[dict]:
    return cases()[cid]["prefix"]["events"]


def event_index(cid: str) -> dict[str, dict]:
    return {e["source_event_id"]: e for e in events(cid)}


def dump(ev: Mapping) -> str:
    """Exact serialization the official validator matches error_signature against."""
    return json.dumps(ev.get("content"), ensure_ascii=False, default=str)


def show(cid: str, eid: str) -> None:
    """Print one event fully (index line + full JSON-dumped content)."""
    evs = events(cid)
    for i, ev in enumerate(evs):
        if ev["source_event_id"] == eid:
            print(f"#{i} {ev['source_event_id']} kind={ev.get('kind')} call_id={ev.get('call_id')} tool={ev.get('tool_name')}")
            print(dump(ev))
            return
    raise KeyError(f"event {eid} not found in {cid}")


def show_range(cid: str, start: int, end: int) -> None:
    """Print events[start:end] with full dumps."""
    for i, ev in enumerate(events(cid)):
        if start <= i < end:
            print(f"#{i} {ev['source_event_id']} kind={ev.get('kind')} call_id={ev.get('call_id')} tool={ev.get('tool_name')}")
            print(dump(ev))


def write_row(out_path: str | Path, cid: str, *, annotator: str, status: str,
              episode: dict[str, Any] | None = None, rejection_reason: str = "") -> None:
    """Merge a filled episode into the blank template row and append canonically."""
    row = json.loads(json.dumps(templates()[cid]))  # deep copy
    row["annotator"] = annotator
    row["annotation_status"] = status
    row["rejection_reason"] = rejection_reason
    if status == "annotated":
        assert episode is not None, "annotated rows need failure_episode"
        row["failure_episode"] = episode
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_bytes(out_path) if out_path.exists() else []
    existing = [r for r in existing if r["candidate_id"] != cid]
    existing.append(row)
    existing.sort(key=lambda r: r["candidate_id"])
    with out_path.open("w", encoding="utf-8", newline="\n") as fh:
        for r in existing:
            fh.write(json.dumps(r, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    print(f"wrote {cid} ({status}) -> {out_path}")


# ---------------- self-check (mirrors scripts/validate_failure_episode_annotations.py) ----------------

POLICY_KEYS = {"alternative_evidence_sets", "relevant_evidence_ids", "causal_paths"}
EPISODE_KEYS = {
    "scope", "failure_family", "recoverability", "error_signature",
    "diagnostic_evidence", "recovery_sequence", "resolution_evidence",
    "anchor_source_event_id", "initial_action_source_event_id",
    "initial_result_source_event_ids", "repair_steps", "resolution_source_event_ids",
    "required_core_source_event_ids", "optional_support_source_event_ids",
    "chain_policy", "query_policies",
}


def _vids(values: Any, visible: set[str], name: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(values, list) or (nonempty and not values):
        raise ValueError(f"{name} must be a{' nonempty' if nonempty else ''} list")
    result = list(map(str, values))
    if any(not v for v in result) or len(result) != len(set(result)):
        raise ValueError(f"{name} contains blank or duplicate event IDs")
    if not set(result) <= visible:
        unknown = sorted(set(result) - visible)
        raise ValueError(f"{name} references unknown events: {unknown}")
    return result


def _vpol(value: Any, visible: set[str], order: Mapping[str, int], name: str) -> None:
    if not isinstance(value, Mapping) or set(value) != POLICY_KEYS:
        raise ValueError(f"{name} has invalid fields")
    relevant = set(_vids(value["relevant_evidence_ids"], visible, f"{name}.relevant"))
    raw_alts = value["alternative_evidence_sets"]
    if not isinstance(raw_alts, list) or not raw_alts:
        raise ValueError(f"{name} needs an evidence alternative")
    alts = [_vids(v, relevant, f"{name}.alternative") for v in raw_alts]
    if len({frozenset(v) for v in alts}) != len(alts):
        raise ValueError(f"{name} has duplicate evidence alternatives")
    raw_paths = value["causal_paths"]
    if not isinstance(raw_paths, list) or len(raw_paths) != len(alts):
        raise ValueError(f"{name} needs one causal path per alternative")
    path_sets = []
    for path in raw_paths:
        if not isinstance(path, Mapping) or set(path) != {"evidence_ids", "constraints"}:
            raise ValueError(f"{name} has an invalid causal path")
        nodes = set(_vids(path["evidence_ids"], relevant, f"{name}.path"))
        path_sets.append(frozenset(nodes))
        edges = []
        for edge in path["constraints"]:
            if not isinstance(edge, list) or len(edge) != 2:
                raise ValueError(f"{name} causal edge must contain two event IDs")
            left, right = map(str, edge)
            if left == right or left not in nodes or right not in nodes:
                raise ValueError(f"{name} causal edge is outside its path")
            if order[left] >= order[right]:
                raise ValueError(f"{name} causal edge contradicts chronology: {left}->{right}")
            edges.append((left, right))
        if len(edges) != len(set(edges)):
            raise ValueError(f"{name} has duplicate causal edges")
    if set(path_sets) != {frozenset(v) for v in alts}:
        raise ValueError(f"{name} paths do not match evidence alternatives")


def check_episode(episode: Any, cid: str) -> None:
    case = cases()[cid]
    if not isinstance(episode, Mapping) or set(episode) != EPISODE_KEYS:
        raise ValueError("failure_episode fields differ from the v1 template")
    if episode["scope"] != "task_level_failure_episode":
        raise ValueError("scope must be task_level_failure_episode")
    for field in ("failure_family", "error_signature", "diagnostic_evidence",
                  "recovery_sequence", "resolution_evidence"):
        if not isinstance(episode[field], str) or not episode[field].strip():
            raise ValueError(f"failure_episode.{field} is required")
    if episode["recoverability"] not in {"R0", "R1", "R2", "R3"}:
        raise ValueError("recoverability must be R0/R1/R2/R3")
    evs = case["prefix"]["events"]
    by_id = {e["source_event_id"]: e for e in evs}
    visible = set(by_id)
    order = {e["source_event_id"]: i for i, e in enumerate(evs)}
    anchor, initial = str(episode["anchor_source_event_id"]), str(episode["initial_action_source_event_id"])
    if not anchor or anchor != initial or initial not in visible:
        raise ValueError("anchor must equal initial action")
    if by_id[initial].get("kind") != "tool_call":
        raise ValueError("initial action must be a tool_call")
    init_results = _vids(episode["initial_result_source_event_ids"], visible, "initial results")
    if any(order[v] <= order[initial] for v in init_results):
        raise ValueError("initial results must follow the initial action")
    serialized = "\n".join(dump(by_id[v]) for v in init_results)
    if episode["error_signature"] not in serialized:
        raise ValueError("error_signature is NOT a verbatim substring of the initial results dump")
    steps = episode["repair_steps"]
    if not isinstance(steps, list) or not steps:
        raise ValueError("need >=1 repair step")
    step_ids, prev = [], max(order[v] for v in init_results)
    final_results = []
    referenced = {initial, *init_results}
    for idx, step in enumerate(steps):
        if not isinstance(step, Mapping) or set(step) != {
            "step_id", "decision_source_event_ids", "action_source_event_id",
            "result_source_event_ids", "outcome", "semantic_change",
        }:
            raise ValueError(f"repair step {idx} fields differ from template")
        sid = str(step["step_id"])
        if not sid or sid in step_ids:
            raise ValueError("step_id must be nonempty and unique")
        step_ids.append(sid)
        decisions = _vids(step["decision_source_event_ids"], visible, f"step[{idx}].decisions", nonempty=False)
        action = str(step["action_source_event_id"])
        if action not in visible or by_id[action].get("kind") != "tool_call":
            raise ValueError(f"step {idx} action must be a known tool_call")
        results = _vids(step["result_source_event_ids"], visible, f"step[{idx}].results")
        if order[action] <= prev or any(order[v] >= order[action] for v in decisions):
            raise ValueError(f"step {idx} decision/action order invalid")
        if any(order[v] <= order[action] for v in results):
            raise ValueError(f"step {idx} results must follow the action")
        expected = "resolved" if idx == len(steps) - 1 else "intermediate_failure"
        if step["outcome"] != expected:
            raise ValueError(f"step {idx} outcome must be {expected}")
        if not isinstance(step["semantic_change"], str) or not step["semantic_change"].strip():
            raise ValueError(f"step {idx} semantic_change required")
        prev = max(order[v] for v in results)
        final_results = results
        referenced.update([*decisions, action, *results])
    resolution = _vids(episode["resolution_source_event_ids"], visible, "resolution evidence")
    if not set(resolution) <= set(final_results):
        raise ValueError("resolution evidence must come from the final repair result")
    core = _vids(episode["required_core_source_event_ids"], visible, "required core")
    if len(core) < 4 or [order[v] for v in core] != sorted(order[v] for v in core):
        raise ValueError("required core needs >=4 chronological events")
    optional = _vids(episode["optional_support_source_event_ids"], visible, "optional support", nonempty=False)
    if set(core) & set(optional):
        raise ValueError("core and optional support must be disjoint")
    _vpol(episode["chain_policy"], visible, order, "chain_policy")
    qps = episode["query_policies"]
    if not isinstance(qps, Mapping) or set(qps) != {"audit_recovery", "interactive_reacquisition"}:
        raise ValueError("query_policies incomplete")
    for qt, pol in qps.items():
        _vpol(pol, visible, order, f"query_policies.{qt}")
    referenced.update([*resolution, *core, *optional])
    rel_union = set(episode["chain_policy"]["relevant_evidence_ids"])
    for pol in qps.values():
        rel_union.update(pol["relevant_evidence_ids"])
    if not referenced <= rel_union:
        missing = sorted(referenced - rel_union)
        raise ValueError(f"events excluded from every relevance policy: {missing}")


def check_file(out_path: str | Path) -> list[str]:
    """Validate every row in a written output file. Returns list of error strings."""
    errors = []
    for row in _load_bytes(Path(out_path)):
        cid = row.get("candidate_id", "?")
        try:
            case = cases()[cid]
            for f in ("source", "repository", "task_id", "split", "trajectory_revision"):
                if row.get(f) != case.get(f):
                    raise ValueError(f"frozen metadata changed: {f}")
            if not str(row.get("annotator") or "").strip():
                raise ValueError("annotator required")
            status = str(row.get("annotation_status") or "")
            if status == "annotated":
                if str(row.get("rejection_reason") or "").strip():
                    raise ValueError("annotated row must not carry a rejection reason")
                check_episode(row.get("failure_episode"), cid)
            elif status == "rejected":
                if not str(row.get("rejection_reason") or "").strip():
                    raise ValueError("rejected row needs a reason")
            else:
                raise ValueError("status must be annotated or rejected")
        except (KeyError, TypeError, ValueError) as err:
            errors.append(f"{cid[:12]}: {err}")
    return errors


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 4 and sys.argv[1] == "show":
        show(sys.argv[2], sys.argv[3])
    elif len(sys.argv) == 4 and sys.argv[1] == "range":
        show_range(sys.argv[2], int(sys.argv[3].split(":")[0]), int(sys.argv[3].split(":")[1]))
    elif len(sys.argv) == 2:
        for e in check_file(sys.argv[1]):
            print("ERROR", e)
        print("checked", sys.argv[1])
