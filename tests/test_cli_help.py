from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _decode_console_output(output: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return output.decode(encoding)
        except UnicodeDecodeError:
            continue
    return output.decode("utf-8", errors="replace")


def test_every_command_help_opens_and_has_plain_chinese_description() -> None:
    scripts = sorted(
        path
        for path in (ROOT / "scripts").glob("*.py")
        if "ArgumentParser" in path.read_text(encoding="utf-8")
    )
    commands = [[sys.executable, str(path), "--help"] for path in scripts]
    commands.append([sys.executable, "-m", "tracegraph", "--help"])

    environment = os.environ.copy()
    source_path = str(ROOT / "src")
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (source_path, environment.get("PYTHONPATH", "")) if part
    )

    failures: list[str] = []
    for command in commands:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=environment,
            capture_output=True,
            check=False,
        )
        output = _decode_console_output(result.stdout + result.stderr)
        label = Path(command[1]).name if command[1] != "-m" else "python -m tracegraph"
        if result.returncode != 0:
            failures.append(f"{label} 退出码为 {result.returncode}")
        elif not any("\u4e00" <= character <= "\u9fff" for character in output):
            failures.append(f"{label} 的帮助中没有中文说明")
        elif any(
            phrase in output
            for phrase in ("usage:", "options:", "show this help message and exit")
        ):
            failures.append(f"{label} 的帮助中仍有 Python 自动生成的英文说明")

    assert len(commands) >= 50
    assert not failures, "\n".join(failures)
