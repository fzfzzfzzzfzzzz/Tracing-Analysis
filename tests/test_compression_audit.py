import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from tracegraph.cli import build_parser
from tracegraph.compression_audit import (
    FailureChainGold,
    PrefixRecord,
    QueryRecord,
    file_sha256,
    generate_controlled_dataset,
    import_adjudicated_real,
    stable_digest,
    validate_real_annotations,
    write_file_manifest,
)
from tracegraph.compression_audit_live import (
    theoretical_maximum_cost_cny,
    validate_live_authorization,
)
from tracegraph.compression_audit_metrics import score_episode, score_run
from tracegraph.compression_audit_real import (
    mine_ama_bench_candidates,
    mine_swe_gym_candidates,
)
from tracegraph.compression_audit_runtime import (
    AconCompressionAdapter,
    ReferenceMemoryAdapter,
    counterfactual_bundle,
    deterministic_answer,
    memory_artifact,
    prepare_v0_trials,
    run_deterministic,
)


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def minimal_dataset(root):
    prefixes, gold, queries = generate_controlled_dataset()
    write_jsonl(root / "public" / "prefixes.jsonl", [item.to_dict() for item in prefixes[:1]])
    write_jsonl(
        root / "public" / "queries.jsonl",
        [item.to_dict() for item in queries if item.prefix_id == prefixes[0].prefix_id],
    )
    write_jsonl(root / "private" / "all_gold.jsonl", [gold[0].to_dict()])
    legacy_prefixes = prefixes[:24]
    legacy_ids = {item.prefix_id for item in legacy_prefixes}
    write_jsonl(
        root / "legacy_diagnostic" / "audit_prefixes.jsonl",
        [item.to_dict() for item in legacy_prefixes],
    )
    write_jsonl(
        root / "legacy_diagnostic" / "audit_queries.jsonl",
        [item.to_dict() for item in queries if item.prefix_id in legacy_ids],
    )
    write_jsonl(
        root / "legacy_diagnostic" / "audit_gold.jsonl",
        [item.to_dict() for item in gold if item.prefix_id in legacy_ids],
    )
    (root / "manifest.json").write_text(
        json.dumps({"benchmark_id": "compression_audit_v1"}) + "\n",
        encoding="utf-8",
    )
    write_file_manifest(root)
    return prefixes, gold, queries


class CompressionAuditDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prefixes, cls.gold, cls.queries = generate_controlled_dataset()

    def test_controlled_cartesian_product_and_family_holdout(self):
        self.assertEqual(len(self.prefixes), 240)
        self.assertEqual(len(self.gold), 240)
        self.assertEqual(len(self.queries), 1440)
        self.assertEqual(
            {split: sum(item.split == split for item in self.prefixes) for split in ("dev", "validation", "test")},
            {"dev": 48, "validation": 48, "test": 144},
        )
        families = {
            split: {item.failure_family for item in self.prefixes if item.split == split}
            for split in ("dev", "validation", "test")
        }
        self.assertFalse(families["dev"] & families["validation"])
        self.assertFalse(families["dev"] & families["test"])
        self.assertFalse(families["validation"] & families["test"])

    def test_records_round_trip_and_queries_do_not_leak_answers(self):
        prefix = self.prefixes[0]
        gold = self.gold[0]
        round_trip = PrefixRecord.from_dict(prefix.to_dict())
        self.assertEqual(round_trip.prefix_hash, prefix.prefix_hash)
        self.assertEqual(FailureChainGold.from_dict(gold.to_dict()).gold_hash, gold.gold_hash)
        item_queries = [item for item in self.queries if item.prefix_id == prefix.prefix_id]
        self.assertEqual(len(item_queries), 6)
        for query in item_queries:
            self.assertEqual(QueryRecord.from_dict(query.to_dict()).query_hash, query.query_hash)
            self.assertNotIn(gold.error_signature, query.text)
            self.assertNotIn(gold.failed_action, query.text)
            self.assertNotIn(gold.replacement_action, query.text)

    def test_ingest_is_query_hidden_and_causal_reactivation_is_query_aware(self):
        prefix = self.prefixes[0]
        item_queries = [item for item in self.queries if item.prefix_id == prefix.prefix_id]
        audit = next(item for item in item_queries if item.query_type == "audit_chain")
        distractor = next(item for item in item_queries if item.track == "distractor")
        adapter = ReferenceMemoryAdapter("M5_lifecycle_causal_reactivation")
        first = adapter.ingest(prefix, prefix.budget_tokens)
        second = adapter.ingest(prefix, prefix.budget_tokens)
        self.assertEqual(first.state_hash, second.state_hash)
        self.assertFalse(first.ingestion_usage["future_query_observed"])
        historical = adapter.materialize(first, audit, prefix.budget_tokens)
        current = adapter.materialize(first, distractor, prefix.budget_tokens)
        self.assertGreater(len(historical.retrieved_event_ids), 0)
        self.assertEqual(current.retrieved_event_ids, ())

    def test_same_budget_oracle_and_irrelevant_control(self):
        prefix = next(item for item in self.prefixes if item.recoverability == "R1")
        gold = next(item for item in self.gold if item.prefix_id == prefix.prefix_id)
        query = next(
            item
            for item in self.queries
            if item.prefix_id == prefix.prefix_id and item.query_type == "interactive_reacquisition"
        )
        _, deleted = counterfactual_bundle(prefix, query, gold, condition_id="candidate")
        _, oracle = counterfactual_bundle(
            prefix, query, gold, condition_id="oracle_failure_chain"
        )
        _, irrelevant = counterfactual_bundle(
            prefix, query, gold, condition_id="irrelevant_size_control"
        )
        chain = set(gold.ordered_event_ids)
        self.assertFalse(chain & set(deleted.visible_event_ids))
        self.assertTrue(chain.issubset(oracle.visible_event_ids))
        self.assertFalse(chain & set(irrelevant.visible_event_ids))
        self.assertLessEqual(oracle.token_count, prefix.budget_tokens)
        self.assertLessEqual(irrelevant.token_count, prefix.budget_tokens)

    def test_official_acon_bridge_preserves_query_hidden_contract(self):
        prefix = self.prefixes[0]
        query = next(item for item in self.queries if item.prefix_id == prefix.prefix_id)

        class Runtime:
            def prepare(self, messages, *, new_indices):
                self.messages = messages
                self.new_indices = new_indices
                return SimpleNamespace(
                    runtime_main_result_eligible=True,
                    included_indices=tuple(range(min(len(messages), len(prefix.events)))),
                    content_overrides={0: "ACON summary"},
                    metadata=lambda: {
                        "compressor_provider_input_tokens": 10,
                        "compressor_provider_output_tokens": 2,
                        "compressor_latency_seconds": 0.1,
                        "compressor_cost_usd": 0.001,
                        "accounting_complete": True,
                        "provenance": {"source_manifest_verified": True},
                    },
                )

        adapter = AconCompressionAdapter(Runtime)
        state = adapter.ingest(prefix, 10000)
        bundle = adapter.materialize(state, query, 10000)
        self.assertFalse(state.ingestion_usage["future_query_observed"])
        self.assertEqual(state.ingestion_usage["provider_input_tokens"], 10)
        self.assertEqual(bundle.method_id, "ACON_official")
        self.assertEqual(bundle.records[0]["content"], "ACON summary")

    def test_structured_scorer_distinguishes_visibility_loss(self):
        prefix = self.prefixes[0]
        gold = self.gold[0]
        query = next(
            item
            for item in self.queries
            if item.prefix_id == prefix.prefix_id and item.query_type == "audit_failed_action"
        )
        full_adapter = ReferenceMemoryAdapter("M0_full_history")
        full_state = full_adapter.ingest(prefix, prefix.budget_tokens)
        full_bundle = full_adapter.materialize(full_state, query, prefix.budget_tokens)
        full_answer = deterministic_answer(query, gold, full_bundle)
        full_episode = {
            "episode_id": "full",
            "method_id": "M0_full_history",
            "condition_id": "full",
            "model": "fixture",
            "status": "complete",
            "answer": full_answer,
            "artifact": memory_artifact(prefix, full_state, full_bundle).to_dict(),
            "model_calls": [],
            "tool_calls": [],
            "provider_input_tokens": 0,
            "provider_output_tokens": 0,
            "request_hash": "a",
            "response_hash": "b",
        }
        deleted_state, deleted_bundle = counterfactual_bundle(
            prefix, query, gold, condition_id="candidate"
        )
        deleted_episode = dict(full_episode)
        deleted_episode.update(
            {
                "episode_id": "deleted",
                "method_id": "failure_chain_deletion",
                "condition_id": "candidate",
                "answer": deterministic_answer(query, gold, deleted_bundle),
                "artifact": memory_artifact(prefix, deleted_state, deleted_bundle).to_dict(),
            }
        )
        self.assertTrue(score_episode(full_episode, prefix, query, gold)["audit_pass"])
        self.assertFalse(score_episode(deleted_episode, prefix, query, gold)["audit_pass"])
        self.assertTrue(
            score_episode(deleted_episode, prefix, query, gold)["honest_abstention"]
        )

    def test_v0_matrix_has_exact_request_ceiling(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            minimal_dataset(root)
            trials = prepare_v0_trials(root)
            self.assertEqual(len(trials), 272)
            self.assertEqual(sum(item["max_model_turns"] for item in trials), 368)
            self.assertEqual(sum(item["track"] == "audit_qa" for item in trials), 240)
            self.assertEqual(
                sum(item["track"] == "interactive_reacquisition" for item in trials), 32
            )

    def test_deterministic_run_and_score_are_manifested_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset"
            minimal_dataset(dataset)
            run_root = root / "run"
            manifest = run_deterministic(
                dataset,
                run_root,
                methods=("M0_full_history",),
                query_types=("audit_failed_action",),
            )
            self.assertEqual(manifest["episode_count"], 1)
            self.assertTrue((run_root / "episodes.jsonl").is_file())
            self.assertTrue((run_root / "file_manifest.jsonl").is_file())
            with self.assertRaises(FileExistsError):
                run_deterministic(dataset, run_root)
            score_root = root / "score"
            scored = score_run(dataset, run_root, score_root, bootstrap_samples=10)
            self.assertEqual(scored["manifest"]["episodes"], 1)
            self.assertTrue((score_root / "file_manifest.jsonl").is_file())


class CompressionAuditRealGateTests(unittest.TestCase):
    def annotation_row(self, index, source, split):
        event_ids = [f"source-{index}-{offset}" for offset in range(6)]
        return {
            "source": source,
            "candidate_id": f"candidate-{index}",
            "repository": f"{split}-repo-{index}",
            "task_id": f"task-{index}",
            "split": split,
            "trajectory_revision": "a" * 40,
            "prefix": {
                "task_domain": "software",
                "budget_tokens": 2048,
                "events": [
                    {
                        "source_event_id": event_id,
                        "kind": "tool_call" if offset in {0, 4} else "observation",
                        "content": f"event {offset}",
                        "causal_role": (
                            "failed_action",
                            "failure_result",
                            "diagnostic_evidence",
                            "switch_decision",
                            "replacement_action",
                            "resolution_evidence",
                        )[offset],
                    }
                    for offset, event_id in enumerate(event_ids)
                ],
                "messages": [{"role": "user", "content": "task"}],
                "tool_schemas": [],
                "current_fact": "",
                "current_source_event_ids": [],
            },
            "failure_chain": {
                "failure_family": f"fixture-family-{index % 10}",
                "failed_action": "method_a",
                "failed_arguments": {},
                "error_signature": "fixture_error",
                "diagnostic_evidence": "fixture diagnosis",
                "switch_decision": "switch methods",
                "replacement_action": "method_b",
                "replacement_arguments": {},
                "resolution_evidence": "success",
                "ordered_source_event_ids": event_ids,
                "recoverability": f"R{index % 4}",
            },
            "replay": {
                "docker_replayable": source == "swe_gym",
                "snapshot_ref": "fixture",
            },
            "annotator": "fixture",
            "adjudicator": "fixture",
            "annotation_status": "adjudicated",
        }

    def test_double_human_gate_and_real_import(self):
        rows = []
        for index in range(100):
            source = "swe_gym" if index < 60 else "ama_bench"
            split = "dev" if index < 20 else "validation" if index < 40 else "test"
            rows.append(self.annotation_row(index, source, split))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            log = root / "replay-proof.log"
            log.write_text("mocked replay receipt for the unit test only\n", encoding="utf-8")
            for row in rows:
                if row["source"] == "swe_gym":
                    row["replay"]["verification"] = {
                        "status": "passed", "exit_code": 0,
                        "image_ref": "fixture@sha256:" + "a" * 64,
                        "command": ["fixture-only"], "log_path": log.name,
                        "log_sha256": file_sha256(log),
                    }
            candidates = copy.deepcopy(rows)
            annotator_a = copy.deepcopy(rows)
            annotator_b = copy.deepcopy(rows)
            adjudicated = copy.deepcopy(rows)
            for candidate, left, right, final in zip(
                candidates, annotator_a, annotator_b, adjudicated, strict=True
            ):
                candidate["annotation_status"] = "candidate"
                candidate["annotator"] = ""
                left["annotation_status"] = "annotated"
                left["annotator"] = "annotator-a"
                right["annotation_status"] = "annotated"
                right["annotator"] = "annotator-b"
                final["annotator_a_sha256"] = stable_digest(left)
                final["annotator_b_sha256"] = stable_digest(right)
            write_jsonl(root / "candidates.jsonl", candidates)
            write_jsonl(root / "annotator_a.jsonl", annotator_a)
            write_jsonl(root / "annotator_b.jsonl", annotator_b)
            write_jsonl(root / "adjudicated.jsonl", adjudicated)
            report = validate_real_annotations(root)
            self.assertTrue(report["ready"])
            prefixes, gold, queries = import_adjudicated_real(root)
            self.assertEqual(len(prefixes), 100)
            self.assertEqual(len(gold), 100)
            self.assertEqual(len(queries), 520)
            self.assertTrue(all(item.source_kind == "real_trajectory" for item in prefixes))

    def test_candidate_miners_propose_windows_but_never_gold(self):
        swe_row = {
            "instance_id": "org__repo-1",
            "run_id": "run-1",
            "resolved": True,
            "messages": [
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "a",
                            "function": {
                                "name": "execute_bash",
                                "arguments": json.dumps({"command": "bad --flag"}),
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "a", "content": "exit code 2: invalid flag"},
                {"role": "assistant", "content": "The flag is unsupported; use the portable form."},
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "b",
                            "function": {
                                "name": "execute_bash",
                                "arguments": json.dumps({"command": "good"}),
                            },
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "b", "content": "exit code 0; tests passed"},
            ],
        }
        ama_row = {
            "episode_id": 9,
            "task_type": "fixture",
            "domain": "Web",
            "success": True,
            "trajectory": [
                {"turn_idx": 0, "action": "left", "observation": "same state"},
                {"turn_idx": 1, "action": "left", "observation": "same state"},
                {"turn_idx": 2, "action": "right", "observation": "goal reached successfully"},
            ],
        }
        swe = mine_swe_gym_candidates(
            [swe_row], revision="a" * 40, source_file_sha256="b" * 64
        )
        ama = mine_ama_bench_candidates(
            [ama_row], revision="c" * 40, source_file_sha256="d" * 64
        )
        self.assertEqual(len(swe), 1)
        self.assertEqual(len(ama), 1)
        for candidate in (*swe, *ama):
            self.assertEqual(candidate["annotation_status"], "candidate")
            self.assertTrue(candidate["candidate_hints"]["proposal_only"])
            self.assertEqual(candidate["failure_chain"]["failed_action"], "")


