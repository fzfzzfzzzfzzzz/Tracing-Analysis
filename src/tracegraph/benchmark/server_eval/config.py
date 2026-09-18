"""Configuration and local-only tokenizer validation for server experiments."""

from __future__ import annotations

import json
import re
from pathlib import Path

from ..compression_audit.io import canonical_json, file_sha256
from ..compression_audit.development_protocol import (
    submission_response_format, submission_vllm_085_response_format,
)
from .ablations import ABLATIONS
from .candidate import NAME as CANDIDATE
from .lifecycle_retrieval import METHODS as TRACEGRAPH_MEMORY_METHODS

METHODS = (
    "full_history",
    "recent_masking",
    "flat_bm25_archive",
    "bm25_pair_window_archive",
    "bm25_episode_archive",
    "tracegraph_0_4",
    "rolling_summary",
    "acon_official",
    "ama_official_bm25",
    "ama_official_embedding",
    *TRACEGRAPH_MEMORY_METHODS,
    *ABLATIONS,
    CANDIDATE,
)
GATES = {"judge_holdout_correct": 36, "judge_critical_false_positives": 0,
         "first_format_valid": 36, "final_format_valid": 38, "full_pass": 20, "oracle_pass": 14}


def answer_response_format(model: dict) -> dict:
    """Select a frozen server-compatible wire schema without weakening parsing."""

    if model.get("server_version") == "0.8.5":
        return submission_vllm_085_response_format()
    return submission_response_format()


