#!/usr/bin/env python3
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mediabuyerbench.evaluator import load_case, render_prompt, score_response
OUT_ROOT = ROOT / ".runs" / "baselines" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

MODELS = [
    {
        "id": "gpt-5.5",
        "runner": "codex",
        "cmd": lambda prompt, out: [
            "codex", "exec", "-m", "gpt-5.5", "--cd", str(ROOT), "--sandbox", "read-only", "--output-last-message", str(out), "-"
        ],
    },
    {
        "id": "claude-sonnet",
        "runner": "claude-code",
        "cmd": lambda prompt, out: [
            "claude", "--print", "--model", "sonnet", "--disallowedTools", "Bash,Read,Write,Edit,Grep,Glob", "--output-format", "text", prompt
        ],
    },
    {
        "id": "claude-opus",
        "runner": "claude-code",
        "cmd": lambda prompt, out: [
            "claude", "--print", "--model", "opus", "--disallowedTools", "Bash,Read,Write,Edit,Grep,Glob", "--output-format", "text", prompt
        ],
    },
]

PREFIX = """You are being evaluated by MediaBuyerBench. Answer only the benchmark prompt. Do not use tools. Be concise but specific.\n\n"""


def run_command(model: dict[str, Any], prompt: str, output_path: Path) -> tuple[str, str]:
    cmd = model["cmd"](prompt, output_path)
    if model["runner"] == "codex":
        proc = subprocess.run(cmd, input=prompt, text=True, capture_output=True, timeout=300, cwd=ROOT)
        log = proc.stdout + proc.stderr
        if proc.returncode != 0:
            raise RuntimeError(f"{model['id']} failed: {log[-2000:]}")
        text = output_path.read_text(encoding="utf-8")
        return text, log
    proc = subprocess.run(cmd, text=True, capture_output=True, timeout=300, cwd=ROOT)
    log = proc.stderr
    if proc.returncode != 0:
        raise RuntimeError(f"{model['id']} failed: {proc.stdout[-1000:]}\n{proc.stderr[-2000:]}")
    output_path.write_text(proc.stdout, encoding="utf-8")
    return proc.stdout, log


def main() -> int:
    case_paths = sorted((ROOT / "cases" / "public_lite").rglob("*.json"))
    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    all_scores = []

    for model in MODELS:
        model_dir = OUT_ROOT / model["id"]
        model_dir.mkdir(parents=True, exist_ok=True)
        for case_path in case_paths:
            case = load_case(case_path)
            prompt = PREFIX + render_prompt(case)
            out_file = model_dir / f"{case['id']}.md"
            log_file = model_dir / f"{case['id']}.log"
            print(f"Running {model['id']} on {case['id']}...", flush=True)
            response, log = run_command(model, prompt, out_file)
            log_file.write_text(log, encoding="utf-8")
            score = score_response(case, response)
            score.update({
                "model": model["id"],
                "runner": model["runner"],
                "response_path": str(out_file.relative_to(ROOT)),
            })
            score_file = model_dir / f"{case['id']}.score.json"
            score_file.write_text(json.dumps(score, indent=2), encoding="utf-8")
            all_scores.append(score)
            print(f"  score={score['overall_score']}", flush=True)

    summary_path = OUT_ROOT / "summary.json"
    summary_path.write_text(json.dumps({"scores": all_scores}, indent=2), encoding="utf-8")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
