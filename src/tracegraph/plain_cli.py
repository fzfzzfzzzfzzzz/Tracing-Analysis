"""把命令帮助和失败原因改成普通话，不改动程序内部抛出的异常。"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable
from typing import Any


USER_FACING_ERRORS = (ValueError, RuntimeError, FileNotFoundError, KeyError, OSError)


class PlainArgumentParser(argparse.ArgumentParser):
    """Show argparse's built-in help and errors in plain Chinese."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._positionals.title = "位置参数"
        self._optionals.title = "可选参数"
        for action in self._actions:
            if isinstance(action, argparse._HelpAction):
                action.help = "显示这份帮助并退出"

    def format_help(self) -> str:
        return super().format_help().replace("usage:", "用法：", 1)

    def format_usage(self) -> str:
        return super().format_usage().replace("usage:", "用法：", 1)

    def error(self, message: str) -> None:
        self.print_usage(sys.stderr)
        self.exit(2, f"{self.prog}: 参数有误：{_plain_argument_error(message)}\n")


def _plain_argument_error(message: str) -> str:
    required = "the following arguments are required:"
    unrecognized = "unrecognized arguments:"
    if message.startswith(required):
        return f"缺少必需参数：{message.removeprefix(required).strip()}"
    if message.startswith(unrecognized):
        return f"无法识别这些参数：{message.removeprefix(unrecognized).strip()}"
    expected = re.fullmatch(r"argument (.+): expected one argument", message)
    if expected:
        return f"参数 {expected.group(1)} 后面需要一个值"
    invalid_value = re.fullmatch(r"argument (.+): invalid .+ value: (.+)", message)
    if invalid_value:
        return f"参数 {invalid_value.group(1)} 的值格式不对：{invalid_value.group(2)}"
    invalid_choice = re.fullmatch(r"argument (.+): invalid choice: (.+)", message)
    if invalid_choice:
        return f"参数 {invalid_choice.group(1)} 使用了不支持的选项：{invalid_choice.group(2)}"
    return "命令参数不符合要求，请查看 --help 后重新输入"


def plain_error_message(error: BaseException) -> str:
    """Return a short Chinese explanation without changing the underlying exception."""

    detail = str(error).strip() or error.__class__.__name__
    lowered = detail.lower()
    if any(word in lowered for word in ("hash", "mismatch", "drift", "digest")):
        reason = "文件或数据与之前固定的版本不一致，请检查输入位置和文件指纹"
    elif any(word in lowered for word in ("missing", "not found", "no such", "absent")):
        reason = "缺少必需的文件或记录，请检查输入位置是否完整"
    elif any(word in lowered for word in ("already exists", "non-empty", "not empty")):
        reason = "目标位置已经有内容，为避免覆盖，程序已经停止"
    elif any(word in lowered for word in ("budget", "cost", "price", "cap", "limit")):
        reason = "允许使用的输入量或费用上限不符合要求，请检查相关参数"
    elif any(word in lowered for word in ("json", "schema", "parse", "decode")):
        reason = "输入文件的格式不符合要求，请检查文件内容"
    elif any(word in lowered for word in ("duplicate", "collision")):
        reason = "输入中出现重复编号或重复记录，请先去重"
    elif any(word in lowered for word in ("unknown", "unsupported", "invalid")):
        reason = "输入中有程序不认识或不支持的内容，请检查参数和值"
    elif any(word in lowered for word in ("provider", "model", "http", "request")):
        reason = "模型服务或请求没有按要求完成，请检查授权、网络和返回内容"
    elif any(word in lowered for word in ("required", "must", "requires")):
        reason = "缺少必须满足的条件，请检查命令参数和输入文件"
    else:
        reason = "输入或保存的文件不符合这条命令的要求"
    return f"运行失败：{reason}。"


def run_cli(main: Callable[[], Any]) -> int:
    """Run a script entrypoint with concise user-facing errors."""

    try:
        result = main()
    except USER_FACING_ERRORS as error:
        print(plain_error_message(error), file=sys.stderr)
        return 2
    return int(result) if isinstance(result, int) else 0