def answer_transport_flags(model: dict) -> tuple[bool, bool]:
    """Return (typed_fields, native_tool) for the frozen answer wire protocol."""

    native_tool = model.get("answer_transport") == "native_tool_call"
    # Native tool arguments carry the typed a/e/t/s fields on every supported
    # server.  The vLLM 0.8.5 compatibility path also uses typed fields even
    # when its transport is guided JSON.
    return model.get("server_version") == "0.8.5" or native_tool, native_tool


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text(encoding="utf-8"))
    if config["schema_version"] not in ("server_eval_v1", "server_eval_v2"):
        raise ValueError("unsupported server evaluation configuration")
    if config["gates"] != GATES:
        raise ValueError("calibration gates must be frozen before experimentation")
    if config.get("development_only") is not True or config.get("independent_validation") is not False:
        raise ValueError("this suite cannot certify independent validation")
    if not config["methods"] or len(set(config["methods"])) != len(config["methods"]):
        raise ValueError("methods must be unique and nonempty")
    if set(config["methods"]) - set(METHODS):
        raise ValueError("unknown method; reference stand-ins are forbidden")
    for key in ("history_budgets",):
        values = config[key]
        if not values or len(set(values)) != len(values) or any(
                type(v) is not int or v < 256 or v % 2 for v in values):
            raise ValueError("history budgets must be unique even integers >=256")
    models = config["models"]
    if not models or len({m["id"] for m in models}) != len(models):
        raise ValueError("model slots must be unique")
    for model in models:
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", model["id"]):
            raise ValueError("unsafe model slot ID")
        if model["provider"] != "self_hosted_openai":
            raise ValueError("server suite supports self-hosted endpoints only; no paid fallback")
        if model["max_output_tokens"] != 2048 or model["context_window"] <= 2048:
            raise ValueError("invalid model context/output limits")
        if model["thinking_transport"] not in ("chat_template_kwargs", "enable_thinking", "none"):
            raise ValueError("unknown thinking transport")
        if type(model.get("enable_thinking", False)) is not bool:
            raise ValueError("enable_thinking must be boolean")
        if model.get("enable_thinking", False) and model["thinking_transport"] == "none":
            raise ValueError("enabled thinking requires an explicit transport")
        thinking_kinds = model.get("thinking_kinds")
        if (thinking_kinds is not None and (
                not isinstance(thinking_kinds, list)
                or not thinking_kinds
                or len(thinking_kinds) != len(set(thinking_kinds))
                or any(not isinstance(kind, str) or not kind for kind in thinking_kinds))):
            raise ValueError("thinking_kinds must be unique nonempty strings")
        if thinking_kinds is not None and not model.get("enable_thinking", False):
            raise ValueError("thinking_kinds requires enabled thinking")
        sampling = model.get("thinking_sampling")
        if sampling is not None:
            if (not isinstance(sampling, dict)
                    or set(sampling) != {"temperature", "top_p", "top_k", "min_p"}
                    or type(sampling["temperature"]) not in (int, float)
                    or not 0 < sampling["temperature"] <= 2
                    or type(sampling["top_p"]) not in (int, float)
                    or not 0 < sampling["top_p"] <= 1
                    or type(sampling["top_k"]) is not int or sampling["top_k"] <= 0
                    or type(sampling["min_p"]) not in (int, float)
                    or not 0 <= sampling["min_p"] <= 1):
                raise ValueError("invalid thinking sampling parameters")
            if not model.get("enable_thinking", False):
                raise ValueError("thinking_sampling requires enabled thinking")
        if model.get("action_transport", "json_schema") not in (
                "json_schema", "native_tool_call"):
            raise ValueError("unknown mini action transport")
        if model.get("construction_transport", "text") not in (
                "text", "native_tool_call"):
            raise ValueError("unknown construction transport")
        if model.get("retrieval_transport", "text") not in (
                "text", "native_tool_call"):
            raise ValueError("unknown retrieval transport")
        construction_output = model.get(
            "construction_max_output_tokens", model["max_output_tokens"]
        )
        retrieval_output = model.get(
            "retrieval_max_output_tokens", model["max_output_tokens"]
        )
        if (type(construction_output) is not int
                or not model["max_output_tokens"] <= construction_output <= 8192
                or construction_output >= model["context_window"]):
            raise ValueError("invalid construction output limit")
        if (type(retrieval_output) is not int
                or not model["max_output_tokens"] <= retrieval_output <= 8192
                or retrieval_output >= model["context_window"]):
            raise ValueError("invalid retrieval output limit")
        construction_chars = model.get("construction_submission_max_chars")
        if (construction_chars is not None and (
                type(construction_chars) is not int
                or not 1024 <= construction_chars <= 65536)):
            raise ValueError("invalid construction submission character limit")
        retrieval_chars = model.get("retrieval_submission_max_chars")
        if (retrieval_chars is not None and (
                type(retrieval_chars) is not int
                or not 1024 <= retrieval_chars <= 65536)):
            raise ValueError("invalid retrieval submission character limit")
        answer_transport = model.get("answer_transport", "response_format")
        if answer_transport not in ("response_format", "native_tool_call"):
            raise ValueError("unknown audit answer transport")
        answer_thinking = model.get("enable_thinking", False) and (
            thinking_kinds is None or "answer" in thinking_kinds
        )
        if answer_thinking and answer_transport != "native_tool_call":
            raise ValueError(
                "thinking audit answers require native tool transport; guided JSON suppresses thinking"
            )
    if config["judge_model_id"] not in {m["id"] for m in models}:
        raise ValueError("judge must name a configured model")
    code_search = config["ama_code_search"]
    if code_search.get("mode") not in ("disabled", "docker", "bwrap"):
        raise ValueError("unknown AMA code-search sandbox")
    if code_search["mode"] == "docker":
        image = code_search.get("image")
        if (not isinstance(image, str) or "@sha256:" not in image
                or len(image.rsplit("@sha256:", 1)[1]) != 64):
            raise ValueError("AMA Docker search requires a digest-pinned image")
    elif code_search.get("image") is not None:
        raise ValueError("AMA non-Docker search must not configure an image")
    for key in ("request_limit", "input_token_limit", "output_token_limit", "wall_seconds",
                "build_calls_per_prefix", "retrieve_calls_per_query", "timeout_seconds"):
        if type(config["limits"][key]) is not int or config["limits"][key] <= 0:
            raise ValueError(f"invalid limit: {key}")
    if config["limits"]["timeout_seconds"] > 300:
        raise ValueError("request timeout must not exceed 300 seconds")
    if config["data"]["split"] not in ("dev", "validation", "test"):
        raise ValueError("unknown data split")
    calibration_only = config["data"].get("calibration_only", False)
    if type(calibration_only) is not bool:
        raise ValueError("calibration_only must be boolean")
    if calibration_only and config["data"]["split"] != "dev":
        raise ValueError("calibration_only is restricted to the development split")
    exclusions = config["data"].get("calibration_exclude_prefix_ids", [])
    if (not isinstance(exclusions, list) or any(not isinstance(value, str) or not value
                                               for value in exclusions)
            or len(exclusions) != len(set(exclusions))):
        raise ValueError("calibration exclusions must be unique nonempty prefix IDs")
    calibration_prefix_ids = config["data"].get("calibration_prefix_ids", [])
    if (not isinstance(calibration_prefix_ids, list)
            or any(not isinstance(value, str) or not value
                   for value in calibration_prefix_ids)
            or len(calibration_prefix_ids) != len(set(calibration_prefix_ids))):
        raise ValueError("calibration prefix IDs must be unique nonempty strings")
    if calibration_prefix_ids and len(calibration_prefix_ids) != 8:
        raise ValueError("an explicit calibration population must contain exactly 8 prefixes")
    prefix_ids = config["data"].get("prefix_ids", [])
    if (not isinstance(prefix_ids, list) or any(not isinstance(value, str) or not value
                                                for value in prefix_ids)
            or len(prefix_ids) != len(set(prefix_ids))):
        raise ValueError("prefix IDs must be unique nonempty strings")
    capability_only = config.get("capability_attribution_only", False)
    if type(capability_only) is not bool:
        raise ValueError("capability_attribution_only must be boolean")
    oracle_gate_only = config.get("oracle_gate_only", False)
    if type(oracle_gate_only) is not bool:
        raise ValueError("oracle_gate_only must be boolean")
    if oracle_gate_only and not capability_only:
        raise ValueError("oracle_gate_only requires capability_attribution_only")
    oracle_context_mode = config.get("oracle_context_mode", "recent_plus_gold")
    if oracle_context_mode not in ("recent_plus_gold", "evidence_only"):
        raise ValueError("unknown oracle context mode")
    if oracle_context_mode == "evidence_only" and not oracle_gate_only:
        raise ValueError("evidence-only Oracle requires oracle_gate_only")
    if capability_only and (
        config["methods"] != ["full_history"]
        or config.get("interactive") is not False
        or not prefix_ids
    ):
        raise ValueError(
            "capability attribution requires an explicit development subset, "
            "full_history only, and no interaction"
        )
    return config


