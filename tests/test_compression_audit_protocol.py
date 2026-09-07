"""Adversarial protocol tests; all provider calls in this module are mocked."""

import copy
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import test_compression_audit as audit_test_fixtures
from test_compression_audit import minimal_dataset, write_jsonl
from tracegraph.benchmark.compression_audit.dataset import (
    ContextBundle,
    PrefixRecord,
    convert_legacy_diagnostic,
    generate_controlled_dataset,
    load_jsonl,
)
from tracegraph.capture import estimate_tokens
from tracegraph.compression_audit_tokenization import VerifiedContextTokenizer, retokenize_trials
from tracegraph.benchmark.compression_audit.live import (
    _provider_body,
    _tool_result,
    _usage,
    prepare_live_run,
    reconcile_live_recordings,
    request_input_token_upper_bound,
    run_live_v0,
)
from tracegraph.benchmark.compression_audit.metrics import _request_integrity, score_episode, score_run
from tracegraph.benchmark.compression_audit.runtime import (
    RANKED_REFERENCE_METHODS,
    ReferenceMemoryAdapter,
    counterfactual_bundle,
    deterministic_answer,
    memory_artifact,
    prepare_v0_trials,
)
from tracegraph.live_guard import require_live_authorization_id


FROZEN_ROOT = Path(os.environ.get("TRACEGRAPH_FROZEN_ROOT", Path.cwd()))


class ProtocolIsolationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.prefixes, cls.gold, cls.queries = generate_controlled_dataset()

    def test_ranked_ingestion_cannot_depend_on_gold_labels(self):
        original = self.prefixes[0]
        public = PrefixRecord.from_dict(original.to_dict())
        self.assertTrue(all("causal_role" not in event for event in public.events))
        scrambled = replace(
            original,
            events=tuple({**event, "causal_role": "wrong_gold_label"} for event in original.events),
        )
        for method in RANKED_REFERENCE_METHODS:
            adapter = ReferenceMemoryAdapter(method)
            states = [adapter.ingest(prefix, prefix.budget_tokens) for prefix in (original, public, scrambled)]
            self.assertEqual(len({state.state_hash for state in states}), 1)
            self.assertFalse(states[0].ingestion_usage["hidden_gold_observed"])

    def test_mutated_memory_is_rejected_before_query_materialization(self):
        prefix = self.prefixes[0]
        adapter = ReferenceMemoryAdapter("M5_lifecycle_causal_reactivation")
        state = adapter.ingest(prefix, prefix.budget_tokens)
        state.ingestion_usage["observed_failure_chain_event_ids"].append("injected")
        with self.assertRaisesRegex(ValueError, "modified"):
            adapter.materialize(state, self.queries[0], prefix.budget_tokens)

    def test_every_reference_method_respects_budget_and_tool_pairs(self):
        query_by_prefix = {query.prefix_id: query for query in self.queries if query.query_type == "audit_chain"}
        for prefix in self.prefixes[::8]:
            for method in RANKED_REFERENCE_METHODS:
                adapter = ReferenceMemoryAdapter(method)
                state = adapter.ingest(prefix, prefix.budget_tokens)
                bundle = adapter.materialize(state, query_by_prefix[prefix.prefix_id], prefix.budget_tokens)
                self.assertLessEqual(bundle.token_count, bundle.budget_tokens)
                visible = set(bundle.visible_event_ids)
                for event in prefix.events:
                    if event["event_id"] not in visible or not event.get("call_id"):
                        continue
                    group = {item["event_id"] for item in prefix.events if item.get("call_id") == event["call_id"]}
                    self.assertTrue(group.issubset(visible))

    def test_oracle_and_control_match_size_and_eviction_set(self):
        prefix, gold = self.prefixes[0], self.gold[0]
        query = self.queries[0]
        state, oracle = counterfactual_bundle(prefix, query, gold, condition_id="oracle_failure_chain")
        other_state, control = counterfactual_bundle(prefix, query, gold, condition_id="irrelevant_size_control")
        self.assertEqual(state.state_hash, other_state.state_hash)
        self.assertEqual(oracle.token_count, control.token_count)
        self.assertEqual(len(json.dumps(oracle.records, ensure_ascii=False).encode()), len(json.dumps(control.records, ensure_ascii=False).encode()))
        self.assertEqual(oracle.retrieval_usage["common_base_event_ids"], control.retrieval_usage["common_base_event_ids"])
        artifact = memory_artifact(prefix, state, oracle).to_dict()
        self.assertEqual(set(artifact["visible_event_ids"]), set(oracle.visible_event_ids))
        self.assertFalse(artifact["exact_model_token_count"])
        with self.assertRaisesRegex(ValueError, "cannot fit"):
            counterfactual_bundle(prefix, query, gold, condition_id="oracle_failure_chain", budget_tokens=20)

    def test_budget_overflow_is_rejected_at_context_boundary(self):
        with self.assertRaisesRegex(ValueError, "exceeds"):
            ContextBundle.create(prefix_id="p", query_id="q", method_id="m", condition_id="candidate", records=[{"content": "x" * 100}], visible_event_ids=(), budget_tokens=1)

    def test_multi_failure_family_contains_real_distinct_failed_retries(self):
        prefix = next(item for item in self.prefixes if item.failure_family == "multi_failure_recovery")
        errors = [event for event in prefix.events if event["kind"] == "error"]
        self.assertEqual(len(errors), 3)
        self.assertEqual(len({event["call_id"] for event in errors}), 3)
        gold = next(item for item in self.gold if item.prefix_id == prefix.prefix_id)
        self.assertTrue({event["event_id"] for event in errors}.issubset(gold.ordered_event_ids))
        query = next(item for item in self.queries if item.prefix_id == prefix.prefix_id)
        _, oracle = counterfactual_bundle(prefix, query, gold, condition_id="oracle_failure_chain")
        self.assertLessEqual(oracle.token_count, prefix.budget_tokens)

    def test_r0_cannot_recover_and_r3_actions_are_blocked(self):
        for level in ("R0", "R3"):
            prefix = next(item for item in self.prefixes if item.recoverability == level)
            gold = next(item for item in self.gold if item.prefix_id == prefix.prefix_id)
            result = _tool_result("read_audit_log" if level == "R0" else "repeat_failed_action", {}, prefix, gold, [])
            self.assertFalse(result["source_event_ids"])
            self.assertFalse(result["executed_side_effect"])
            self.assertEqual(result["content"]["status"], "unavailable" if level == "R0" else "blocked")

    def test_r2_requires_successful_stages_and_tools_never_return_gold_text(self):
        prefix = next(item for item in self.prefixes if item.recoverability == "R2")
        gold = next(item for item in self.gold if item.prefix_id == prefix.prefix_id)
        tampered_gold = replace(gold, failed_action="GOLD_ONLY_SENTINEL", error_signature="GOLD_ONLY_SENTINEL")
        calls = [_tool_result("read_audit_log", {}, prefix, tampered_gold, [])]
        self.assertEqual(calls[0]["content"]["next_required_tool"], "inspect_environment")
        for name in ("inspect_environment", "replay_in_sandbox", "read_audit_log"):
            result = _tool_result(name, {}, prefix, tampered_gold, calls)
            self.assertEqual(result["content"]["status"], "evidence_found")
            self.assertNotIn("GOLD_ONLY_SENTINEL", json.dumps(result))
            calls.append(result)
        self.assertEqual(set(calls[-1]["source_event_ids"]), set(gold.ordered_event_ids))

    def test_retrieved_but_uncited_evidence_does_not_pass(self):
        prefix, gold, query = self.prefixes[0], self.gold[0], self.queries[0]
        adapter = ReferenceMemoryAdapter("M0_full_history")
        state = adapter.ingest(prefix, prefix.budget_tokens)
        bundle = adapter.materialize(state, query, prefix.budget_tokens)
        answer = deterministic_answer(query, gold, bundle)
        answer["evidence_event_ids"] = []
        episode = {"status": "complete", "answer": answer, "artifact": memory_artifact(prefix, state, bundle).to_dict(), "tool_calls": [{"source_event_ids": list(gold.ordered_event_ids)}]}
        self.assertFalse(score_episode(episode, prefix, query, gold)["audit_pass"])

    def test_legacy_adapter_preserves_chronology_and_actual_slot_evidence(self):
        root = FROZEN_ROOT / "outputs/phase6/e1_controlled_v1"
        if not root.is_dir():
            self.skipTest("immutable local legacy fixture is not installed")
        prefixes, gold, _ = convert_legacy_diagnostic(root)
        for prefix, chain in zip(prefixes, gold, strict=True):
            positions = {event["event_id"]: event["step_id"] for event in prefix.events}
            self.assertEqual([positions[item] for item in chain.ordered_event_ids], sorted(positions[item] for item in chain.ordered_event_ids))
            if chain.chain_applicable:
                events = {event["event_id"]: event for event in prefix.events}
                self.assertEqual(events[chain.evidence_by_field["replacement_action"][0]]["kind"], "tool_call")
                self.assertEqual(events[chain.evidence_by_field["switch_decision"][0]]["kind"], "decision")
        self.assertEqual(sum(item.chain_applicable for item in gold), 8)


class LiveFailureSafetyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.dataset = self.root / "dataset"
        self.prefixes, self.gold, self.queries = minimal_dataset(self.dataset)
        self.config = audit_test_fixtures.CompressionAuditLiveTests().config()
        self.auth_id = self.config["v0_live"]["authorization"]["authorization_id"]
        self.config["v0_live"]["limits"]["timeout_seconds"] = 1
        self.config_path = self.root / "config.json"
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")
        self.run = self.root / "run"
        self.runner_module = "tracegraph.benchmark.compression_audit.live_runner"
        self.authorization_module = (
            "tracegraph.benchmark.compression_audit.live_authorization"
        )
        self.config_patches = [
            patch(f"{self.runner_module}.load_config", return_value=self.config),
            patch(f"{self.authorization_module}.load_config", return_value=self.config),
        ]
        self.credential_patch = patch(
            f"{self.runner_module}.load_ignored_dashscope_credentials",
            return_value=("test-only", "https://example.invalid"),
        )
        self.tokenizer_patches = [
            patch(f"{module}.VerifiedContextTokenizer")
            for module in (self.authorization_module, self.runner_module)
        ]
        self.retokenize_patches = [
            patch(
                f"{module}.retokenize_trials",
                side_effect=lambda rows, tokenizer: rows,
            )
            for module in (self.authorization_module, self.runner_module)
        ]
        self.live_env_patch = patch.dict(os.environ, {"TRACEGRAPH_DISABLE_LIVE": "0"})
        self.live_env_patch.start()
        for config_patch in self.config_patches:
            config_patch.start()
        self.credential_patch.start()
        for tokenizer_patch in self.tokenizer_patches:
            tokenizer = tokenizer_patch.start().return_value
            tokenizer.provenance = {"fixture_only": True}
            tokenizer.count.side_effect = estimate_tokens
        for retokenize_patch in self.retokenize_patches:
            retokenize_patch.start()

    def tearDown(self):
        self.live_env_patch.stop()
        self.credential_patch.stop()
        for config_patch in reversed(self.config_patches):
            config_patch.stop()
        for retokenize_patch in reversed(self.retokenize_patches):
            retokenize_patch.stop()
        for tokenizer_patch in reversed(self.tokenizer_patches):
            tokenizer_patch.stop()
        self.directory.cleanup()

    def response(self):
        prefix, gold, query = self.prefixes[0], self.gold[0], self.queries[0]
        adapter = ReferenceMemoryAdapter("M0_full_history")
        state = adapter.ingest(prefix, prefix.budget_tokens)
        bundle = adapter.materialize(state, query, prefix.budget_tokens)
        answer = deterministic_answer(query, gold, bundle)
        return {"usage": {"prompt_tokens": 100, "completion_tokens": 50}, "choices": [{"message": {"role": "assistant", "tool_calls": [{"id": "call-fixture", "type": "function", "function": {"name": "submit_audit_answer", "arguments": json.dumps(answer)}}]}}]}

    def test_live_authorization_requires_matching_explicit_id(self):
        authorization = self.config["v0_live"]["authorization"]
        with self.assertRaisesRegex(RuntimeError, "required"):
            require_live_authorization_id(authorization, None)
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            require_live_authorization_id(authorization, "wrong")
        self.assertEqual(
            require_live_authorization_id(authorization, self.auth_id), self.auth_id
        )

    def test_actual_request_response_usage_and_fixed_seed_are_recorded(self):
        with patch("tracegraph.benchmark.compression_audit.live_runner._post_json", return_value=(self.response(), 0.01)) as provider:
            result = run_live_v0(
                self.config_path,
                self.dataset,
                self.run,
                max_new_requests=1,
                authorization_id=self.auth_id,
            )
        self.assertEqual(provider.call_count, 1)
        self.assertEqual(result["provider_requests"], 1)
        episode = load_jsonl(self.run / "episodes.jsonl")[0]
        self.assertTrue(_request_integrity(episode))
        self.assertEqual(episode["model_calls"][0]["request"]["seed"], 1)
        broken = copy.deepcopy(episode)
        broken["model_calls"][0]["request"]["seed"] = 2
        self.assertFalse(_request_integrity(broken))
        with self.assertRaises(FileExistsError):
            run_live_v0(
                self.config_path, self.dataset, self.run, authorization_id=self.auth_id
            )

    def run_two_turn_fixture(self):
        interactive = next(
            row for row in prepare_v0_trials(self.dataset)
            if row["track"] == "interactive_reacquisition"
            and row["recoverability"] == "R1" and row["condition_id"] == "full"
        )
        first = {
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            "choices": [{"message": {
                "role": "assistant", "content": "",
                "tool_calls": [{"id": "read-log", "type": "function", "function": {
                    "name": "read_audit_log", "arguments": "{}",
                }}],
            }}],
        }
        with patch(
            f"{self.authorization_module}.prepare_v0_trials", return_value=[interactive]
        ), patch(
            f"{self.runner_module}.prepare_v0_trials", return_value=[interactive]
        ), patch(
            f"{self.runner_module}._post_json",
            side_effect=[(first, 0.01), (self.response(), 0.01)],
        ) as provider:
                run_live_v0(
                    self.config_path,
                    self.dataset,
                    self.run,
                    authorization_id=self.auth_id,
                )
                self.assertEqual(provider.call_count, 2)
        return load_jsonl(self.run / "episodes.jsonl")[0]

    def test_multiturn_requests_keep_independent_snapshots(self):
        episode = self.run_two_turn_fixture()
        self.assertEqual([len(call["request"]["messages"]) for call in episode["model_calls"]], [2, 4])
        self.assertTrue(_request_integrity(episode))
        self.assertEqual(episode["model_calls"], load_jsonl(self.run / "provider_ledger.jsonl"))

    def test_recording_reconciliation_changes_no_answer_or_source_file(self):
        from tracegraph.benchmark.compression_audit.dataset import file_sha256, write_file_manifest

        episode = self.run_two_turn_fixture()
        original_answer = copy.deepcopy(episode["answer"])
        episode["model_calls"][0]["request"]["messages"] = copy.deepcopy(
            episode["model_calls"][-1]["request"]["messages"]
        )
        self.assertFalse(_request_integrity(episode))
        write_jsonl(self.run / "episodes.jsonl", [episode])
        write_file_manifest(self.run)
        source_hash = file_sha256(self.run / "episodes.jsonl")
        output = self.root / "reconciled"
        with patch("tracegraph.benchmark.compression_audit.live_runner._post_json") as provider:
            result = reconcile_live_recordings(self.run, output)
        provider.assert_not_called()
        corrected = load_jsonl(output / "episodes.jsonl")[0]
        self.assertTrue(_request_integrity(corrected))
        self.assertEqual(corrected["answer"], original_answer)
        self.assertEqual(corrected["provider_input_tokens"], episode["provider_input_tokens"])
        self.assertEqual(file_sha256(self.run / "episodes.jsonl"), source_hash)
        self.assertEqual(result["reconciliation"]["changed_embedded_request_count"], 1)
        with self.assertRaises(FileExistsError):
            reconcile_live_recordings(self.run, output)

    def test_recording_reconciliation_refuses_an_inconsistent_durable_request(self):
        from tracegraph.benchmark.compression_audit.dataset import write_file_manifest

        self.run_two_turn_fixture()
        ledger = load_jsonl(self.run / "provider_ledger.jsonl")
        ledger[0]["request"]["seed"] = 999
        write_jsonl(self.run / "provider_ledger.jsonl", ledger)
        write_file_manifest(self.run)
        output = self.root / "reconciled"
        with self.assertRaisesRegex(ValueError, "integrity mismatch"):
            reconcile_live_recordings(self.run, output)
        self.assertFalse(output.exists())

    def test_recording_reconciliation_cannot_write_inside_its_source(self):
        self.run_two_turn_fixture()
        with self.assertRaisesRegex(ValueError, "outside the immutable source"):
            reconcile_live_recordings(self.run, self.run / "derived")
        self.assertFalse((self.run / "derived").exists())

    def test_network_error_is_durable_counted_and_never_retried(self):
        with patch("tracegraph.benchmark.compression_audit.live_runner._post_json", side_effect=RuntimeError("fixture timeout")) as provider:
            result = run_live_v0(
                self.config_path, self.dataset, self.run, authorization_id=self.auth_id
            )
            self.assertEqual(result["provider_requests"], 1)
            self.assertFalse(result["usage_complete"])
            self.assertGreater(result["provider_cost_upper_bound_cny"], 0)
            self.assertEqual(len(load_jsonl(self.run / "provider_attempts.jsonl")), 1)
            self.assertEqual(load_jsonl(self.run / "episodes.jsonl")[0]["status"], "provider_error")
            with self.assertRaisesRegex(RuntimeError, "usage is uncertain"):
                run_live_v0(
                    self.config_path,
                    self.dataset,
                    self.run,
                    resume=True,
                    authorization_id=self.auth_id,
                )
            self.assertEqual(provider.call_count, 1)

    def test_missing_usage_stops_before_a_second_request(self):
        response = self.response()
        del response["usage"]
        with patch("tracegraph.benchmark.compression_audit.live_runner._post_json", return_value=(response, 0.01)) as provider:
            result = run_live_v0(
                self.config_path, self.dataset, self.run, authorization_id=self.auth_id
            )
        self.assertEqual(provider.call_count, 1)
        self.assertFalse(result["usage_complete"])

    def test_interrupted_attempt_cannot_be_retransmitted(self):
        prepare_live_run(self.config_path, self.dataset, self.run)
        write_jsonl(self.run / "provider_attempts.jsonl", [{"request_sha256": "unsettled"}])
        with patch("tracegraph.benchmark.compression_audit.live_runner._post_json") as provider:
            with self.assertRaisesRegex(RuntimeError, "uncertain outcome"):
                run_live_v0(
                    self.config_path,
                    self.dataset,
                    self.run,
                    resume=True,
                    authorization_id=self.auth_id,
                )
        provider.assert_not_called()

    def test_parallel_live_writer_is_refused_before_provider_use(self):
        lock = self.root / ".run.live.lock"
        lock.write_text("fixture writer", encoding="utf-8")
        with patch("tracegraph.benchmark.compression_audit.live_runner._post_json") as provider:
            with self.assertRaisesRegex(RuntimeError, "writer lock"):
                run_live_v0(
                    self.config_path,
                    self.dataset,
                    self.run,
                    authorization_id=self.auth_id,
                )
        provider.assert_not_called()

    def test_episode_turn_reservation_prevents_partial_start(self):
        interactive = prepare_v0_trials(self.dataset)[240]
        with patch(
            f"{self.authorization_module}.prepare_v0_trials", return_value=[interactive]
        ), patch(
            f"{self.runner_module}.prepare_v0_trials", return_value=[interactive]
        ), patch(f"{self.runner_module}._post_json") as provider:
                result = run_live_v0(
                    self.config_path,
                    self.dataset,
                    self.run,
                    max_new_requests=3,
                    authorization_id=self.auth_id,
                )
        self.assertEqual(result["provider_requests"], 0)
        self.assertEqual(result["completed_episode_count"], 0)
        provider.assert_not_called()

    def test_invalid_structured_response_is_not_counted_as_valid_output(self):
        response = self.response()
        response["choices"][0]["message"]["tool_calls"] = []
        with patch("tracegraph.benchmark.compression_audit.live_runner._post_json", return_value=(response, 0.01)):
            run_live_v0(
                self.config_path,
                self.dataset,
                self.run,
                max_new_requests=1,
                authorization_id=self.auth_id,
            )
        result = score_run(self.dataset, self.run, self.root / "score", bootstrap_samples=10)
        gate = next(item for item in result["gates"]["gates"] if item["name"] == "structured_output_coverage")
        self.assertEqual(gate["value"], 0)

    def test_usage_and_text_only_ceiling_reject_invalid_values(self):
        for usage in ({"prompt_tokens": -1, "completion_tokens": 1}, {"prompt_tokens": True, "completion_tokens": 1}):
            with self.assertRaises(RuntimeError):
                _usage({"usage": usage})
        body = _provider_body({"messages": [{"role": "user", "content": "你好"}], "tools": []}, self.config)
        self.assertGreater(request_input_token_upper_bound(body), len(json.dumps(body)))
        body["messages"][0]["content"] = [{"type": "image_url"}]
        with self.assertRaises(ValueError):
            request_input_token_upper_bound(body)


