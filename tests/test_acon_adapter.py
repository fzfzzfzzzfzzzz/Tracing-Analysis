import hashlib
import tempfile
import unittest
from pathlib import Path

from tracegraph.integrations.acon import (
    AconAdapterError,
    AconRuntimeAdapter,
    canonical_message_json,
    verify_acon_source,
)


class FakeLLM:
    def __init__(self):
        self.records = []

    def drain_records(self):
        records = self.records
        self.records = []
        return records


class FakeObservationOptimizer:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.calls = []
        self.llm = FakeLLM()

    def check_summarization_needed(self, observation):
        return len(observation) > 30

    def process(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise TimeoutError("simulated compressor timeout")
        self.llm.records.append(
            {
                "model": "fake-compressor",
                "input_tokens": 17,
                "output_tokens": 3,
                "cost_usd": 0.002,
                "latency_seconds": 0.25,
                "provider_usage_present": True,
            }
        )
        return "refined observation"


class FakeHistoryOptimizer:
    def __init__(self):
        self.calls = []
        self.llm = FakeLLM()

    def check_summarization_needed(self, history_text, prev_history_summary=None):
        return bool(history_text)

    def process(self, **kwargs):
        self.calls.append(kwargs)
        self.llm.records.append(
            {
                "model": "fake-compressor",
                "input_tokens": 23,
                "output_tokens": 4,
                "cost_usd": 0.003,
                "latency_seconds": 0.5,
                "provider_usage_present": True,
            }
        )
        return "first turn summary"


class NoUsageObservationOptimizer(FakeObservationOptimizer):
    def process(self, **kwargs):
        self.calls.append(kwargs)
        return "refined without provider usage"


def messages_after_first_tool():
    return [
        {"role": "user", "content": "Please update my order."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {"id": "call-1", "name": "lookup", "arguments": {"order_id": "A1"}}
            ],
        },
        {
            "role": "tool",
            "id": "call-1",
            "requestor": "assistant",
            "error": False,
            "content": "A long observation that must be compressed.",
        },
    ]


class AconAdapterTests(unittest.TestCase):
    def make_adapter(self, *, observation=None, history=None, fallback="error"):
        return AconRuntimeAdapter(
            observation_optimizer=observation,
            history_optimizer=history,
            provenance={
                "source_manifest_verified": True,
                "source_snapshot_sha": "a" * 40,
            },
            preserve_last_k_turns=1,
            fallback=fallback,
        )

    def test_canonical_serialization_excludes_volatile_telemetry(self):
        left = {
            "role": "tool",
            "content": "ok",
            "id": "c1",
            "timestamp": "first",
            "cost": 12.5,
        }
        right = dict(left, timestamp="second", cost=99.0)
        self.assertEqual(canonical_message_json(left), canonical_message_json(right))
        self.assertIn('"id":"c1"', canonical_message_json(left))

    def test_calls_official_observation_signature_and_preserves_tool_identity(self):
        observation = FakeObservationOptimizer()
        adapter = self.make_adapter(observation=observation, history=None)
        messages = messages_after_first_tool()

        plan = adapter.prepare(messages, new_indices=[2])

        self.assertEqual(plan.content_overrides[2], "refined observation")
        self.assertEqual(plan.included_indices, (0, 1, 2))
        self.assertEqual(len(observation.calls), 1)
        call = observation.calls[0]
        self.assertEqual(call["task"], "Please update my order.")
        self.assertEqual(call["observation"], messages[2]["content"])
        self.assertEqual(call["opt_args"], {})
        self.assertEqual(call["raw_history"][0]["role"], "user")
        self.assertEqual(call["raw_history"][1]["role"], "assistant")
        self.assertIn('"name":"lookup"', call["history"])
        metadata = plan.metadata()
        self.assertEqual(metadata["compressor_provider_input_tokens"], 17)
        self.assertAlmostEqual(metadata["compressor_cost_usd"], 0.002)
        self.assertTrue(plan.runtime_main_result_eligible)

    def test_history_summary_replaces_only_old_turns(self):
        observation = FakeObservationOptimizer()
        history = FakeHistoryOptimizer()
        adapter = self.make_adapter(observation=observation, history=history)
        messages = messages_after_first_tool()
        adapter.prepare(messages, new_indices=[2])
        messages.extend(
            [
                {"role": "assistant", "content": "What address should I use?"},
                {"role": "user", "content": "Use the saved address."},
            ]
        )

        plan = adapter.prepare(messages, new_indices=[4])

        self.assertEqual(plan.included_indices, (0, 3, 4))
        self.assertEqual(plan.summarized_until, 2)
        self.assertEqual(plan.history_summary, "first turn summary")
        self.assertIn("Please update my order.", plan.content_overrides[0])
        self.assertIn("<HISTORY_SUMMARY>", plan.content_overrides[0])
        self.assertEqual(len(history.calls), 1)
        call = history.calls[0]
        self.assertIsNone(call["prev_history_summary"])
        self.assertEqual(call["opt_args"], {})
        self.assertIn("refined observation", call["history"])
        self.assertEqual([item["role"] for item in call["raw_history"]], ["assistant", "user"])
        metadata = plan.metadata()
        self.assertEqual(metadata["compressor_provider_input_tokens"], 23)
        self.assertEqual(metadata["compressor_provider_output_tokens"], 4)

    def test_strict_fallback_raises_without_exposing_exception_text(self):
        adapter = self.make_adapter(
            observation=FakeObservationOptimizer(fail=True),
            history=None,
        )
        with self.assertRaisesRegex(AconAdapterError, "official ACON observation optimizer failed"):
            adapter.prepare(messages_after_first_tool(), new_indices=[2])

    def test_explicit_raw_fallback_invalidates_main_result(self):
        adapter = self.make_adapter(
            observation=FakeObservationOptimizer(fail=True),
            history=None,
            fallback="raw",
        )
        plan = adapter.prepare(messages_after_first_tool(), new_indices=[2])

        self.assertNotIn(2, plan.content_overrides)
        self.assertFalse(plan.runtime_main_result_eligible)
        self.assertEqual(plan.metadata()["fallback_count"], 1)
        self.assertEqual(plan.call_records[0].error_type, "TimeoutError")

    def test_missing_provider_usage_invalidates_main_result(self):
        observation = NoUsageObservationOptimizer()
        adapter = self.make_adapter(observation=observation, history=None)
        plan = adapter.prepare(messages_after_first_tool(), new_indices=[2])

        self.assertFalse(plan.accounting_complete)
        self.assertFalse(plan.runtime_main_result_eligible)

    def test_source_manifest_verification_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.py"
            source.write_text("official source\n", encoding="utf-8")
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            provenance = verify_acon_source(
                root,
                {"source.py": digest},
                snapshot_sha="b" * 40,
                source_repo="https://example.invalid/official",
            )
            self.assertTrue(provenance["source_manifest_verified"])
            with self.assertRaisesRegex(AconAdapterError, "hash mismatch"):
                verify_acon_source(
                    root,
                    {"source.py": "0" * 64},
                    snapshot_sha="b" * 40,
                    source_repo="https://example.invalid/official",
                )


if __name__ == "__main__":
    unittest.main()
