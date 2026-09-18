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
        """Generate one valid user turn and normalize explicit closure.

        Some OpenAI-compatible local endpoints occasionally return an immediate
        EOS (one completion token and no content) for the deterministic user
        simulator.  Tau3 otherwise appends that empty message and classifies the
        run as an infrastructure error.  Retry only that invalid response once,
        with a recorded seed/temperature perturbation, and charge both attempts
        to the returned message.  Normal responses keep the upstream path and
        parameters unchanged.
        """

        initial_message_count = len(state.messages)
        original_seed = self.llm_args.get("seed")
        original_temperature = self.llm_args.get("temperature")
        attempts = []
        try:
            for attempt_index in range(2):
                del state.messages[initial_message_count:]
                if attempt_index:
                    if isinstance(original_seed, int):
                        self.llm_args["seed"] = original_seed + attempt_index
                    self.llm_args["temperature"] = max(
                        0.2,
                        float(original_temperature or 0.0),
                    )
                user_message = super()._generate_next_message(message, state)
                attempts.append(user_message)
                if not (user_message.has_content() or user_message.is_tool_call()):
                    continue

                normalized = normalize_user_stop(user_message.content)
                total_cost = sum(float(item.cost or 0.0) for item in attempts)
                total_usage = {
                    key: sum(int((item.usage or {}).get(key, 0)) for item in attempts)
                    for key in ("prompt_tokens", "completion_tokens")
                }
                raw_data = dict(user_message.raw_data or {})
                raw_data["tracegraph_user_simulator_retry"] = {
                    "attempt_count": len(attempts),
                    "empty_response_retries": len(attempts) - 1,
                    "retry_cost_included": True,
                    "retry_usage_included": True,
                }
                normalized_message = user_message.model_copy(
                    update={
                        "content": normalized,
                        "cost": total_cost,
                        "usage": total_usage,
                        "raw_data": raw_data,
                    }
                )
                state.messages.append(normalized_message)
                return normalized_message, state
        finally:
            if original_seed is None:
                self.llm_args.pop("seed", None)
            else:
                self.llm_args["seed"] = original_seed
            if original_temperature is None:
                self.llm_args.pop("temperature", None)
            else:
                self.llm_args["temperature"] = original_temperature

        del state.messages[initial_message_count:]
        raise ValueError("user simulator returned an empty response after 2 attempts")


def register_tau3_user(name: str = "tracegraph_user_simulator") -> None:
    from tau2.registry import registry

    registry.register_user(ProtocolNormalizingUserSimulator, name)
