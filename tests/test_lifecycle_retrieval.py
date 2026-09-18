from __future__ import annotations

from tracegraph.capture import estimate_tokens
from tracegraph.benchmark.compression_audit.controlled import generate_controlled_dataset
from tracegraph.benchmark.compression_audit.models import PrefixRecord
from tracegraph.benchmark.server_eval.config import METHODS as SERVER_METHODS
from tracegraph.benchmark.server_eval.lifecycle_retrieval import (
    CAUSAL_RETRIEVAL,
    LIFECYCLE_CARDS,
    LIFECYCLE_RETRIEVAL,
    LifecycleRetrievalAdapter,
    archive_trigger_terms,
)
from tracegraph.benchmark.server_eval.methods import MemoryMethods


def _case():
    prefixes, gold_rows, queries = generate_controlled_dataset()
    prefix = next(
        item for item in prefixes
        if item.split == "dev" and item.context_length == "long"
    )
    gold = next(item for item in gold_rows if item.prefix_id == prefix.prefix_id)
    query_map = {
        item.query_type: item
        for item in queries
        if item.prefix_id == prefix.prefix_id
    }
    return prefix, gold, query_map


def test_public_archive_gate_is_fixed_and_current_only_is_negative():
    assert archive_trigger_terms(
        "Reconstruct the earlier failed-to-successful sequence."
    ) == ("earlier", "failed", "reconstruct", "sequence")
    assert archive_trigger_terms(
        "Report the current status only. Do not revisit unrelated historical work."
    ) == ()


def test_lifecycle_cards_never_read_archive_after_query():
    prefix, gold, queries = _case()
    adapter = LifecycleRetrievalAdapter(
        LIFECYCLE_CARDS, token_counter=estimate_tokens
    )
    state = adapter.ingest(prefix, 1024)
    bundle = adapter.materialize(state, queries["audit_chain"], 1024)

    assert state.ingestion_usage["future_query_observed"] is False
    assert state.ingestion_usage["hidden_gold_observed"] is False
    assert bundle.retrieval_usage["archive_access"] is False
    assert bundle.retrieval_usage["archive_triggered"] is False
    assert bundle.retrieved_event_ids == ()
    assert not set(gold.ordered_event_ids) <= set(bundle.visible_event_ids)


def test_lifecycle_resident_memory_uses_card_not_raw_unresolved_error():
    prefix, _, _ = _case()
    value = prefix.to_dict()
    value["events"] = value["events"][:5]
    value["messages"] = value["messages"][:5]
    value.pop("prefix_hash", None)
    unresolved = PrefixRecord.from_dict(value)
    adapter = LifecycleRetrievalAdapter(
        LIFECYCLE_CARDS, token_counter=estimate_tokens
    )
    state = adapter.ingest(unresolved, 1024)

    assert any(
        entry["record"]["kind"] == "failure_card"
        for entry in state.summaries
    )
    assert state.ingestion_usage["lifecycle_view"]["metadata"][
        "raw_failure_messages_selected"
    ] == 0


def test_hybrid_keeps_current_query_resident_only_and_restores_audit_chain():
    prefix, gold, queries = _case()
    adapter = LifecycleRetrievalAdapter(
        LIFECYCLE_RETRIEVAL, token_counter=estimate_tokens
    )
    state = adapter.ingest(prefix, 1024)

    current = adapter.materialize(state, queries["distractor_current"], 1024)
    assert current.retrieval_usage["archive_triggered"] is False
    assert current.retrieved_event_ids == ()

    audit = adapter.materialize(state, queries["audit_chain"], 1024)
    assert audit.retrieval_usage["archive_triggered"] is True
    assert audit.retrieval_usage["send_eligible"] is True
    assert set(gold.ordered_event_ids) <= set(audit.visible_event_ids)
    assert set(audit.retrieved_event_ids) <= set(state.archived_event_ids)
    assert audit.token_count <= audit.budget_tokens == 1024


def test_hybrid_refuses_an_indivisible_chain_that_exceeds_provider_limit():
    prefix, _, queries = _case()
    adapter = LifecycleRetrievalAdapter(
        LIFECYCLE_RETRIEVAL, token_counter=estimate_tokens
    )
    state = adapter.ingest(prefix, 256)
    bundle = adapter.materialize(state, queries["audit_chain"], 256)

    assert bundle.retrieval_usage["archive_triggered"] is True
    assert bundle.retrieval_usage["send_eligible"] is False
    assert "causal_closure_exceeds_provider_limit" in bundle.retrieval_usage["safety_reasons"]
    assert bundle.records == ()
    assert bundle.visible_event_ids == ()


def test_server_method_registry_preserves_legacy_and_exposes_three_names():
    assert {
        "tracegraph_0_4",
        CAUSAL_RETRIEVAL,
        LIFECYCLE_CARDS,
        LIFECYCLE_RETRIEVAL,
    } <= set(SERVER_METHODS)

    prefix, gold, queries = _case()
    methods = MemoryMethods(
        {"summary_chunk_tokens": 1024}, estimate_tokens, workspace=None
    )
    outputs = {}
    for method in (CAUSAL_RETRIEVAL, LIFECYCLE_CARDS, LIFECYCLE_RETRIEVAL):
        state = methods.build(prefix, method, 1024, f"build:{method}")
        outputs[method] = methods.materialize(
            state, prefix, queries["audit_chain"], f"answer:{method}"
        )
        assert outputs[method]["method_id"] == method

    assert set(gold.ordered_event_ids) <= set(
        outputs[CAUSAL_RETRIEVAL]["visible_event_ids"]
    )
    assert set(gold.ordered_event_ids) <= set(
        outputs[LIFECYCLE_RETRIEVAL]["visible_event_ids"]
    )
    assert not set(gold.ordered_event_ids) <= set(
        outputs[LIFECYCLE_CARDS]["visible_event_ids"]
    )
