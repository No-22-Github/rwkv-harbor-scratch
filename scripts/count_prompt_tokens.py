"""Count RWKV tokens of the first Terminus prompt for every task in a dataset.

Usage (from RWKV-Harbor-Test root):
    RWKV_API_BASE=http://<host>:8000/v1 PYTHONPATH=. uv run --project harbor \
        python scripts/count_prompt_tokens.py datasets/terminal-bench-2 analysis/tb2-prompt-lengths.csv
"""

import csv
import os
import sys
import tomllib
from pathlib import Path

import httpx

from rwkv_harbor.llm import render_prompt

API = os.environ["RWKV_API_BASE"].rstrip("/")
TEMPLATE = Path(
    "harbor/src/harbor/agents/terminus_2/templates/terminus-json-plain.txt"
).read_text()
# Terminus sends the tmux pane on the first turn; a fresh shell is one prompt line.
TERMINAL_STATE = "root@0123456789ab:/app#"


def count(client: httpx.Client, text: str) -> int:
    response = client.post(f"{API}/tokens/count", json={"text": text})
    response.raise_for_status()
    return int(response.json()["tokens"])


def main() -> None:
    dataset, out = Path(sys.argv[1]), Path(sys.argv[2])
    rows = []
    with httpx.Client(timeout=60) as client:
        template_only = render_prompt(
            [
                {
                    "role": "user",
                    "content": TEMPLATE.format(
                        instruction="", terminal_state=TERMINAL_STATE
                    ),
                }
            ]
        )
        template_tokens = count(client, template_only)
        for task_dir in sorted(p for p in dataset.iterdir() if p.is_dir()):
            instruction_path = task_dir / "instruction.md"
            if not instruction_path.exists():
                continue
            instruction = instruction_path.read_text()
            config = tomllib.loads((task_dir / "task.toml").read_text())
            prompt = render_prompt(
                [
                    {
                        "role": "user",
                        "content": TEMPLATE.format(
                            instruction=instruction, terminal_state=TERMINAL_STATE
                        ),
                    }
                ]
            )
            rows.append(
                {
                    "task": task_dir.name,
                    "difficulty": config.get("metadata", {}).get("difficulty", ""),
                    "category": config.get("metadata", {}).get("category", ""),
                    "agent_timeout_sec": config.get("agent", {}).get("timeout_sec", ""),
                    "instruction_chars": len(instruction),
                    "instruction_tokens": count(client, instruction),
                    "first_prompt_tokens": count(client, prompt),
                }
            )
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"template_tokens={template_tokens} tasks={len(rows)}")


if __name__ == "__main__":
    main()
