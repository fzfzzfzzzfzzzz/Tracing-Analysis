"""Probe Qwen3.8 thinking plus native bash tool transport before evaluation."""

from __future__ import annotations

import argparse
import json
import urllib.request
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    parser.add_argument("--model", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)

    body = {
        "model": args.model,
        "messages": [{
            "role": "user",
            "content": "Call the bash tool exactly once with command `printf OK`. Do not answer in text.",
        }],
        "tools": [{
            "type": "function",
            "function": {
                "name": "bash",
                "description": "Execute one bash command.",
                "parameters": {
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                    "additionalProperties": False,
                },
            },
        }],
        "tool_choice": "auto",
        "temperature": 1.0,
        "top_p": 0.95,
        "top_k": 20,
        "min_p": 0,
        "max_tokens": 2048,
        "seed": 20260980,
        "stream": False,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    request = urllib.request.Request(
        args.base_url.rstrip("/") + "/chat/completions",
        json.dumps(body).encode(),
        {"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        result = json.load(response)
    if result.get("model") != args.model:
        raise ValueError("unexpected served model identity")
    message = result["choices"][0]["message"]
    reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
    tool_calls = message.get("tool_calls") or []
    if not reasoning.strip():
        raise ValueError("thinking was not returned")
    if len(tool_calls) != 1 or tool_calls[0]["function"]["name"] != "bash":
        raise ValueError("native bash tool call was not parsed")
    arguments = json.loads(tool_calls[0]["function"]["arguments"])
    if not isinstance(arguments.get("command"), str) or not arguments["command"].strip():
        raise ValueError("bash command is empty")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "model": result["model"],
        "reasoning_chars": len(reasoning),
        "tool_name": tool_calls[0]["function"]["name"],
        "command": arguments["command"],
        "usage": result.get("usage"),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