class PinnedTokenizerTests(unittest.TestCase):
    def test_official_tokenizer_exactly_matches_every_oracle_control(self):
        specification = json.loads(Path("configs/compression_audit_v1.json").read_text(encoding="utf-8"))["v0_live"]["context_tokenizer"]
        if not (FROZEN_ROOT / specification["path"]).is_file():
            self.skipTest("optional pinned tokenizer fixture is not downloaded")
        legacy_root = FROZEN_ROOT / "outputs/phase6/e1_controlled_v1"
        if not legacy_root.is_dir():
            self.skipTest("immutable legacy compatibility fixture is not installed")
        tokenizer = VerifiedContextTokenizer(specification, FROZEN_ROOT)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            prefixes, gold, queries = convert_legacy_diagnostic(legacy_root)
            for filename, items in (
                ("audit_prefixes", prefixes), ("audit_gold", gold), ("audit_queries", queries),
            ):
                write_jsonl(root / "legacy_diagnostic" / f"{filename}.jsonl", [item.to_dict() for item in items])
            rows = retokenize_trials(prepare_v0_trials(root), tokenizer)
        oracle = {row["query_id"]: row for row in rows if row["condition_id"] == "oracle_failure_chain"}
        for row in rows:
            self.assertTrue(row["artifact"]["exact_model_token_count"])
            if row["condition_id"] == "irrelevant_size_control":
                self.assertEqual(row["artifact"]["context_tokens"], oracle[row["query_id"]]["artifact"]["context_tokens"])
                self.assertEqual(row["tokenized_user_input_tokens"], oracle[row["query_id"]]["tokenized_user_input_tokens"])
        bad = {**specification, "sha256": "0" * 64}
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            VerifiedContextTokenizer(bad, FROZEN_ROOT)


if __name__ == "__main__":
    unittest.main()
