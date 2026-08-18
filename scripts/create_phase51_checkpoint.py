#!/usr/bin/env python3
"""Create the recoverable Phase 5.1 checkpoint after the F5-G1 No-Go."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


CHECKPOINT_ID = "p51_wp0_f5_g1_no_go"
OUTPUT_ROOT = Path("outputs/phase5_1/checkpoints") / CHECKPOINT_ID
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
}
PHASE5_EMBEDDED_HASHES = (
    (
        "outputs/phase5/e0_development_v1/development_prefix_manifest.json",
        "manifest_sha256",
    ),
    (
        "outputs/phase5/e0_development_v1/tool_schemas.json",
        "artifact_sha256",
    ),
    (
        "outputs/phase5/e0_development_v1/prune_replay_v2/summary.json",
        "summary_sha256",
    ),
    (
        "outputs/phase5/e0_development_v1/prune_replay_v2/f5_g1_gate.json",
        "gate_report_sha256",
    ),
    (
        "outputs/phase5/e0_development_v1/prune_replay_v2/run_manifest.json",
        "run_manifest_sha256",
    ),
)


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run(
    arguments: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    binary: bool = False,
) -> subprocess.CompletedProcess[Any]:
    result = subprocess.run(
        arguments,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=not binary,
        encoding=None if binary else "utf-8",
        errors=None if binary else "replace",
        check=False,
    )
    if result.returncode:
        error = (
            result.stderr.decode("utf-8", errors="replace")
            if binary
            else result.stderr
        )
        raise RuntimeError(
            f"command failed ({result.returncode}): {arguments!r}\n{error}"
        )
    return result


def _git(repo: Path, *arguments: str, binary: bool = False) -> Any:
    return _run(
        [
            "git",
            "-c",
            f"safe.directory={repo.as_posix()}",
            *arguments,
        ],
        cwd=repo,
        binary=binary,
    ).stdout


def _file_record(repo: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(repo).as_posix(),
        "size_bytes": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _tree_record(repo: Path, raw_root: str) -> dict[str, Any]:
    root = repo / raw_root
    files = [
        _file_record(repo, path)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    ]
    return {
        "path": raw_root,
        "file_count": len(files),
        "size_bytes": sum(item["size_bytes"] for item in files),
        "tree_sha256": _sha256_json(files),
        "files": files,
    }


def _embedded_check(repo: Path, raw_path: str, field: str) -> dict[str, Any]:
    path = repo / raw_path
    value = json.loads(path.read_text(encoding="utf-8"))
    declared = value.pop(field, None)
    recomputed = _sha256_json(value)
    return {
        "path": raw_path,
        "field": field,
        "declared": declared,
        "recomputed": recomputed,
        "valid": declared == recomputed,
    }


def _write_text(path: Path, value: str) -> None:
    path.write_text(value, encoding="utf-8", newline="\n")


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    output = repo / OUTPUT_ROOT
    if output.exists():
        raise FileExistsError(f"checkpoint is immutable: {output}")

    head = _git(repo, "rev-parse", "HEAD").strip()
    branch = _git(repo, "branch", "--show-current").strip()
    status = _git(
        repo,
        "status",
        "--short",
        "--branch",
        "--untracked-files=all",
    )
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

    protected = [
        _tree_record(repo, raw_root)
        for raw_root in (*PROTECTED_ROOT_HASHES, "outputs/phase5")
    ]
    for item in protected:
        expected = PROTECTED_ROOT_HASHES.get(item["path"])
        if expected is not None and item["tree_sha256"] != expected:
            raise RuntimeError(f"protected artifact drift: {item['path']}")
    embedded = [
        _embedded_check(repo, raw_path, field)
        for raw_path, field in PHASE5_EMBEDDED_HASHES
    ]
    if not all(item["valid"] for item in embedded):
        raise RuntimeError("Phase 5 embedded artifact hash audit failed")

    env = dict(os.environ)
    env["PYTHONPATH"] = "src"
    pytest = _run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=repo,
        env=env,
    )
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
        "schema_version": "phase51_checkpoint_v1",
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
        "phase5_embedded_hash_checks": embedded,
        "verification": {
            "pytest": pytest.stdout.strip().splitlines()[-1],
            "ruff": ruff.stdout.strip(),
            "git_diff_check": True,
        },
        "governance": {
            "phase5_f5_g1_decision": "no_go",
            "phase5_f5_g1_gate_report_sha256": (
                "1f43ef64a4b91b6d0322c8fd80fecfde85ddccbe966b55c4f2d2cc965f7225aa"
            ),
            "phase5_replay_v2_run_manifest_sha256": (
                "b2430eebe767ddfecc809f020a07d250142dc1454b1a8d4e67e80aa73e2dc081"
            ),
            "phase4_r2_e0_remain_no_go": True,
            "external_provider_generations": 0,
            "new_output_root": "outputs/phase5_1",
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
                "dirty_identity_sha256": manifest["repository"][
                    "dirty_identity_sha256"
                ],
                "phase5_tree_sha256": protected[-1]["tree_sha256"],
                "pytest": manifest["verification"]["pytest"],
                "external_provider_generations": 0,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
