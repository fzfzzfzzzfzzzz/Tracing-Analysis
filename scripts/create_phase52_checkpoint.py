#!/usr/bin/env python3
"""Freeze the completed Phase 5.1 state before Phase 5.2 implementation."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from create_phase51_checkpoint import (
    _embedded_check,
    _file_record,
    _git,
    _run,
    _sha256_json,
    _tree_record,
    _write_text,
)


CHECKPOINT_ID = "p52_wp0_phase51_complete"
OUTPUT_ROOT = Path("outputs/phase5_2/checkpoints") / CHECKPOINT_ID
PROTECTED_ROOT_HASHES = {
    "outputs/gdsc_r0_audit": (
        "15ac8851550f3b3a7f9e4ce6caaf826252bb5a10b679814daadfcb02bb381613"
    ),
    "outputs/gdsc_r2_1": (
        "12e443366e814eb3403601952dc88763a90bb24c482862402d9110da40d7f491"
    ),
    "outputs/phase4": (
        "85a75eb998b08591f426ff64ce328ccc867407042f93485e254b4bf685b93867"
    ),
    "outputs/phase5": (
        "be1871f159124856b78a364d6389fd3fc71355a3245a4076601933efca5cab83"
    ),
    "outputs/phase5_1": (
        "052981da5bcc836c0fcf417482bc5f90dbdaae5026f7599b734e8a2e9ccea27d"
    ),
}
EMBEDDED_HASHES = (
    (
        "outputs/phase5/e0_development_v1/development_prefix_manifest.json",
        "manifest_sha256",
    ),
    (
        "outputs/phase5/e0_development_v1/tool_schemas.json",
        "artifact_sha256",
    ),
    (
        "outputs/phase5/e0_development_v1/prune_replay_v2/f5_g1_gate.json",
        "gate_report_sha256",
    ),
    (
        "outputs/phase5/e0_development_v1/prune_replay_v2/run_manifest.json",
        "run_manifest_sha256",
    ),
    (
        "outputs/phase5_1/e0_evidence_ceiling_v1/summary.json",
        "summary_sha256",
    ),
    (
        "outputs/phase5_1/e0_evidence_ceiling_v1/p51_g0_gate.json",
        "gate_report_sha256",
    ),
    (
        "outputs/phase5_1/e0_evidence_ceiling_v1/run_manifest.json",
        "run_manifest_sha256",
    ),
)


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    output = repo / OUTPUT_ROOT
    if output.exists():
        raise FileExistsError(f"checkpoint is immutable: {output}")

    head = _git(repo, "rev-parse", "HEAD").strip()
    branch = _git(repo, "branch", "--show-current").strip()
    status = _git(repo, "status", "--short", "--branch", "--untracked-files=all")
    tracked_patch = _git(
        repo,
        "diff",
        "--binary",
        "--no-ext-diff",
        "HEAD",
        "--",
        binary=True,
    )
    untracked_raw = _git(
        repo,
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
        binary=True,
    )
    untracked_paths = sorted(
        (
            Path(item.decode("utf-8"))
            for item in untracked_raw.split(b"\0")
            if item
        ),
        key=lambda item: item.as_posix(),
    )
    untracked_records = [_file_record(repo, repo / path) for path in untracked_paths]

    protected = [_tree_record(repo, root) for root in PROTECTED_ROOT_HASHES]
    for item in protected:
        if item["tree_sha256"] != PROTECTED_ROOT_HASHES[item["path"]]:
            raise RuntimeError(f"protected artifact drift: {item['path']}")
    embedded = [
        _embedded_check(repo, raw_path, field)
        for raw_path, field in EMBEDDED_HASHES
    ]
    if not all(item["valid"] for item in embedded):
        raise RuntimeError("Phase 5/5.1 embedded artifact hash audit failed")

    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    pytest = _run([sys.executable, "-m", "pytest", "-q"], cwd=repo, env=env)
    ruff = _run(
        [str(repo / ".venv/Scripts/ruff.exe"), "check", "src", "scripts", "tests"],
        cwd=repo,
    )
    diff_check = _git(repo, "diff", "--check")

    output.mkdir(parents=True, exist_ok=False)
    patch_path = output / "tracked.patch"
    patch_path.write_bytes(tracked_patch)
    zip_path = output / "untracked_files.zip"
    with zipfile.ZipFile(zip_path, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for relative in untracked_paths:
            archive.write(repo / relative, arcname=relative.as_posix())
    _write_text(output / "pytest.txt", pytest.stdout + pytest.stderr)
    _write_text(output / "ruff.txt", ruff.stdout + ruff.stderr)
    _write_text(output / "diff_check.txt", diff_check)

    dirty_identity = {
        "base_revision": head,
        "tracked_patch_sha256": hashlib.sha256(tracked_patch).hexdigest(),
        "untracked_files": untracked_records,
    }
    manifest: dict[str, Any] = {
        "schema_version": "phase52_checkpoint_v1",
        "checkpoint_id": CHECKPOINT_ID,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "repository": {
            "base_revision": head,
            "branch": branch,
            "status": status.splitlines(),
            "dirty_identity_sha256": _sha256_json(dirty_identity),
            "tracked_patch": _file_record(repo, patch_path),
            "untracked_archive": {
                **_file_record(repo, zip_path),
                "file_count": len(untracked_records),
            },
            "untracked_files": untracked_records,
        },
        "protected_artifacts": protected,
        "embedded_hash_checks": embedded,
        "verification": {
            "pytest": pytest.stdout.strip().splitlines()[-1],
            "ruff": ruff.stdout.strip(),
            "git_diff_check": True,
        },
        "governance": {
            "phase4_r2_e0_remain_no_go": True,
            "phase5_f5_g1_remains_no_go": True,
            "phase51_p51_g0_decision": "stop_old_corpus_rule_path",
            "phase52_annotation_model": "zai/glm-4.7-flash",
            "phase52_provider_generations": 0,
            "new_output_root": "outputs/phase5_2",
            "old_outputs_overwrite_forbidden": True,
        },
        "recovery": {
            "instructions": [
                f"check out base revision {head}",
                "apply tracked.patch with git apply --binary",
                "extract untracked_files.zip at the repository root",
                "verify every restored file hash from this manifest",
            ]
        },
    }
    manifest["checkpoint_sha256"] = _sha256_json(manifest)
    _write_text(
        output / "manifest.json",
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )
    print(
        json.dumps(
            {
                "checkpoint": OUTPUT_ROOT.as_posix(),
                "checkpoint_sha256": manifest["checkpoint_sha256"],
                "dirty_identity_sha256": manifest["repository"]["dirty_identity_sha256"],
                "pytest": manifest["verification"]["pytest"],
                "phase52_provider_generations": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
