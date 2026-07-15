"""Content-addressed, recoverable storage for raw trace payloads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .schema import utc_now


class ArchiveIntegrityError(RuntimeError):
    """Raised when an archive object does not match its content hash."""


class ArchiveStore:
    """A small content-addressed JSON archive.

    Archive writes are atomic within one filesystem and deduplicated by SHA-256.
    The returned URI is a stable ``sha256:<digest>`` recoverable handle.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _canonical_payload(payload: Any) -> bytes:
        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")

    def put(self, payload: Any, *, metadata: dict[str, Any] | None = None) -> str:
        raw = self._canonical_payload(payload)
        digest = hashlib.sha256(raw).hexdigest()
        target = self.objects / digest[:2] / f"{digest}.json"
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            envelope = {
                "algorithm": "sha256",
                "digest": digest,
                "created_at": utc_now(),
                "metadata": metadata or {},
                "payload": payload,
            }
            temporary = target.with_suffix(".tmp")
            temporary.write_text(
                json.dumps(envelope, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            temporary.replace(target)
        return f"sha256:{digest}"

    def _path_for(self, reference: str) -> Path:
        algorithm, separator, digest = reference.partition(":")
        if separator != ":" or algorithm != "sha256" or len(digest) != 64:
            raise ValueError(f"invalid archive reference: {reference!r}")
        return self.objects / digest[:2] / f"{digest}.json"

    def get(self, reference: str, *, verify: bool = True) -> Any:
        path = self._path_for(reference)
        envelope = json.loads(path.read_text(encoding="utf-8"))
        payload = envelope["payload"]
        if verify:
            actual = hashlib.sha256(self._canonical_payload(payload)).hexdigest()
            expected = reference.partition(":")[2]
            if actual != expected or envelope.get("digest") != expected:
                raise ArchiveIntegrityError(f"archive integrity check failed for {reference}")
        return payload

    def exists(self, reference: str) -> bool:
        try:
            return self._path_for(reference).is_file()
        except ValueError:
            return False

    def verify_all(self) -> list[str]:
        failures: list[str] = []
        for path in self.objects.glob("*/*.json"):
            reference = f"sha256:{path.stem}"
            try:
                self.get(reference, verify=True)
            except (OSError, KeyError, ValueError, json.JSONDecodeError, ArchiveIntegrityError):
                failures.append(reference)
        return failures

