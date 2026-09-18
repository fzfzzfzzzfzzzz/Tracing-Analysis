from __future__ import annotations

import pytest

from tracegraph.plain_cli import PlainArgumentParser, plain_error_message, run_cli


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (FileNotFoundError("missing input.json"), "缺少必需的文件或记录"),
        (ValueError("budget must be positive"), "输入量或费用上限"),
        (ValueError("manifest hash mismatch"), "文件指纹"),
    ],
)
def test_plain_error_message_explains_error_in_chinese(
    error: Exception, expected: str
) -> None:
    message = plain_error_message(error)
    assert expected in message
    assert "运行失败" in message


def test_run_cli_preserves_direct_function_behavior(capsys: pytest.CaptureFixture[str]) -> None:
    def fail() -> None:
        raise ValueError("invalid input")

    assert run_cli(fail) == 2
    captured = capsys.readouterr()
    assert "运行失败" in captured.err
    assert "程序不认识或不支持" in captured.err


def test_plain_argument_parser_keeps_exit_code_and_uses_chinese(
    capsys: pytest.CaptureFixture[str],
) -> None:
    parser = PlainArgumentParser(prog="example")
    parser.add_argument("--input", required=True)

    with pytest.raises(SystemExit) as raised:
        parser.parse_args([])

    assert raised.value.code == 2
    captured = capsys.readouterr()
    assert "缺少必需参数：--input" in captured.err
    assert "the following arguments are required" not in captured.err
