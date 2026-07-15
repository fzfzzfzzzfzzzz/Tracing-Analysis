"""Optional τ³ user simulator with explicit stop-protocol normalization."""

from __future__ import annotations

from tau2.user.user_simulator import UserSimulator

from tracegraph.user_protocol import normalize_user_stop


class ProtocolNormalizingUserSimulator(UserSimulator):
    """Convert explicit natural-language closure into τ³'s literal marker.

    The base simulator still creates every user message. This adapter only
    normalizes a message after the model has explicitly said that no further
    help is needed; it never reads task rewards or environment state.
    """

    def generate_next_message(self, message, state):
        user_message, state = super().generate_next_message(message, state)
        normalized = normalize_user_stop(user_message.content)
        if normalized == user_message.content:
            return user_message, state
        normalized_message = user_message.model_copy(update={"content": normalized})
        state.messages[-1] = normalized_message
        return normalized_message, state


def register_tau3_user(name: str = "tracegraph_user_simulator") -> None:
    from tau2.registry import registry

    registry.register_user(ProtocolNormalizingUserSimulator, name)
