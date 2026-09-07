"""Definitions moved from ``tracegraph.compression_audit_live``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Sequence
from ...capture import estimate_tokens
from ...compression_audit import BENCHMARK_ID, EpisodeRecord, FailureChainGold, PrefixRecord, QueryRecord, canonical_json, file_sha256, git_provenance, implementation_provenance, load_config, load_jsonl, stable_digest, verify_file_manifest, write_file_manifest
from ...compression_audit_runtime import answer_tool_schema, load_dataset, parse_submit_answer, prepare_v0_trials, reacquisition_tool_schema
from ...compression_audit_tokenization import VerifiedContextTokenizer, retokenize_trials
from ...live_guard import require_live_authorization_id
from .development_protocol import DEVELOPMENT_ONLY_NOTICE





def reconcile_live_recordings(source_root: Path, output_root: Path) -> dict[str, Any]:
    """Rebuild embedded request snapshots from verified durable logs, without inference.

    This only repairs the historical shared-list recording defect. It refuses any
    mismatch in responses, hashes, usage, or other call metadata and never edits a
    source artifact or changes an answer. Both pre-send and post-send logs must agree.
    """

    if output_root.exists():
        raise FileExistsError(f"reconciliation output already exists: {output_root}")
    if output_root.resolve().is_relative_to(source_root.resolve()):
        raise ValueError("reconciliation output must be outside the immutable source run")
    verify_file_manifest(source_root)
    source_manifest = json.loads((source_root / "manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((source_root / "run_summary.json").read_text(encoding="utf-8"))
    if summary.get("status") != "complete" or summary.get("usage_complete") is not True:
        raise ValueError("only a completed run with certain usage can be reconciled")
    attempts = load_jsonl(source_root / "provider_attempts.jsonl")
    ledger = load_jsonl(source_root / "provider_ledger.jsonl")
    episodes = load_jsonl(source_root / "episodes.jsonl")
    if len(attempts) != len(ledger) or len(ledger) != summary.get("provider_requests"):
        raise ValueError("request ledger coverage mismatch")
    indexed: dict[tuple[str, int], dict[str, Any]] = {}
    for attempt, call in zip(attempts, ledger, strict=True):
        key = (str(call["trial_id"]), int(call["turn_index"]))
        if key in indexed:
            raise ValueError("duplicate request ledger entry")
        if (
            key != (str(attempt["trial_id"]), int(attempt["turn_index"]))
            or attempt["request_sha256"] != stable_digest(attempt["request"])
            or call["request_sha256"] != stable_digest(call["request"])
            or call["request_sha256"] != attempt["request_sha256"]
            or call["response_sha256"] != stable_digest(call["response"])
            or call.get("valid_usage") is not True
        ):
            raise ValueError("durable request/response ledger integrity mismatch")
        indexed[key] = call
    repaired_episodes = []
    used_keys: set[tuple[str, int]] = set()
    changed_calls = 0
    changed_episode_ids = []
    for episode in episodes:
        repaired = json.loads(canonical_json(episode))
        repaired_calls = []
        episode_changed = False
        for embedded in episode.get("model_calls", ()):
            key = (str(embedded["trial_id"]), int(embedded["turn_index"]))
            if key in used_keys or key not in indexed or key[0] != episode["episode_id"]:
                raise ValueError("episode/ledger request ownership mismatch")
            durable = indexed[key]
            if stable_digest({k: v for k, v in embedded.items() if k != "request"}) != stable_digest(
                {k: v for k, v in durable.items() if k != "request"}
            ):
                raise ValueError("only embedded request-body aliasing may be reconciled")
            if stable_digest(embedded["request"]) != durable["request_sha256"]:
                changed_calls += 1
                episode_changed = True
            repaired_calls.append(json.loads(canonical_json(durable)))
            used_keys.add(key)
        if (
            not repaired_calls
            or episode.get("request_hash") != stable_digest(
                [call["request_sha256"] for call in repaired_calls]
            )
            or episode.get("response_hash") != stable_digest(
                [call["response_sha256"] for call in repaired_calls]
            )
            or episode.get("provider_input_tokens") != sum(
                call["prompt_tokens"] for call in repaired_calls
            )
            or episode.get("provider_output_tokens") != sum(
                call["completion_tokens"] for call in repaired_calls
            )
        ):
            raise ValueError("episode hashes or usage do not match durable calls")
        repaired["model_calls"] = repaired_calls
        repaired_episodes.append(repaired)
        if episode_changed:
            changed_episode_ids.append(str(episode["episode_id"]))
    if used_keys != set(indexed) or len(episodes) != summary.get("completed_episode_count"):
        raise ValueError("incomplete episode/ledger coverage")
    reconciliation = {
        "schema_version": "compression_audit_recording_reconciliation_v1",
        "protocol": "v0.1-diagnostic",
        "development_only": True,
        "independent_validation": False,
        "interpretation": DEVELOPMENT_ONLY_NOTICE,
        "source_run": str(source_root.resolve()),
        "source_manifest_sha256": file_sha256(source_root / "manifest.json"),
        "source_episodes_sha256": file_sha256(source_root / "episodes.jsonl"),
        "source_ledger_sha256": file_sha256(source_root / "provider_ledger.jsonl"),
        "source_attempts_sha256": file_sha256(source_root / "provider_attempts.jsonl"),
        "changed_embedded_request_count": changed_calls,
        "changed_episode_ids": changed_episode_ids,
        "answers_and_usage_changed": False,
        "provider_requests_made_during_reconciliation": 0,
        "recording_implementation": source_manifest.get("implementation"),
        "reconciliation_implementation": implementation_provenance(Path.cwd()),
    }
    output_root.mkdir(parents=True, exist_ok=False)
    for name in (
        "config.snapshot.json", "trials.jsonl", "preflight.json", "provider_attempts.jsonl",
        "provider_ledger.jsonl", "run_summary.json",
    ):
        shutil.copyfile(source_root / name, output_root / name)
    with (output_root / "episodes.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for episode in repaired_episodes:
            handle.write(canonical_json(episode) + "\n")
    _write_json(output_root / "reconciliation.json", reconciliation)
    artifacts = write_file_manifest(output_root)
    manifest = {
        **source_manifest,
        "mode": "recording_reconciliation_no_inference",
        "reconciliation": reconciliation,
        "artifacts": artifacts,
    }
    _write_json(output_root / "manifest.json", manifest)
    return manifest


def run_live_v0(
    config_path: Path,
    dataset_root: Path,
    output_root: Path,
    *,
    workspace: Path | None = None,
    max_new_requests: int | None = None,
    resume: bool = False,
    authorization_id: str | None = None,
) -> dict[str, Any]:
    """Serialize writers so concurrent resumes cannot duplicate paid attempts."""

    destination = output_root.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    lock_path = destination.parent / f".{destination.name}.live.lock"
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise RuntimeError("a live writer lock exists; concurrent or interrupted runs require inspection") from error
    os.close(descriptor)
    try:
        return _run_live_v0_locked(
            config_path, dataset_root, destination, workspace=workspace,
            max_new_requests=max_new_requests, resume=resume,
            authorization_id=authorization_id,
        )
    finally:
        lock_path.unlink()


# Imported after definitions so mutually-referential helpers initialize safely.
from .live_authorization import (
    _write_json as _write_json,
)

from .live_runner import (
    _run_live_v0_locked as _run_live_v0_locked,
)
