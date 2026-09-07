"""Candidate mining for the human-verified real portion of compression_audit_v1.

Heuristics in this module may propose windows, but they never create benchmark
gold.  Every selected record remains blocked on two independent annotations and
adjudication in :mod:`tracegraph.compression_audit`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .capture import estimate_tokens
from .benchmark.compression_audit.dataset import (
    BENCHMARK_ID,
    canonical_json,
    file_sha256,
    git_provenance,
    implementation_provenance,
    load_jsonl,
    stable_digest,
    write_file_manifest,
)


CANDIDATE_SCHEMA_VERSION = "compression_audit_real_candidate_v1"
FAILURE_RE = re.compile(
    r"(?:traceback|exception|permission denied|no such file|not found|timed?\s*out|"
    r"invalid (?:argument|option|schema)|non[- ]zero|exit (?:code|status)\s*[:=]?\s*[1-9]|"
    r"command failed|test(?:s)? failed|\berror\b|\bfatal\b)",
    re.IGNORECASE,
)
SUCCESS_RE = re.compile(
    r"(?:exit (?:code|status)\s*[:=]?\s*0|\bpassed\b|\bsuccess(?:ful(?:ly)?)?\b|"
    r"patch applied|tests? (?:all )?pass|resolved\s*[:=]\s*true)",
    re.IGNORECASE,
)
SIDE_EFFECT_RE = re.compile(
    r"(?:write|apply_patch|delete|remove|move|deploy|publish|send|create|execute|bash|shell)",
    re.IGNORECASE,
)


def _json_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _list(value: Any) -> list[Any]:
    parsed = _json_value(value)
    return list(parsed) if isinstance(parsed, list) else []


def _mapping(value: Any) -> dict[str, Any]:
    parsed = _json_value(value)
    return dict(parsed) if isinstance(parsed, dict) else {}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return canonical_json(value)


def load_source_rows(path: Path) -> list[dict[str, Any]]:
    """Load an immutable local JSON/JSONL/Parquet export of a pinned dataset."""

    suffix = path.suffix.lower()
    if suffix in {".jsonl", ".ndjson"}:
        return load_jsonl(path)
    if suffix == ".json":
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, list):
            rows = value
        elif isinstance(value, dict):
            rows = value.get("rows") or value.get("data")
        else:
            rows = None
        if not isinstance(rows, list) or not all(isinstance(item, dict) for item in rows):
            raise ValueError(f"JSON source must contain an object-row list: {path}")
        return [dict(item) for item in rows]
    if suffix == ".parquet":
        try:
            import pyarrow.parquet as pq
        except ImportError as error:  # pragma: no cover - depends on optional analysis extra
            raise RuntimeError("Parquet input requires the project analysis extra") from error
        # Keep nested messages/calls as Python lists. Pandas otherwise exposes
        # these Arrow list columns as NumPy arrays, which silently lose events.
        return [dict(item) for item in pq.read_table(path).to_pylist()]
    raise ValueError(f"unsupported real trajectory source: {path}")


def _event(
    source_event_id: str,
    kind: str,
    content: Any,
    *,
    call_id: str | None = None,
    tool_name: str | None = None,
    side_effect: bool = False,
    source_message_ordinal: int | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "source_event_id": source_event_id,
        "kind": kind,
        "content": content,
        "causal_role": "unclassified",
        "token_count": estimate_tokens(content),
        "side_effect": side_effect,
    }
    if call_id:
        value["call_id"] = call_id
    if tool_name:
        value["tool_name"] = tool_name
    if source_message_ordinal is not None:
        value["source_message_ordinal"] = source_message_ordinal
    return value


def _tool_call(call: Mapping[str, Any], fallback_id: str) -> tuple[str, str, dict[str, Any]]:
    function = _mapping(call.get("function"))
    name = str(function.get("name") or call.get("name") or "unknown_tool")
    arguments = _mapping(function.get("arguments") or call.get("arguments"))
    if not arguments and function.get("arguments") not in (None, ""):
        arguments = {"raw": function.get("arguments")}
    call_id = str(call.get("id") or call.get("call_id") or fallback_id)
    return call_id, name, arguments


def normalize_swe_gym_events(
    row: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize OpenAI-style OpenHands messages while retaining every source message."""

    raw_messages = _list(row.get("messages"))
    events: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    pending: list[str] = []
    known_calls: set[str] = set()
    for message_index, raw in enumerate(raw_messages):
        if not isinstance(raw, dict):
            raw = {"role": "unknown", "content": raw}
        message = dict(raw)
        messages.append(message)
        role = str(message.get("role") or "unknown")
        content = message.get("content")
        if role == "tool":
            explicit = str(message.get("tool_call_id") or "")
            call_id = explicit if explicit in known_calls else (pending.pop(0) if pending else explicit)
            if not call_id:
                call_id = f"m{message_index:04d}-unpaired"
            events.append(
                _event(
                    f"m{message_index:04d}:result:{call_id}",
                    "tool_result",
                    content,
                    call_id=call_id,
                    source_message_ordinal=message_index + 1,
                )
            )
            if call_id in pending:
                pending.remove(call_id)
            continue
        if content not in (None, ""):
            events.append(
                _event(
                    f"m{message_index:04d}:message",
                    f"{role}_message",
                    content,
                    source_message_ordinal=message_index + 1,
                )
            )
        for call_index, raw_call in enumerate(_list(message.get("tool_calls"))):
            if not isinstance(raw_call, dict):
                continue
            call_id, name, arguments = _tool_call(
                raw_call, f"m{message_index:04d}-c{call_index:02d}"
            )
            known_calls.add(call_id)
            pending.append(call_id)
            events.append(
                _event(
                    f"m{message_index:04d}:call:{call_id}",
                    "tool_call",
                    {"name": name, "arguments": arguments},
                    call_id=call_id,
                    tool_name=name,
                    side_effect=bool(SIDE_EFFECT_RE.search(name)),
                    source_message_ordinal=message_index + 1,
                )
            )
    return events, messages


