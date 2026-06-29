"""Optional, pluggable LLM judge for the rubric's ``judge``/``hybrid`` criteria.

Deterministic substring matching cannot robustly grade subjective judgment
(see ``rubrics/README.md``). This module provides an opt-in LLM judge for the
criteria tagged ``judge``/``hybrid``, while ``programmatic`` items and ``avoid``
guardrails stay deterministic.

Design goals:
- No third-party dependency. Following the repo's existing pattern
  (``scripts/run_baseline_models.py``), the default judge shells out to the
  ``claude`` CLI. It is never invoked unless explicitly requested, so CI and
  offline runs are unaffected.
- Injectable + testable. A judge is just a callable; the CLI runner is a small
  injectable function, so the prompt-building and verdict-parsing logic is unit
  tested with a fake runner and no network.

A ``Judge`` is ``Callable[[criterion, item, response, case], JudgeVerdict]``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Callable

from mediabuyerbench.evaluator import _find_phrase

Runner = Callable[[str], str]
Judge = Callable[[dict, dict, str, dict], "JudgeVerdict"]


@dataclass(frozen=True)
class JudgeVerdict:
    satisfied: bool
    score: float  # 0.0 .. 1.0
    rationale: str = ""


def build_judge_prompt(criterion: dict, item: dict, response: str, case: dict) -> str:
    """Build a fixed, self-contained judge prompt for one criterion."""
    guidance = criterion.get("judge_guidance") or criterion.get("condition", "")
    expectation = item.get("expected", "flag")
    source = criterion.get("source", {}).get("name", "")
    return (
        "You are a strict, fair senior paid-media reviewer. Score ONE criterion for the "
        "response below. Do not reward keyword presence; reward whether the reasoning is "
        "actually correct and sufficient.\n\n"
        f"Criterion: {criterion.get('title', criterion.get('id'))}\n"
        f"What good looks like: {guidance}\n"
        f"Source: {source}\n"
        f"Expected behaviour: the response should '{expectation}' this.\n\n"
        f"Business context: {json.dumps(case.get('business', {}))}\n"
        f"User question: {case.get('user_prompt', '')}\n\n"
        "Response under review:\n"
        '"""\n'
        f"{response}\n"
        '"""\n\n'
        'Return ONLY a JSON object: {"satisfied": <bool>, "score": <number 0..1>, '
        '"rationale": "<one sentence>"}.'
    )


def parse_verdict(text: str) -> JudgeVerdict:
    """Parse a judge's JSON verdict out of free-form model output."""
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError(f"No JSON object found in judge output: {text[:200]!r}")
    obj = json.loads(text[start : end + 1])
    if "score" in obj:
        score = float(obj["score"])
    else:
        score = 1.0 if obj.get("satisfied") else 0.0
    score = max(0.0, min(1.0, score))
    satisfied = bool(obj["satisfied"]) if "satisfied" in obj else score >= 0.5
    return JudgeVerdict(satisfied=satisfied, score=score, rationale=str(obj.get("rationale", "")))


def _claude_cli_runner(model: str, binary: str) -> Runner:
    def run(prompt: str) -> str:
        if shutil.which(binary) is None:
            raise RuntimeError(f"{binary!r} CLI not found on PATH; cannot run the LLM judge")
        proc = subprocess.run(
            [binary, "--print", "--model", model, "--disallowedTools", "Bash,Read,Write,Edit,Grep,Glob"],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode != 0:
            raise RuntimeError(f"judge CLI failed (exit {proc.returncode}): {proc.stderr[:200]}")
        return proc.stdout
    return run


def claude_cli_judge(model: str = "sonnet", binary: str = "claude", runner: Runner | None = None) -> Judge:
    """Build a Judge that grades each criterion via the claude CLI (or an injected runner)."""
    run = runner if runner is not None else _claude_cli_runner(model, binary)

    def judge(criterion: dict, item: dict, response: str, case: dict) -> JudgeVerdict:
        return parse_verdict(run(build_judge_prompt(criterion, item, response, case)))

    return judge


def deterministic_judge(criterion: dict, item: dict, response: str, case: dict) -> JudgeVerdict:
    """Offline fallback judge: detect-phrase match (the default rubric behaviour)."""
    phrases = item.get("detect", [])
    matched = _find_phrase(response, phrases) is not None if phrases else False
    return JudgeVerdict(matched, 1.0 if matched else 0.0, "deterministic detect match" if matched else "no detect match")
