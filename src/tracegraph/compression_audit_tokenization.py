"""Hash-pinned, offline model tokenization for exact counterfactual size controls."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .capture import estimate_tokens
from .benchmark.compression_audit.dataset import ContextBundle, canonical_json, file_sha256, stable_digest


class VerifiedContextTokenizer:
    """Load tokenizer data only; never execute remote model code or call a provider."""

    def __init__(self, specification: Mapping[str, Any], workspace: Path) -> None:
        path = (workspace / str(specification["path"])).resolve()
        if not path.is_file():
            raise FileNotFoundError(
                "pinned context tokenizer missing; run scripts/fetch_compression_audit_tokenizer.py"
            )
        actual_hash = file_sha256(path)
        if actual_hash != specification.get("sha256"):
            raise ValueError("context tokenizer SHA-256 does not match the pinned official artifact")
        try:
            import tokenizers
        except ImportError as error:
            raise RuntimeError("exact context matching requires the benchmark optional dependency") from error
        self.tokenizer = tokenizers.Tokenizer.from_file(str(path))
        self.provenance = {
            "model": str(specification["model"]),
            "source": str(specification["source"]),
            "sha256": actual_hash,
            "engine": "huggingface_tokenizers",
            "engine_version": tokenizers.__version__,
            "serialization": "canonical_json_utf8_no_special_tokens",
            "remote_code_executed": False,
        }
        definition = json.loads(self.tokenizer.to_str())
        if definition.get("model", {}).get("type") != "BPE" or "ByteLevel" not in canonical_json(definition.get("pre_tokenizer")):
            raise ValueError("the v0 input-cost bound requires a byte-level BPE tokenizer")

    def count(self, value: Any) -> int:
        text = value if isinstance(value, str) else canonical_json(value)
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)


def match_irrelevant_tokens(
    records: Sequence[Mapping[str, Any]],
    target: int,
    tokenizer: VerifiedContextTokenizer,
) -> list[dict[str, Any]]:
    """Pad only irrelevant content, leaving the common eviction set untouched."""

    values = json.loads(canonical_json(records))
    controls = [item for item in values if item.get("representation") == "irrelevant_size_control"]
    if len(controls) != 1:
        raise ValueError("exact size matching requires one unrelated control record")
    control = controls[0]

    def count_with(words: int, suffix: str = "") -> int:
        control["content"] = " neutral" * words + suffix
        return tokenizer.count(values)

    if count_with(0) > target:
        raise ValueError("control metadata alone exceeds the oracle's model-token size")
    low, high = 0, max(1, target * 2)
    for _ in range(24):
        middle = (low + high) // 2
        count = count_with(middle)
        if count == target:
            return values
        if count < target:
            low = middle + 1
        else:
            high = middle - 1
        if low > high:
            break
    for words in range(max(0, high - 4), max(0, low) + 5):
        for suffix in ("", " ", ".", " x", " 0", " unrelated", " .", " x ."):
            if count_with(words, suffix) == target:
                return values
    raise ValueError("could not construct an exactly model-token-matched irrelevant control")


def retokenize_trials(
    trials: Sequence[Mapping[str, Any]],
    tokenizer: VerifiedContextTokenizer,
) -> list[dict[str, Any]]:
    rows = json.loads(canonical_json(trials))
    full_counts = {
        str(row["prefix_id"]): tokenizer.count(row["artifact"]["materialized_records"])
        for row in rows if row["condition_id"] == "full"
    }
    full_records = {
        str(row["prefix_id"]): {str(record["record_id"]): record for record in row["artifact"]["materialized_records"]}
        for row in rows if row["condition_id"] == "full"
    }
    oracles = {
        str(row["query_id"]): row for row in rows if row["condition_id"] == "oracle_failure_chain"
    }
    for row in rows:
        artifact = row["artifact"]
        records = artifact["materialized_records"]
        retrieval = artifact["provenance"]["retrieval_usage"]
        if retrieval.get("read_event_ids"):
            retrieval["observation_tokens"] = sum(
                tokenizer.count(full_records[str(row["prefix_id"])][event_id]["content"])
                for event_id in retrieval["read_event_ids"]
            )
        if row["condition_id"] == "irrelevant_size_control":
            oracle = oracles[str(row["query_id"])]
            target = tokenizer.count(oracle["artifact"]["materialized_records"])
            records = match_irrelevant_tokens(records, target, tokenizer)
        if row["condition_id"] in {"oracle_failure_chain", "irrelevant_size_control"}:
            retrieval.update({
                "matching_basis": "exact_pinned_model_tokens",
                "exact_context_token_match": True,
                "exact_provider_token_match": False,
            })
        token_count = tokenizer.count(records)
        budget = int(artifact["provenance"]["budget_tokens"])
        if row["condition_id"] == "full":
            budget = max(budget, token_count)
        if token_count > budget:
            raise ValueError(f"exact model-token context exceeds the fixed budget: {row['trial_id']}")
        bundle = ContextBundle.create(
            prefix_id=str(row["prefix_id"]), query_id=str(row["query_id"]),
            method_id=str(row["method_id"]), condition_id=str(row["condition_id"]),
            records=records, visible_event_ids=artifact["visible_event_ids"],
            retrieved_event_ids=artifact["retrieved_event_ids"], budget_tokens=budget,
            retrieval_usage=retrieval, token_counter=tokenizer.count,
        )
        artifact.update({
            "context_hash": bundle.context_hash,
            "materialized_records": records,
            "context_tokens": token_count,
            "full_history_tokens": full_counts[str(row["prefix_id"])],
            "compression_ratio": 1 - token_count / max(1, full_counts[str(row["prefix_id"])]),
            "token_accounting": "pinned_model_tokenizer_v1",
            "exact_model_token_count": True,
        })
        artifact["provenance"]["tokenizer"] = tokenizer.provenance
        artifact["provenance"]["budget_tokens"] = budget
        payload = json.loads(row["request_template"]["messages"][1]["content"])
        payload["records"] = records
        row["request_template"]["messages"][1]["content"] = canonical_json(payload)
        row["request_template_sha256"] = stable_digest(row["request_template"])
        row["estimated_input_tokens"] = estimate_tokens(row["request_template"])
        row["tokenized_user_input_tokens"] = tokenizer.count(canonical_json(payload))
    by_query = {
        row["query_id"]: row for row in rows if row["condition_id"] == "oracle_failure_chain"
    }
    for row in rows:
        if row["condition_id"] != "irrelevant_size_control":
            continue
        oracle = by_query[row["query_id"]]
        if row["tokenized_user_input_tokens"] != oracle["tokenized_user_input_tokens"]:
            raise ValueError("oracle/control request text differs in exact model-token length")
    return rows
