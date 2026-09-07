"""Definitions moved from ``tracegraph.compression_audit``."""

from __future__ import annotations

# ruff: noqa: E402, F401, F811

import hashlib
import json
import math
import shutil
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from ...capture import TOKEN_ACCOUNTING_VERSION, estimate_tokens

from .constants import (
    IMPLEMENTATION_PATHS as IMPLEMENTATION_PATHS,
)



def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def stable_digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _nonempty(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _tuple_strings(value: Iterable[Any]) -> tuple[str, ...]:
    return tuple(str(item) for item in value)


def _assert_output_available(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"benchmark output already exists: {path}")


def git_provenance(workspace: Path) -> dict[str, Any]:
    """Return reproducibility metadata without mutating the repository."""

    root = workspace.resolve()
    marker = next((path / ".git" for path in (root, *root.parents) if (path / ".git").exists()), None)
    if marker is None:
        return {"commit": "unavailable", "branch": "unavailable", "tracked_worktree_dirty": None}
    if marker.is_file():
        pointer = marker.read_text(encoding="utf-8").strip()
        if not pointer.startswith("gitdir:"):
            return {"commit": "unavailable", "branch": "unavailable", "tracked_worktree_dirty": None}
        git_dir = (marker.parent / pointer.split(":", 1)[1].strip()).resolve()
    else:
        git_dir = marker.resolve()
    head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
    branch = "HEAD"
    commit = head
    if head.startswith("ref:"):
        ref_name = head.split(":", 1)[1].strip()
        branch = ref_name.removeprefix("refs/heads/")
        ref_path = git_dir / Path(ref_name)
        if ref_path.is_file():
            commit = ref_path.read_text(encoding="utf-8").strip()
        else:
            commit = "unavailable"
            packed = git_dir / "packed-refs"
            if packed.is_file():
                for line in packed.read_text(encoding="utf-8").splitlines():
                    if line and not line.startswith(("#", "^")):
                        value, name = line.split(" ", 1)
                        if name == ref_name:
                            commit = value
                            break
    return {
        "commit": commit,
        "branch": branch,
        # A full status walk is intentionally omitted: this workspace can contain large
        # ignored experiment trees. Artifact hashes capture the exact benchmark code/data.
        "tracked_worktree_dirty": None,
    }


def implementation_provenance(workspace: Path) -> dict[str, Any]:
    """Hash the exact uncommitted implementation used for a build or run."""

    start = workspace.resolve()
    root = next(
        (path for path in (start, *start.parents) if (path / "pyproject.toml").is_file()),
        start,
    )
    hashes = {
        relative: file_sha256(root / relative)
        for relative in IMPLEMENTATION_PATHS
        if (root / relative).is_file()
    }
    return {"root": str(root), "source_hashes": hashes, "digest": stable_digest(hashes)}


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: Iterable[Mapping[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(canonical_json(dict(row)) + "\n")
            count += 1
    return count


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        raise FileNotFoundError(path)
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        value = json.loads(line)
        if not isinstance(value, dict):
            raise ValueError(f"{path}:{line_number} must contain a JSON object")
        rows.append(value)
    return rows
