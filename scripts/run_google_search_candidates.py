#!/usr/bin/env python3
"""Generate no-tools candidate answers for a frozen Google Search suite.

This runner never exposes the source pack, frozen reference decisions, or
previous scores to candidate models. It records elapsed wall time so candidate
runs can be compared alongside expert-referee scores.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mediabuyerbench.evaluator import load_case, render_prompt


DEFAULT_SUITE = ROOT / "suites" / "google_search_public_demo_v1.json"

CANDIDATES: dict[str, dict[str, str]] = {
    "gpt-5.6-terra": {"runner": "codex", "model": "gpt-5.6-terra"},
    "gemini-3.1-pro-high": {"runner": "agy", "model": "gemini-3.1-pro-high"},
    "grok-4.6": {"runner": "grok", "model": "grok-4.6"},
}

PREFIX = """You are being evaluated by MediaBuyerBench as a Google Search operator.
Answer only from the benchmark case packet. Do not use tools, browse, research,
or rely on outside facts not supplied in the packet. Do not assume missing
business facts. Give a concise, auditable operating decision: diagnosis,
decisive evidence, material uncertainty, smallest safe action and scope, what
not to do yet, and a measurement/go-no-go rule. Do not reveal private reasoning.

"""


def load_suite(path: Path) -> dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    for field in ("id", "case_split", "case_ids"):
        if field not in suite:
            raise ValueError(f"Suite missing {field}")
    return suite


def run_candidate(candidate_id: str, prompt: str) -> tuple[str, float, str]:
    candidate = CANDIDATES[candidate_id]
    started = time.monotonic()
    if candidate["runner"] == "codex":
        with tempfile.NamedTemporaryFile(suffix=".txt") as output_file:
            proc = subprocess.run(
                [
                    "codex", "exec", "--model", candidate["model"], "--cd", str(ROOT),
                    "--sandbox", "read-only", "--output-last-message", output_file.name, "-",
                ],
                cwd=ROOT,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=360,
            )
            if proc.returncode:
                raise RuntimeError(f"{candidate_id} failed: {(proc.stdout + proc.stderr)[-1200:]}")
            response = Path(output_file.name).read_text(encoding="utf-8")
            log = proc.stdout + proc.stderr
    elif candidate["runner"] == "agy":
        proc = subprocess.run(
            [
                "agy", "--prompt", prompt, "--model", candidate["model"], "--mode", "plan",
                "--sandbox", "--disable-slash-commands", "--print-timeout", "5m", "--effort", "high",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=360,
        )
        if proc.returncode:
            raise RuntimeError(f"{candidate_id} failed: {proc.stderr[-1200:]}")
        response, log = proc.stdout, proc.stderr
    else:
        proc = subprocess.run(
            [
                "grok", "--single", prompt, "--model", candidate["model"], "--no-plan",
                "--no-subagents", "--no-memory", "--disable-web-search", "--permission-mode", "plan",
                "--sandbox", "read-only",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=360,
        )
        if proc.returncode:
            raise RuntimeError(f"{candidate_id} failed: {proc.stderr[-1200:]}")
        response, log = proc.stdout, proc.stderr
    return response.strip(), round(time.monotonic() - started, 3), log


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", choices=sorted(CANDIDATES), required=True)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    suite = load_suite(args.suite)
    case_dir = ROOT / suite["case_split"]
    cases = {case["id"]: case for case in (load_case(path) for path in case_dir.glob("*.json"))}
    if set(cases) != set(suite["case_ids"]):
        raise SystemExit("Suite case files do not exactly match declared case IDs")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    metrics: list[dict[str, Any]] = []
    for case_id in suite["case_ids"]:
        case = cases[case_id]
        print(f"Running {args.candidate} on {case_id}...", flush=True)
        response, elapsed_seconds, log = run_candidate(args.candidate, PREFIX + render_prompt(case))
        (args.output_dir / f"{case_id}.md").write_text(response + "\n", encoding="utf-8")
        (args.output_dir / f"{case_id}.log").write_text(log, encoding="utf-8")
        metrics.append({"case_id": case_id, "elapsed_seconds": elapsed_seconds})

    metadata = {
        "suite_id": suite["id"],
        "candidate_id": args.candidate,
        "runner": CANDIDATES[args.candidate]["runner"],
        "model": CANDIDATES[args.candidate]["model"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "candidate_protocol": "no_tools_or_external_research",
        "cases": metrics,
        "total_elapsed_seconds": round(sum(item["elapsed_seconds"] for item in metrics), 3),
    }
    (args.output_dir / "metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
