"""Per-task statistics for a RwkvTerminus job, from the rwkv-calls logs.

Usage (from RWKV-Harbor-Test root):
    python3 scripts/summarize_run.py jobs/rwkv-tb2-sample/<timestamp> [max_output_tokens]
"""

import json
import re
import sys
from pathlib import Path


def summarize_trial(trial: Path, max_output: int) -> dict:
    calls = [
        json.loads(p.read_text())
        for p in sorted((trial / "agent" / "rwkv-calls").glob("call-*.json"))
    ]
    outputs = [c["raw_output"] for c in calls]
    hit_max = sum(
        1 for c in calls if not c["matched_stop"] and c["completion_tokens"] >= max_output
    )
    repeats = sum(1 for a, b in zip(outputs, outputs[1:]) if a.strip() == b.strip())
    log = (trial / "trial.log").read_text() if (trial / "trial.log").exists() else ""
    exception = trial / "exception.txt"
    ending = "自报完成"
    if exception.exists():
        text = exception.read_text()
        ending = "超时" if "AgentTimeoutError" in text else (
            "推理端 502" if " 502 " in text else "异常"
        )
    stdout = trial / "verifier" / "test-stdout.txt"
    tests = "—"
    if stdout.exists():
        text = stdout.read_text()
        if "404  Not Found" in text:
            tests = "判分环境损坏"
        else:
            passed = sum(int(n) for n in re.findall(r"(\d+) passed", text)[-1:])
            failed = sum(int(n) for n in re.findall(r"(\d+) failed", text)[-1:])
            tests = f"{passed}/{passed + failed}"
    reward_path = trial / "verifier" / "reward.txt"
    return {
        "task": trial.name.split("__")[0],
        "reward": reward_path.read_text().strip() if reward_path.exists() else "—",
        "ending": ending,
        "turns": len(calls),
        "max_context_k": round(max((c["prompt_tokens"] for c in calls), default=0) / 1000, 1),
        "summaries": log.count("Proactively summarizing"),
        "think_pct": round(100 * sum(o.lstrip().startswith("<think>") for o in outputs) / max(len(outputs), 1)),
        "repeat_pct": round(100 * repeats / max(len(outputs) - 1, 1)),
        "hit_max": hit_max,
        "completion_tokens": sum(c["completion_tokens"] for c in calls),
        "hit_max_tokens": sum(
            c["completion_tokens"]
            for c in calls
            if not c["matched_stop"] and c["completion_tokens"] >= max_output
        ),
        "gen_sec": round(sum(c["elapsed_sec"] for c in calls)),
        "tests": tests,
    }


def main() -> None:
    job = Path(sys.argv[1])
    max_output = int(sys.argv[2]) if len(sys.argv) > 2 else 2048
    rows = [
        summarize_trial(t, max_output)
        for t in sorted(job.iterdir())
        if t.is_dir() and (t / "agent").exists()
    ]
    print(json.dumps(rows, ensure_ascii=False, indent=1))
    turns = sum(r["turns"] for r in rows)
    print(
        f"turns={turns} hit_max={sum(r['hit_max'] for r in rows)} "
        f"hit_max_tokens={sum(r['hit_max_tokens'] for r in rows)} "
        f"completion_tokens={sum(r['completion_tokens'] for r in rows)}"
    )


if __name__ == "__main__":
    main()
