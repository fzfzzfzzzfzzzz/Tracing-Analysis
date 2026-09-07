"""Official ACON bridge subsystem."""

from __future__ import annotations

# ruff: noqa: F401

import hashlib
import importlib
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, Sequence
from tracegraph.capture import estimate_tokens

from .acon_runtime import (
    AconRuntimeAdapter as AconRuntimeAdapter,
    TauCompressorLLM as TauCompressorLLM,
)

from .acon_types import (
    AconAdapterError as AconAdapterError,
    verify_acon_source as verify_acon_source,
)

def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise AconAdapterError("ACON adapter config must be a JSON object")
    return value


def load_official_acon_adapter(
    *,
    config_path: Path,
    source_root: Path,
    compressor_model_override: str | None = None,
) -> AconRuntimeAdapter:
    """Load hash-pinned official classes and construct a per-session adapter."""

    config = _load_json(config_path)
    source_manifest = config.get("source_manifest")
    if not isinstance(source_manifest, dict):
        raise AconAdapterError("source_manifest is required")
    provenance = verify_acon_source(
        source_root,
        {str(key): str(value) for key, value in source_manifest.items()},
        snapshot_sha=str(config.get("source_snapshot_sha", "")),
        source_repo=str(config.get("source_repo", "")),
    )
    config_bytes = json.dumps(
        config,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    provenance["adapter_config_sha256"] = hashlib.sha256(config_bytes).hexdigest()

    package_root = (source_root.resolve() / "src").resolve()
    package_text = str(package_root)
    if package_text not in sys.path:
        sys.path.insert(0, package_text)
    importlib.invalidate_caches()
    for module_name in ("productive_agents", "productive_agents.ctxopt"):
        existing = sys.modules.get(module_name)
        if existing is not None:
            existing_path = Path(getattr(existing, "__file__", "") or "").resolve()
            if not existing_path.is_relative_to(source_root.resolve()):
                raise AconAdapterError(
                    "productive_agents was already loaded from an unverified path"
                )
    obs_module = importlib.import_module("productive_agents.ctxopt.obs_optimizer")
    history_module = importlib.import_module("productive_agents.ctxopt.history_optimizer")
    for module in (obs_module, history_module):
        module_path = Path(module.__file__ or "").resolve()
        if not module_path.is_relative_to(source_root.resolve()):
            raise AconAdapterError("productive_agents was imported from an unverified path")

    compressor_model = compressor_model_override or str(config.get("compressor_model", ""))
    if not compressor_model:
        raise AconAdapterError("compressor_model is required")
    llm_args = config.get("compressor_llm_args") or {}
    if not isinstance(llm_args, dict):
        raise AconAdapterError("compressor_llm_args must be an object")
    provenance["compressor_model"] = compressor_model
    provenance["official_classes"] = {
        "observation": "productive_agents.ctxopt.obs_optimizer.ObservationOptimizer",
        "history": "productive_agents.ctxopt.history_optimizer.HistoryOptimizer",
    }

    prompt_relative = str(config.get("prompt_dir", ""))
    prompt_dir = (source_root.resolve() / prompt_relative).resolve()
    if not prompt_dir.is_relative_to(source_root.resolve()) or not prompt_dir.is_dir():
        raise AconAdapterError("verified ACON prompt_dir is missing or outside source root")

    observation_config = dict(config.get("observation") or {})
    history_config = dict(config.get("history") or {})
    observation_config.update({"model": compressor_model, "obs_prompt_dir": str(prompt_dir)})
    history_config.update({"model": compressor_model, "history_prompt_dir": str(prompt_dir)})

    observation_llm = TauCompressorLLM(
        model=compressor_model,
        call_name="acon_observation_compressor",
        llm_args=llm_args,
    )
    history_llm = TauCompressorLLM(
        model=compressor_model,
        call_name="acon_history_compressor",
        llm_args=llm_args,
    )
    observation_optimizer = obs_module.ObservationOptimizer(
        observation_config,
        debug_mode=False,
        llm=observation_llm,
    )
    history_optimizer = history_module.HistoryOptimizer(
        history_config,
        debug_mode=False,
        llm=history_llm,
    )
    observation_llm.system_message = observation_optimizer.system_message
    history_llm.system_message = history_optimizer.system_message
    provenance["threshold_tokenizers"] = {
        "observation": getattr(getattr(observation_optimizer, "tokenizer", None), "name", None)
        or "approximate_len_div_4",
        "history": getattr(getattr(history_optimizer, "tokenizer", None), "name", None)
        or "approximate_len_div_4",
    }

    return AconRuntimeAdapter(
        observation_optimizer=observation_optimizer,
        history_optimizer=history_optimizer,
        provenance=provenance,
        preserve_last_k_turns=int(config.get("preserve_last_k_turns", 1)),
        fallback=str(config.get("fallback", "error")),
    )
