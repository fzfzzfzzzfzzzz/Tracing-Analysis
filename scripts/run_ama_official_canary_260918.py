"""Run the frozen AMA-Bench canary through the pinned official QA interface.

This is a transport/provenance wrapper, not a replacement benchmark.  Prompt
construction, long-context truncation, batching, and answer parsing come from
the pinned AMA-Hub implementation.  The wrapper adds exact split checks,
OpenAI-compatible Qwen transport, per-call usage receipts, and crash-safe
episode outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import requests
import yaml
from openai import OpenAI


ROOT = Path(__file__).resolve().parents[1]
AMA_ROOT = ROOT / "vendor" / "ama-hub-ddfd319"
DATASET = (
    ROOT
    / "data"
    / "external"
    / "compression_audit_sources_v1"
    / "ama_bench_a577737"
    / "open_end_qa_set.jsonl"
)
FREEZE = (
    ROOT
    / "data"
    / "external_benchmark_freezes"
    / "ama_bench_260917"
    / "canary_episode_ids.jsonl"
)
CONFIG = ROOT / "configs" / "ama_qwen38_27b_full_history_canary_260918.yaml"
DEFAULT_OUTPUT = ROOT / "outputs" / "ama_external_canary_260918" / "full_history_qwen38_v3"

AMA_COMMIT = "ddfd319e0be33424288c13806f1eafc63e625b59"
DATASET_SHA256 = "45c36052e1520d87ad9de4114f71c9df42d4aac9cf158c0c353e800b653d65ff"
FREEZE_SHA256 = "e6888a0f863146f6743a504b06b2919fa812e60be791a6202acd0b411c50d655"
TOKENIZER_SHA256 = "0997f410c57a1f4e53b09e4be8f4a172d90edd9564368fb0847030937229b9f3"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def atomic_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def append_jsonl(path: Path, value: Any) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(stable_json(value) + "\n")
        handle.flush()


class UsageLoggingClient:
    """AMA-compatible client that preserves provider usage and raw responses."""

    def __init__(self, config: dict[str, Any], output: Path):
        self.provider = "custom"
        self.model = str(config["served_model"])
        self.config = dict(config)
        self.output = output
        self.calls_dir = output / "provider_calls"
        self.calls_dir.mkdir(parents=True, exist_ok=True)
        self.client = OpenAI(
            base_url=str(config["base_url"]),
            api_key=str(config.get("api_key", "EMPTY")),
            timeout=float(config.get("timeout", 900)),
        )
        self.sampling = dict(config["sampling"])
        self._lock = threading.Lock()
        self._call_index = len(list(self.calls_dir.glob("*.json")))

    def _next_index(self) -> int:
        with self._lock:
            self._call_index += 1
            return self._call_index

    def query(
        self,
        prompt: str,
        temperature: float = 0.0,
        max_tokens: int = 4096,
        max_retries: int = 3,
        system: str | None = None,
    ) -> str:
        # The provider adapter consistently applies the benchmark-specific
        # frozen sampling value and preserves AMA's requested value in receipts.
        expected_temperature = float(self.sampling["temperature"])
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        call_index = self._next_index()
        seed = int(self.sampling["seed"]) ^ int(prompt_hash[:8], 16)
        extra_body = {
            "top_k": int(self.sampling["top_k"]),
            "min_p": float(self.sampling["min_p"]),
            "chat_template_kwargs": {
                "enable_thinking": bool(self.sampling["enable_thinking"]),
            },
        }
        if "min_tokens" in self.sampling:
            extra_body["min_tokens"] = int(self.sampling["min_tokens"])
        request_record = {
            "model": self.model,
            "messages": messages,
            "temperature": expected_temperature,
            "interface_requested_temperature": float(temperature),
            "top_p": float(self.sampling["top_p"]),
            "max_tokens": int(max_tokens),
            "seed": seed,
            "extra_body": extra_body,
            "prompt_sha256": prompt_hash,
            "prompt_characters": len(prompt),
        }
        attempts = []
        for attempt in range(1, max_retries + 1):
            started = time.perf_counter()
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=expected_temperature,
                    top_p=float(self.sampling["top_p"]),
                    max_tokens=int(max_tokens),
                    seed=seed + attempt - 1,
                    extra_body=request_record["extra_body"],
                )
                raw = response.model_dump(mode="json")
                content = response.choices[0].message.content or ""
                attempt_record = {
                    "attempt": attempt,
                    "duration_seconds": time.perf_counter() - started,
                    "response": raw,
                }
                attempts.append(attempt_record)
                if not content.strip():
                    raise ValueError("provider returned empty visible content")
                receipt = {
                    "schema_version": "ama_provider_call_v1",
                    "call_index": call_index,
                    "request": request_record,
                    "attempts": attempts,
                    "selected_attempt": attempt,
                }
                atomic_json(
                    self.calls_dir / f"call_{call_index:04d}_{prompt_hash[:12]}.json",
                    receipt,
                )
                return content.strip()
            except Exception as exc:
                if attempts and attempts[-1].get("attempt") == attempt:
                    attempts[-1]["error_type"] = type(exc).__name__
                    attempts[-1]["error"] = str(exc)
                else:
                    attempts.append(
                        {
                            "attempt": attempt,
                            "duration_seconds": time.perf_counter() - started,
                            "error_type": type(exc).__name__,
                            "error": str(exc),
                        }
                    )
                if attempt >= max_retries:
                    atomic_json(
                        self.calls_dir / f"call_{call_index:04d}_{prompt_hash[:12]}.json",
                        {
                            "schema_version": "ama_provider_call_v1",
                            "call_index": call_index,
                            "request": request_record,
                            "attempts": attempts,
                            "selected_attempt": None,
                        },
                    )
                    raise
                time.sleep(min(2**attempt, 10))
        raise AssertionError("unreachable")


def verify_inputs(
    config: dict[str, Any],
    *,
    freeze: Path = FREEZE,
    freeze_sha256: str = FREEZE_SHA256,
    expected_count: int = 5,
) -> list[dict[str, str]]:
    if sha256_file(DATASET) != DATASET_SHA256:
        raise ValueError("AMA dataset hash changed")
    if sha256_file(freeze) != freeze_sha256:
        raise ValueError("AMA freeze hash changed")
    tokenizer = ROOT / str(config["tokenizer_path"]) / "tokenizer.json"
    if sha256_file(tokenizer) != TOKENIZER_SHA256:
        raise ValueError("Qwen tokenizer hash changed")
    commit = subprocess.check_output(
        ["git", "-C", str(AMA_ROOT), "rev-parse", "HEAD"], text=True
    ).strip()
    if commit != AMA_COMMIT:
        raise ValueError(f"AMA source commit changed: {commit}")
    frozen = [json.loads(line) for line in freeze.read_text(encoding="utf-8").splitlines()]
    if (
        len(frozen) != expected_count
        or len({str(row["episode_id"]) for row in frozen}) != expected_count
    ):
        raise ValueError(f"AMA freeze must contain {expected_count} unique episodes")
    models = requests.get(str(config["base_url"]).rstrip("/") + "/models", timeout=15)
    models.raise_for_status()
    model_ids = {str(row["id"]) for row in models.json().get("data", [])}
    if str(config["served_model"]) not in model_ids:
        raise ValueError("Qwen3.8-27B endpoint is not ready")
    return frozen


def load_canary(frozen: list[dict[str, str]]) -> list[dict[str, Any]]:
    wanted = {str(row["episode_id"]): row for row in frozen}
    selected = {}
    with DATASET.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            episode_id = str(row.get("episode_id"))
            if episode_id in wanted:
                if row.get("task_type") != wanted[episode_id]["task_type"]:
                    raise ValueError(f"task type mismatch for episode {episode_id}")
                selected[episode_id] = row
    missing = set(wanted) - set(selected)
    if missing:
        raise ValueError(f"missing frozen AMA episodes: {sorted(missing)}")
    return [selected[str(row["episode_id"])] for row in frozen]


def aggregate_usage(calls_dir: Path) -> dict[str, int]:
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0}
    for path in calls_dir.glob("*.json"):
        row = json.loads(path.read_text(encoding="utf-8"))
        for attempt in row.get("attempts", []):
            usage = (attempt.get("response") or {}).get("usage") or {}
            totals["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            totals["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            details = usage.get("completion_tokens_details") or {}
            totals["reasoning_tokens"] += int(
                usage.get("reasoning_tokens") or details.get("reasoning_tokens") or 0
            )
    totals["total_tokens"] = totals["prompt_tokens"] + totals["completion_tokens"]
    return totals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    config = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    frozen = verify_inputs(config)
    episodes = load_canary(frozen)
    if args.dry_run:
        print(
            stable_json(
                {
                    "status": "ready",
                    "episode_ids": [str(row["episode_id"]) for row in episodes],
                    "qa_pairs": sum(len(row.get("qa_pairs", [])) for row in episodes),
                    "source_commit": AMA_COMMIT,
                }
            )
        )
        return

    output = args.output.resolve()
    if output.exists() != args.resume:
        raise ValueError("use a new output directory, or pass --resume for an existing run")
    output.mkdir(parents=True, exist_ok=True)
    answers_path = output / "answers.jsonl"
    completed = set()
    if answers_path.exists():
        completed = {
            str(json.loads(line)["episode_id"])
            for line in answers_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        }

    manifest = {
        "schema_version": "ama_external_canary_run_v1",
        "status": "running",
        "benchmark": "AMA-Bench open-end QA",
        "method": "official_longcontext",
        "source_commit": AMA_COMMIT,
        "dataset_sha256": DATASET_SHA256,
        "freeze_sha256": FREEZE_SHA256,
        "config_sha256": sha256_file(CONFIG),
        "tokenizer_sha256": TOKENIZER_SHA256,
        "episode_ids": [str(row["episode_id"]) for row in episodes],
        "development_only": True,
        "gold_answers_sent_to_model": False,
        "confirmation_pool_sent_to_model": False,
    }
    atomic_json(output / "run_manifest.json", manifest)

    sys.path.insert(0, str(AMA_ROOT))
    from src.memory_interface import MemoryQAInterface  # noqa: PLC0415

    client = UsageLoggingClient(config, output)
    interface = MemoryQAInterface(
        client=client,
        method_name="longcontext",
        method_config=str(CONFIG),
        max_concurrency_episodes=1,
        max_concurrency_questions=1,
        subset="openend",
    )
    for index, episode in enumerate(episodes, 1):
        episode_id = str(episode["episode_id"])
        if episode_id in completed:
            continue
        print(f"AMA canary {index}/5: episode {episode_id}", flush=True)
        result = interface.process_episode(episode)
        qa_pairs = episode.get("qa_pairs", [])
        if len(result["answer_list"]) != len(qa_pairs):
            raise ValueError(f"answer count mismatch for episode {episode_id}")
        append_jsonl(
            answers_path,
            {
                "episode_id": episode_id,
                "task_type": episode.get("task_type"),
                "answers": [
                    {
                        "question_uuid": pair.get("question_uuid"),
                        "qa_type": pair.get("type"),
                        "predicted_answer": answer,
                    }
                    for pair, answer in zip(qa_pairs, result["answer_list"], strict=True)
                ],
            },
        )

    manifest["status"] = "completed"
    manifest["usage"] = aggregate_usage(output / "provider_calls")
    manifest["completed_episode_count"] = len(episodes)
    manifest["completed_question_count"] = sum(
        len(row.get("qa_pairs", [])) for row in episodes
    )
    atomic_json(output / "run_manifest.json", manifest)
    print(stable_json({"status": "completed", "usage": manifest["usage"]}))


if __name__ == "__main__":
    main()