def normalize_ama_events(
    row: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    events: list[dict[str, Any]] = []
    messages: list[dict[str, Any]] = []
    for fallback_index, raw in enumerate(_list(row.get("trajectory"))):
        if not isinstance(raw, dict):
            continue
        turn = int(raw.get("turn_idx", fallback_index))
        call_id = f"turn-{turn:04d}"
        action = raw.get("action")
        observation = raw.get("observation")
        events.append(
            _event(
                f"t{turn:04d}:action",
                "tool_call",
                {"name": "environment_action", "arguments": {"action": action}},
                call_id=call_id,
                tool_name="environment_action",
                side_effect=False,
                source_message_ordinal=len(messages) + 1,
            )
        )
        events.append(
            _event(
                f"t{turn:04d}:observation",
                "tool_result",
                observation,
                call_id=call_id,
                tool_name="environment_action",
                source_message_ordinal=len(messages) + 2,
            )
        )
        messages.extend(
            (
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": "environment_action",
                                "arguments": canonical_json({"action": action}),
                            },
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _text(observation),
                },
            )
        )
    return events, messages


def _paired_actions(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    calls = {
        str(item.get("call_id")): (index, item)
        for index, item in enumerate(events)
        if item.get("kind") == "tool_call" and item.get("call_id")
    }
    results = {
        str(item.get("call_id")): (index, item)
        for index, item in enumerate(events)
        if item.get("kind") in {"tool_result", "observation", "error"}
        and item.get("call_id")
    }
    pairs = []
    for call_id, (call_index, call) in calls.items():
        if call_id not in results:
            continue
        result_index, result = results[call_id]
        if result_index <= call_index:
            continue
        content = _mapping(call.get("content"))
        pairs.append(
            {
                "call_index": call_index,
                "result_index": result_index,
                "call_event_id": str(call["source_event_id"]),
                "result_event_id": str(result["source_event_id"]),
                "tool_name": str(call.get("tool_name") or content.get("name") or ""),
                "arguments": _mapping(content.get("arguments")),
                "result_text": _text(result.get("content")),
            }
        )
    return sorted(pairs, key=lambda item: int(item["call_index"]))


def _failure_score(text: str) -> int:
    normalized = " ".join(text.split())
    if not normalized:
        return 0
    if SUCCESS_RE.search(normalized) and not FAILURE_RE.search(normalized):
        return 0
    return 2 if FAILURE_RE.search(normalized) else 0


def _action_signature(pair: Mapping[str, Any]) -> str:
    return stable_digest(
        {"tool_name": pair.get("tool_name"), "arguments": pair.get("arguments")}
    )


def propose_failure_window(
    events: Sequence[Mapping[str, Any]],
    *,
    resolved: bool,
    allow_stagnation: bool = False,
) -> dict[str, Any] | None:
    """Propose one review window; the result is explicitly not a gold label."""

    pairs = _paired_actions(events)
    candidates: list[tuple[int, dict[str, Any]]] = []
    previous_result = ""
    for index, failed in enumerate(pairs):
        failure_score = _failure_score(str(failed["result_text"]))
        stagnant = bool(
            allow_stagnation
            and previous_result
            and stable_digest(previous_result) == stable_digest(failed["result_text"])
        )
        previous_result = str(failed["result_text"])
        if failure_score == 0 and not stagnant:
            continue
        for replacement in pairs[index + 1 :]:
            if _action_signature(replacement) == _action_signature(failed):
                continue
            replacement_failure = _failure_score(str(replacement["result_text"]))
            changed = stable_digest(replacement["result_text"]) != stable_digest(
                failed["result_text"]
            )
            explicit_success = bool(SUCCESS_RE.search(str(replacement["result_text"])))
            if replacement_failure or not (explicit_success or changed or resolved):
                continue
            score = failure_score * 10 + int(stagnant) * 6 + int(explicit_success) * 4
            score += int(resolved) * 2 - min(
                int(replacement["call_index"]) - int(failed["result_index"]), 5
            )
            candidates.append(
                (
                    score,
                    {
                        "proposal_only": True,
                        "failed_call_source_event_id": failed["call_event_id"],
                        "failure_result_source_event_id": failed["result_event_id"],
                        "replacement_call_source_event_id": replacement["call_event_id"],
                        "resolution_result_source_event_id": replacement["result_event_id"],
                        "explicit_failure_marker": bool(failure_score),
                        "stagnant_observation_marker": stagnant,
                        "explicit_success_marker": explicit_success,
                        "heuristic_score": score,
                    },
                )
            )
            break
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def _blank_failure_chain() -> dict[str, Any]:
    return {
        "failure_family": "",
        "failed_action": "",
        "failed_arguments": {},
        "error_signature": "",
        "diagnostic_evidence": "",
        "switch_decision": "",
        "replacement_action": "",
        "replacement_arguments": {},
        "resolution_evidence": "",
        "ordered_source_event_ids": [],
        "evidence_source_event_ids_by_field": {},
        "recoverability": "",
    }


def _candidate_row(
    *,
    source: str,
    repository: str,
    task_id: str,
    revision: str,
    source_file_sha256: str,
    raw_row: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    messages: Sequence[Mapping[str, Any]],
    hints: Mapping[str, Any],
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_id = stable_digest(
        {
            "source": source,
            "task_id": task_id,
            "revision": revision,
            "failed_event": hints["failed_call_source_event_id"],
            "replacement_event": hints["replacement_call_source_event_id"],
        }
    )
    final_event = events[-1] if events else {}
    return {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "annotation_status": "candidate",
        "source": source,
        "candidate_id": candidate_id,
        "repository": repository,
        "task_id": task_id,
        "split": "",
        "trajectory_revision": revision,
        "source_file_sha256": source_file_sha256,
        "source_record_sha256": stable_digest(raw_row),
        "prefix": {
            "events": [dict(item) for item in events],
            "messages": [dict(item) for item in messages],
            "tool_schemas": [
                dict(item)
                for item in _list(raw_row.get("tools") or raw_row.get("tool_schemas"))
                if isinstance(item, dict)
            ],
            "task_domain": str(metadata.get("domain") or "software"),
            "context_length": "natural",
            "budget_tokens": 4096,
            "current_fact": _text(final_event.get("content")),
            "current_source_event_ids": (
                [str(final_event["source_event_id"])] if final_event else []
            ),
            "environment_snapshot": dict(metadata),
        },
        "failure_chain": _blank_failure_chain(),
        "candidate_hints": dict(hints),
        "replay": {
            "docker_replayable": False,
            "snapshot_ref": "",
            "verification_command": "",
        },
        "annotator": "",
        "adjudicator": "",
    }


def mine_swe_gym_candidates(
    rows: Iterable[Mapping[str, Any]],
    *,
    revision: str,
    source_file_sha256: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    candidates = []
    for row in rows:
        events, messages = normalize_swe_gym_events(row)
        hints = propose_failure_window(events, resolved=bool(row.get("resolved")))
        if hints is None:
            continue
        task_id = str(row.get("instance_id") or stable_digest(row)[:16])
        repository = task_id.rsplit("-", 1)[0] if "-" in task_id else task_id
        candidates.append(
            _candidate_row(
                source="swe_gym",
                repository=repository,
                task_id=task_id,
                revision=revision,
                source_file_sha256=source_file_sha256,
                raw_row=row,
                events=events,
                messages=messages,
                hints=hints,
                metadata={
                    "domain": "software",
                    "run_id": row.get("run_id"),
                    "resolved": bool(row.get("resolved")),
                    "test_result": row.get("test_result"),
                },
            )
        )
    candidates.sort(key=lambda item: str(item["candidate_id"]))
    return candidates[:limit] if limit is not None else candidates


def mine_ama_bench_candidates(
    rows: Iterable[Mapping[str, Any]],
    *,
    revision: str,
    source_file_sha256: str,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    candidates = []
    for row in rows:
        events, messages = normalize_ama_events(row)
        hints = propose_failure_window(
            events,
            resolved=bool(row.get("success")),
            allow_stagnation=True,
        )
        if hints is None:
            continue
        task_id = str(
            row["episode_id"] if row.get("episode_id") is not None else stable_digest(row)[:16]
        )
        task_type = str(row.get("task_type") or "unknown")
        candidates.append(
            _candidate_row(
                source="ama_bench",
                repository=f"ama:{task_type}",
                task_id=task_id,
                revision=revision,
                source_file_sha256=source_file_sha256,
                raw_row=row,
                events=events,
                messages=messages,
                hints=hints,
                metadata={
                    "domain": row.get("domain"),
                    "task_type": task_type,
                    "success": bool(row.get("success")),
                    "qa_pairs": _list(row.get("qa_pairs")),
                },
            )
        )
    candidates.sort(key=lambda item: str(item["candidate_id"]))
    return candidates[:limit] if limit is not None else candidates


def write_candidate_bundle(
    *,
    swe_source: Path,
    ama_source: Path,
    swe_revision: str,
    ama_revision: str,
    output_root: Path,
    swe_limit: int | None = None,
    ama_limit: int | None = None,
) -> dict[str, Any]:
    """Create an append-free annotation input bundle without creating any gold."""

    if output_root.exists():
        raise FileExistsError(f"candidate output already exists: {output_root}")
    swe_hash = file_sha256(swe_source)
    ama_hash = file_sha256(ama_source)
    swe = mine_swe_gym_candidates(
        load_source_rows(swe_source),
        revision=swe_revision,
        source_file_sha256=swe_hash,
        limit=swe_limit,
    )
    ama = mine_ama_bench_candidates(
        load_source_rows(ama_source),
        revision=ama_revision,
        source_file_sha256=ama_hash,
        limit=ama_limit,
    )
    output_root.mkdir(parents=True, exist_ok=False)
    with (output_root / "candidates.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for row in (*swe, *ama):
            handle.write(canonical_json(row) + "\n")
    for name in ("annotator_a.jsonl", "annotator_b.jsonl", "adjudicated.jsonl"):
        (output_root / name).write_text("", encoding="utf-8")
    source_manifest = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "benchmark_id": BENCHMARK_ID,
        "gold_created": False,
        "swe_gym": {
            "path": str(swe_source),
            "sha256": swe_hash,
            "revision": swe_revision,
            "candidate_count": len(swe),
        },
        "ama_bench": {
            "path": str(ama_source),
            "sha256": ama_hash,
            "revision": ama_revision,
            "candidate_count": len(ama),
        },
        "repository": git_provenance(Path.cwd()),
        "implementation": implementation_provenance(Path.cwd()),
    }
    (output_root / "source_manifest.json").write_text(
        json.dumps(source_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    artifacts = write_file_manifest(output_root)
    manifest = {**source_manifest, "artifacts": artifacts}
    (output_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest
