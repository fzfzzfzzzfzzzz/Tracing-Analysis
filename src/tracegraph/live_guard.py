"""Fail-closed authorization checks shared by every paid/live runner."""

from __future__ import annotations

import hmac
import os
from collections.abc import Mapping
from typing import Any


def require_live_authorization_id(
    authorization: Mapping[str, Any],
    provided_authorization_id: str | None,
) -> str:
    """Require a fresh, explicit CLI acknowledgement for a live run.

    A historical config containing ``authorized_by_user=true`` is deliberately
    insufficient. New live work must use a new config with an opaque
    ``authorization_id`` and pass the same value at invocation time.
    """

    if os.environ.get("TRACEGRAPH_DISABLE_LIVE", "").strip() == "1":
        raise RuntimeError("live provider access is disabled in this process")
    expected = str(authorization.get("authorization_id", "")).strip()
    provided = str(provided_authorization_id or "").strip()
    if not expected:
        raise RuntimeError("the config has no fresh live authorization_id")
    if not provided:
        raise RuntimeError("--live-authorization-id is required for live provider access")
    if not hmac.compare_digest(expected, provided):
        raise RuntimeError("the live authorization ID does not match the config")
    return expected