class LocalTokenizer:
    """Exact local context and chat-template counts, never download or execute remote code.

    The server must be launched with the same files/template. Actual usage is still
    recorded separately; discrepancies stop the run instead of being called exact.
    """

    def __init__(self, model: dict, workspace: Path):
        spec = model.get("tokenizer")
        if not spec or not model.get("served_model") or not model.get("weights_revision"):
            raise ValueError("model weights and tokenizer must be configured on the server")
        root = (workspace / spec["path"]).resolve()
        files = spec["files"]
        if not {"tokenizer.json", "tokenizer_config.json"} <= files.keys():
            raise ValueError("pin tokenizer.json and tokenizer_config.json")
        if not any("chat_template" in p for p in files) and not json.loads(
                (root / "tokenizer_config.json").read_text(encoding="utf-8")).get("chat_template"):
            raise ValueError("local chat template must be pinned")
        # Validate every tokenizer-side file, including model config if present.
        actual = {p.relative_to(root).as_posix(): file_sha256(p) for p in root.rglob("*")
                  if p.is_file()}
        if actual != files:
            raise ValueError("tokenizer directory differs from its complete frozen file manifest")
        from transformers import AutoTokenizer
        self.tokenizer = AutoTokenizer.from_pretrained(str(root), local_files_only=True,
                                                       trust_remote_code=False)
        self.model = model

    def count(self, value) -> int:
        text = value if isinstance(value, str) else canonical_json(value)
        return len(self.tokenizer.encode(text, add_special_tokens=False))

    def request_count(self, body: dict) -> int:
        # Transformers 5 returns a BatchEncoding by default, whose len() is the
        # number of fields rather than the number of token IDs.  Keep the return
        # type explicit so the frozen ceiling remains comparable with vLLM usage.
        template_thinking = body.get("chat_template_kwargs", {}).get(
            "enable_thinking", body.get("enable_thinking", False))
        kwargs = {"tokenize": True, "add_generation_prompt": True,
                  "enable_thinking": template_thinking, "return_dict": False}
        if body.get("tools"):
            kwargs["tools"] = body["tools"]
        tokens = self.tokenizer.apply_chat_template(body["messages"], **kwargs)
        # Grammar/tool-choice transport may add server overhead. Count it conservatively
        # as well; provider usage is checked against this ceiling on every response.
        extra = {k: body[k] for k in ("response_format", "tool_choice") if k in body}
        return len(tokens) + (self.count(extra) if extra else 0) + 256


def live_blockers(config: dict, workspace: Path) -> list[str]:
    blockers = []
    for model in config["models"]:
        if config["schema_version"] == "server_eval_v2":
            from .runtime import verify_runtime
            try:
                verify_runtime(model, workspace)
            except (ValueError, OSError, KeyError) as exc:
                blockers.append(f"{model['id']}:runtime_not_frozen:{type(exc).__name__}")
        for key in ("base_url", "served_model", "weights_revision", "server_version"):
            if not model.get(key):
                blockers.append(f"{model['id']}:{key}_missing")
        try:
            LocalTokenizer(model, workspace)
        except (ValueError, OSError, ImportError, KeyError) as exc:
            blockers.append(f"{model['id']}:tokenizer_not_ready:{type(exc).__name__}")
    if "ama_official_embedding" in config["methods"]:
        try:
            spec = config["embedding"]
            EmbeddingTokenizer(spec, workspace)
            if not spec.get("base_url") or not spec.get("dimension") or not spec.get("returned_model_allowlist"):
                raise ValueError("embedding service incomplete")
        except (ValueError, OSError, ImportError, KeyError, TypeError):
            blockers.append("embedding_service_not_ready")
    return blockers


class EmbeddingTokenizer:
    def __init__(self, model, workspace):
        spec = model["tokenizer"]
        if not model["weights_revision"]:
            raise ValueError("embedding revision missing")
        root = workspace / spec["path"]
        if {p.relative_to(root).as_posix(): file_sha256(p) for p in root.rglob("*") if p.is_file()} != spec["files"]:
            raise ValueError("embedding tokenizer differs")
        from tokenizers import Tokenizer
        self.tokenizer = Tokenizer.from_file(str(root / "tokenizer.json"))

    def count(self, value):
        return len(self.tokenizer.encode(value).ids)
