"""Deterministic normalization for explicit user conversation closure.

Some OpenAI-compatible models express a clear natural-language stop intent but
do not emit the literal marker required by the τ³ user-simulator protocol. This
module maps only strong, first-person declarative closure phrases to that marker.
It does not infer task success or inspect environment/tool state.
"""

from __future__ import annotations

import re

STOP_MARKER = "###STOP###"

_EXPLICIT_CLOSE_PATTERNS = (
    re.compile(
        r"\b(?:i\s+)?(?:do\s+not|don't)\s+need\s+"
        r"(?:anything\s+else|anything\s+more|more\s+help|further\s+assistance)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bthat(?:'s|\s+is)\s+all(?:\s+for\s+(?:now|today))?\b", re.IGNORECASE),
    re.compile(r"\bi(?:'m|\s+am)\s+all\s+set\b", re.IGNORECASE),
    re.compile(
        r"\bnothing\s+else\s+(?:is\s+needed|for\s+(?:now|today)|at\s+the\s+moment)\b",
        re.IGNORECASE,
    ),
)


def has_explicit_stop_intent(content: str | None) -> bool:
    """Return true only for literal marker or declarative close intent.

    Questions are deliberately excluded so phrases such as "Do I need anything
    else?" cannot terminate a benchmark session.
    """

    if not content:
        return False
    if STOP_MARKER in content:
        return True
    sentences = re.split(r"(?<=[.!?])\s+|[\r\n]+", content)
    for sentence in sentences:
        if "?" in sentence:
            continue
        if any(pattern.search(sentence) for pattern in _EXPLICIT_CLOSE_PATTERNS):
            return True
    return False


def normalize_user_stop(content: str | None) -> str | None:
    """Append the τ³ marker when explicit natural-language stop intent exists."""

    if not content or STOP_MARKER in content or not has_explicit_stop_intent(content):
        return content
    return f"{content.rstrip()}\n\n{STOP_MARKER}"
