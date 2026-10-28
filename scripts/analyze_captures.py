"""Measure what each captured harness puts in front of the model, in RWKV tokens.

Reads captures/<agent>/NNNN.json written by capture_proxy.py and counts, for
the first request of each agent: system prompt, tool definitions, and the
rest of the conversation. That is the fixed cost every task starts from; later
requests are retries against the proxy's canned "Done." reply and only grow.
Tokens come from the local rwkv-tokenizer-go CLI.

Usage (from RWKV-Harbor-Test root):
    python3 scripts/analyze_captures.py captures analysis/harness-prompt-sizes.csv
"""

import csv
import json
import subprocess
import sys
from pathlib import Path

TOKENIZER = Path("third_party/bin/rwkv-tokenizer")


def count_tokens(text: str) -> int:
    if not text:
        return 0
    result = subprocess.run(
        [str(TOKENIZER), "encode"], input=text, capture_output=True, text=True, check=True
    )
    return len(result.stdout.split())


def text_of(content) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(text_of(part) for part in content)
    if isinstance(content, dict):
        for key in ("text", "content", "input_text", "output_text"):
            if key in content:
                return text_of(content[key])
        return json.dumps(content, ensure_ascii=False)
    return str(content)


def split_request(body: dict) -> tuple[str, str, str, int]:
    """Return (system text, tools json, other conversation text, tool count)."""
    tools = body.get("tools") or []
    tools_json = json.dumps(tools, ensure_ascii=False) if tools else ""
    system_parts, other_parts = [], []

    # Anthropic: top-level system; Responses: instructions.
    if body.get("system"):
        system_parts.append(text_of(body["system"]))
    if body.get("instructions"):
        system_parts.append(text_of(body["instructions"]))

    messages = body.get("messages")
    if messages is None:
        messages = body.get("input")
    if isinstance(messages, str):
        other_parts.append(messages)
    elif isinstance(messages, list):
        for message in messages:
            if not isinstance(message, dict):
                other_parts.append(text_of(message))
                continue
            role = message.get("role")
            text = text_of(message.get("content"))
            if role in ("system", "developer"):
                system_parts.append(text)
            else:
                other_parts.append(text)
    return "\n".join(system_parts), tools_json, "\n".join(other_parts), len(tools)


def main() -> None:
    capture_dir, out = Path(sys.argv[1]), Path(sys.argv[2])
    rows = []
    for agent_dir in sorted(p for p in capture_dir.iterdir() if p.is_dir()):
        requests = []
        for path in sorted(agent_dir.glob("*.json")):
            record = json.loads(path.read_text())
            if record["method"] != "POST" or not isinstance(record["body"], dict):
                continue
            requests.append(record["body"])
        if not requests:
            rows.append({"agent": agent_dir.name, "requests": 0})
            continue
        system, tools, other, n_tools = split_request(requests[0])
        s, t, o = count_tokens(system), count_tokens(tools), count_tokens(other)
        total = s + t + o
        rows.append(
            {
                "agent": agent_dir.name,
                "requests": len(requests),
                "tools": n_tools,
                "system_tokens": s,
                "tool_def_tokens": t,
                "conversation_tokens": o,
                "total_tokens": total,
            }
        )
    fields = ["agent", "requests", "tools", "system_tokens", "tool_def_tokens",
              "conversation_tokens", "total_tokens"]
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    for row in sorted(rows, key=lambda r: r.get("total_tokens", 0)):
        print(row)


if __name__ == "__main__":
    main()
