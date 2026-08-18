from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from tracegraph import ArchiveStore
from tracegraph.adapters import TauTraceImporter
from tracegraph.integrations.gdsc_manager import GDSCManager


class GDSCIntegrationTests(unittest.TestCase):
    def test_tau_prefix_compiles_to_stable_provider_bundle(self) -> None:
        messages = [
            {"role": "user", "content": "Find order 7"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call-1", "name": "get_order", "arguments": {"order_id": "7"}}
                ],
            },
            {
                "role": "tool",
                "id": "call-1",
                "content": {"order_id": "7", "status": "pending"},
            },
            {"role": "user", "content": "Cancel it"},
        ]
        schema = {
            "type": "function",
            "function": {
                "name": "cancel_order",
                "parameters": {
                    "type": "object",
                    "properties": {"order_id": {"type": "string"}},
                    "required": ["order_id"],
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            importer = TauTraceImporter(ArchiveStore(Path(directory) / "archive"))
            graph = importer.import_simulation(
                {"id": "gdsc-prefix", "task_id": "7", "messages": messages},
                task={"id": "7", "instruction": "cancel order 7"},
                policy="Obtain confirmation before cancellation.",
            )
            manager = GDSCManager(
                model="zai/glm-4.7-flash",
                hard_context_limit=200_000,
            )

            first = manager.compile(
                graph,
                messages=messages,
                system_rules=("fixed scaffold", "full policy"),
                tool_schemas=(schema,),
                budget=4096,
            )
            second = manager.compile(
                graph,
                messages=messages,
                system_rules=("fixed scaffold", "full policy"),
                tool_schemas=(schema,),
                budget=4096,
            )

            self.assertEqual(first.state.state_hash, second.state.state_hash)
            self.assertEqual(first.query.query_hash, second.query.query_hash)
            self.assertEqual(first.bundle.request_hash, second.bundle.request_hash)
            self.assertEqual(first.bundle.tools[0]["function"]["name"], "cancel_order")
            provenance = first.bundle.provenance_manifest
            for key in (
                "event_graph_sha256",
                "task_sha256",
                "tool_schema_sha256",
                "policy_sha256",
                "serializer",
                "tokenizer",
            ):
                self.assertTrue(provenance[key], key)
                self.assertEqual(provenance[key], second.bundle.provenance_manifest[key])
            self.assertLessEqual(first.bundle.serialized_token_cost, 4096)
            self.assertTrue(first.bundle.matched_budget_eligible)


if __name__ == "__main__":
    unittest.main()