class CompressionAuditLiveTests(unittest.TestCase):
    def config(self):
        today = __import__("datetime").date.today().isoformat()
        return {
            "v0_live": {
                "seed": 1,
                "context_tokenizer": {
                    "model": "qwen3.8-27b",
                    "path": "fixture-only.json",
                    "sha256": "a" * 64,
                    "source": "fixture-only",
                },
                "model": {
                    "provider": "dashscope",
                    "name": "qwen3.8-27b",
                    "fallback": None,
                    "temperature": 0,
                    "enable_thinking": False,
                    "max_output_tokens": 512,
                },
                "pricing_snapshot": {
                    "checked_at": today,
                    "currency": "CNY",
                    "region": "china-beijing",
                    "prices_per_million_tokens": {
                        "qwen3.8-27b": {"input": 3.0, "output": 12.0}
                    },
                },
                "limits": {
                    "maximum_cost_cny": 100.0,
                    "request_count_hard_max": 368,
                    "request_input_tokens_hard_max": 50000,
                    "retry_per_request_max": 0,
                },
                "authorization": {
                    "authorized_by_user": True,
                    "maximum_cost_cny": 100.0,
                    "authorization_id": "test-live-authorization",
                },
            }
        }

    def test_live_preflight_is_cost_bounded_and_fail_closed(self):
        config = self.config()
        report = validate_live_authorization(config)
        self.assertTrue(report["valid"])
        self.assertLess(theoretical_maximum_cost_cny(config), 100.0)
        config["v0_live"]["pricing_snapshot"]["checked_at"] = "2000-01-01"
        self.assertFalse(validate_live_authorization(config)["valid"])

    def test_cli_exposes_four_benchmark_commands(self):
        parser = build_parser()
        help_text = parser.format_help()
        for command in (
            "benchmark-build",
            "benchmark-validate",
            "benchmark-run",
            "benchmark-score",
        ):
            self.assertIn(command, help_text)


if __name__ == "__main__":
    unittest.main()
