from __future__ import annotations

import json
from pathlib import Path

from tracegraph.cli import main
from tracegraph.graph import TraceGraph


def test_read_only_cli_routes_emit_utf8_json(tmp_path: Path, capsys) -> None:
    graph_path = tmp_path / "中文记录.json"
    TraceGraph("中文会话").save(graph_path)
    assert main(["validate-trace", str(graph_path)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    archive = tmp_path / "归档"
    archive.mkdir()
    assert main(["verify-archive", str(archive)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True

    assert main(["list-managers"]) == 0
    assert "full_ours" in json.loads(capsys.readouterr().out)


def test_synthetic_cli_uses_unique_paths(tmp_path: Path, capsys) -> None:
    output = tmp_path / "生成" / "trace.json"
    archive = tmp_path / "生成" / "archive"
    assert main(
        ["make-synthetic", "--output", str(output), "--archive", str(archive)]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["output"] == str(output)
    assert output.is_file()
