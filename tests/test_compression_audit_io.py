"""Offline I/O integrity checks; these tests never contact a provider."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from test_compression_audit import minimal_dataset
from tracegraph.benchmark.compression_audit.dataset import load_jsonl, write_file_manifest
from tracegraph.benchmark.compression_audit.metrics import score_run
from tracegraph.compression_audit_real import load_source_rows, normalize_swe_gym_events
from tracegraph.benchmark.compression_audit.runtime import run_deterministic


class RealParquetImportTests(unittest.TestCase):
    def test_jsonl_retains_embedded_unicode_line_separator(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.jsonl"
            source.write_text(
                '{"id":1,"text":"before\\u2028after"}\n'
                '{"id":2,"text":"ordinary"}\n',
                encoding="utf-8",
            )
            imported = load_source_rows(source)
        self.assertEqual(len(imported), 2)
        self.assertEqual(imported[0]["text"], "before\u2028after")

    @unittest.skipUnless(importlib.util.find_spec("pyarrow"), "optional Parquet dependency")
    def test_parquet_retains_nested_messages_and_function_calls(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        row = {
            "instance_id": "example__repository-1",
            "resolved": True,
            "messages": [
                {
                    "role": "assistant",
                    "content": "Try the original command.",
                    "tool_calls": [{
                        "id": "failed-call", "type": "function",
                        "function": {"name": "shell", "arguments": '{"command":"bad"}'},
                    }],
                },
                {"role": "tool", "tool_call_id": "failed-call", "content": "error: syntax"},
                {
                    "role": "assistant",
                    "content": "Switch commands because the original syntax was invalid.",
                    "tool_calls": [{
                        "id": "replacement-call", "type": "function",
                        "function": {"name": "shell", "arguments": '{"command":"good"}'},
                    }],
                },
                {"role": "tool", "tool_call_id": "replacement-call", "content": "exit code 0"},
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.parquet"
            pq.write_table(pa.Table.from_pylist([row]), source)
            imported = load_source_rows(source)
        self.assertEqual(len(imported), 1)
        self.assertIsInstance(imported[0]["messages"], list)
        events, messages = normalize_swe_gym_events(imported[0])
        self.assertEqual(len(messages), 4)
        calls = [event for event in events if event["kind"] == "tool_call"]
        results = [event for event in events if event["kind"] == "tool_result"]
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(results), 2)
        self.assertEqual(calls[0]["content"]["arguments"], {"command": "bad"})
        self.assertEqual({event["call_id"] for event in calls},
                         {event["call_id"] for event in results})
        # Provenance hashing must not stringify NumPy arrays or scalar wrappers.
        json.dumps(imported, ensure_ascii=False, allow_nan=False)


class ScoringInputIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.dataset = self.root / "dataset"
        minimal_dataset(self.dataset)
        self.run = self.root / "run"
        run_deterministic(self.dataset, self.run,
                          methods=("M0_full_history",), query_types=("audit_failed_action",))
        self.score = self.root / "score"

    def tearDown(self):
        self.directory.cleanup()

    def test_changed_episode_file_is_rejected_before_creating_score_output(self):
        episodes = self.run / "episodes.jsonl"
        episodes.write_text(episodes.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest"):
            score_run(self.dataset, self.run, self.score, bootstrap_samples=10)
        self.assertFalse(self.score.exists())

    def test_changed_non_record_dataset_artifact_is_rejected(self):
        (self.dataset / "unexpected.txt").write_text("unmanifested", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "manifest"):
            score_run(self.dataset, self.run, self.score, bootstrap_samples=10)
        self.assertFalse(self.score.exists())

    def test_deterministic_runner_also_rejects_unmanifested_data(self):
        (self.dataset / "unexpected.txt").write_text("unmanifested", encoding="utf-8")
        output = self.root / "another-run"
        with self.assertRaisesRegex(ValueError, "manifest"):
            run_deterministic(self.dataset, output, methods=("M0_full_history",))
        self.assertFalse(output.exists())

    def test_run_cannot_be_scored_against_another_data_revision(self):
        manifest = self.dataset / "manifest.json"
        value = json.loads(manifest.read_text(encoding="utf-8"))
        value["data_revision"] = "different-revision"
        manifest.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "dataset.*frozen|dataset.*run"):
            score_run(self.dataset, self.run, self.score, bootstrap_samples=10)
        self.assertFalse(self.score.exists())

    def test_standalone_episode_import_is_explicitly_diagnostic(self):
        rows = load_jsonl(self.run / "episodes.jsonl")
        source = self.root / "imported.jsonl"
        source.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        result = score_run(self.dataset, source, self.score, bootstrap_samples=10)
        self.assertEqual(result["report"]["ranked_episode_count"], 0)
        self.assertFalse(result["manifest"]["run_manifest_verified"])

    def test_valid_manifested_run_remains_scorable(self):
        write_file_manifest(self.dataset)
        result = score_run(self.dataset, self.run, self.score, bootstrap_samples=10)
        self.assertTrue(result["manifest"]["run_manifest_verified"])
        self.assertTrue(result["manifest"]["dataset_manifest_verified"])

    def test_score_output_cannot_mutate_an_immutable_input_directory(self):
        for output in (self.dataset / "score", self.run / "score"):
            with self.assertRaisesRegex(ValueError, "outside immutable"):
                score_run(self.dataset, self.run, output, bootstrap_samples=10)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
