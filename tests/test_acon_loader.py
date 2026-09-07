from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tracegraph.context_engine import acon_loader
from tracegraph.context_engine.acon_types import AconAdapterError


class _Optimizer:
    def __init__(self, config, *, debug_mode, llm) -> None:
        self.config = config
        self.debug_mode = debug_mode
        self.llm = llm
        self.system_message = "compress safely"
        self.tokenizer = SimpleNamespace(name="fixture-tokenizer")


def test_load_official_acon_adapter_from_verified_local_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "official"
    (source / "src").mkdir(parents=True)
    (source / "prompts").mkdir()
    module_path = source / "src" / "productive_agents" / "ctxopt" / "fixture.py"
    module = SimpleNamespace(__file__=str(module_path))
    module.ObservationOptimizer = _Optimizer
    module.HistoryOptimizer = _Optimizer
    monkeypatch.setattr(
        acon_loader,
        "verify_acon_source",
        lambda *args, **kwargs: {"source_verified": True},
    )
    monkeypatch.setattr(acon_loader.importlib, "import_module", lambda _: module)
    config = tmp_path / "acon.json"
    config.write_text(
        json.dumps(
            {
                "source_manifest": {"fixture.py": "00" * 32},
                "source_snapshot_sha": "fixture",
                "source_repo": "local",
                "compressor_model": "offline-fixture",
                "compressor_llm_args": {},
                "prompt_dir": "prompts",
                "observation": {"threshold": 4},
                "history": {"threshold": 8},
                "preserve_last_k_turns": 2,
                "fallback": "error",
            }
        ),
        encoding="utf-8",
    )
    adapter = acon_loader.load_official_acon_adapter(
        config_path=config,
        source_root=source,
        compressor_model_override="offline-override",
    )
    assert adapter.provenance["source_verified"] is True
    assert adapter.provenance["compressor_model"] == "offline-override"
    assert adapter.provenance["threshold_tokenizers"] == {
        "observation": "fixture-tokenizer",
        "history": "fixture-tokenizer",
    }
    assert adapter.preserve_last_k_turns == 2


def test_acon_loader_rejects_non_object_config_and_missing_manifest(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[]", encoding="utf-8")
    with pytest.raises(AconAdapterError, match="JSON object"):
        acon_loader.load_official_acon_adapter(config_path=bad, source_root=tmp_path)
    bad.write_text("{}", encoding="utf-8")
    with pytest.raises(AconAdapterError, match="source_manifest"):
        acon_loader.load_official_acon_adapter(config_path=bad, source_root=tmp_path)
