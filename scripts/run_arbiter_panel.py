#!/usr/bin/env python3
"""Run a blinded Google Search arbiter panel over fixed candidate responses.

The runners deliberately see candidate labels only. They never receive source
model identities, canonical answers, deterministic checks, or other judges'
scores. Three independent judgments per candidate/case are then aggregated by
the benchmark's median/majority policy.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mediabuyerbench.evaluator import load_case, render_prompt
from mediabuyerbench.judge import (
    aggregate_judgments,
    decision_record_check,
    load_rubric,
    render_arbiter,
    validate_judgment,
)


JUDGES = {
    "gpt-5.6-sol": ("codex", "gpt-5.6-sol"),
    "gpt-5.6-terra": ("codex", "gpt-5.6-terra"),
    "gemini-3.1-pro-high": ("agy", "gemini-3.1-pro-high"),
}


def extract_case_response(candidate: str, case_id: str) -> str:
    marker = re.compile(rf"^CASE {re.escape(case_id)}\s*$", re.MULTILINE)
    match = marker.search(candidate)
    if not match:
        raise ValueError(f"Candidate does not contain {case_id}")
    next_match = re.compile(r"^CASE \S+\s*$", re.MULTILINE).search(candidate, match.end())
    return candidate[match.end() : next_match.start() if next_match else None].strip()


def build_panel_prompt(
    case: dict[str, Any], candidates: dict[str, str], rubric: dict[str, Any]
) -> str:
    judgment_schema = dict(rubric["output_schema"])
    judgment_schema["candidate_id"] = "string, must exactly equal one provided anonymous candidate label"
    schema = {"judgments": [judgment_schema]}
    candidate_sections = []
    for candidate_id, response in candidates.items():
        candidate_sections.extend([f"## Anonymous candidate ({candidate_id})", response, ""])
    return "\n".join(
        [
            rubric["judge_instructions"],
            "",
            render_arbiter(rubric),
            "",
            "Scoring dimensions:",
            *(
                f"- {dimension['id']} ({int(dimension['weight'] * 100)}%): "
                f"{dimension['description']}"
                for dimension in rubric["dimensions"]
            ),
            "",
            f"Critical-error rule: {rubric['critical_error_rule']}",
            "",
            "Each candidate label is anonymous and has no model/provider identity. "
            "Score every response independently; do not normalize scores across candidates, assume facts, "
            "or infer a hidden answer from the other candidates.",
            "For every dimension, cite a short response excerpt, packet facts, and applicable "
            "operator-arbiter rule ids. A score of 4 requires every element in that dimension.",
            f"Deterministic response-format check (not a canonical answer): "
            f"{json.dumps(decision_record_check(response))}",
            "",
            "## Case packet",
            render_prompt(case).rstrip(),
            "",
            *candidate_sections,
            "Return JSON only with this shape:",
            json.dumps(schema, indent=2),
            "",
            f"Every judgment must use this exact case_id: {case['id']}",
            "Use each provided candidate_id exactly once.",
        ]
    )


def parse_json_output(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Judge did not return valid JSON: {raw[-500:]}") from exc


def run_judge(judge_id: str, prompt: str) -> str:
    runner, model = JUDGES[judge_id]
    if runner == "agy":
        proc = subprocess.run(
            [
                "agy",
                "--prompt",
                prompt,
                "--model",
                model,
                "--mode",
                "plan",
                "--sandbox",
                "--disable-slash-commands",
                "--print-timeout",
                "5m",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=330,
        )
        if proc.returncode:
            raise RuntimeError(f"{judge_id} failed: {proc.stderr[-1000:]}")
        return proc.stdout

    with tempfile.NamedTemporaryFile(suffix=".txt") as output_file:
        proc = subprocess.run(
            [
                "codex",
                "exec",
                "--model",
                model,
                "--cd",
                str(ROOT),
                "--sandbox",
                "read-only",
                "--output-last-message",
                output_file.name,
                "-",
            ],
            cwd=ROOT,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=330,
        )
        if proc.returncode:
            raise RuntimeError(f"{judge_id} failed: {(proc.stdout + proc.stderr)[-1000:]}")
        return Path(output_file.name).read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument(
        "--case-dir", type=Path, default=ROOT / "cases" / "private_google_expert_review"
    )
    parser.add_argument(
        "--judge",
        action="append",
        choices=sorted(JUDGES),
        default=[],
        help="Repeat three times for the blind judge panel.",
    )
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    judges = args.judge or list(JUDGES)
    if len(judges) != 3 or len(set(judges)) != 3:
        raise SystemExit("Use exactly three distinct judges for the panel")

    candidates = {
        path.stem.removeprefix("candidate_"): path.read_text(encoding="utf-8")
        for path in sorted(args.candidate_dir.glob("candidate_*.md"))
    }
    if not candidates:
        raise SystemExit("No candidate_*.md files found")
    cases = [load_case(path) for path in sorted(args.case_dir.glob("*.json"))]
    if not cases:
        raise SystemExit("No cases found")
    rubric = load_rubric()
    output_dir = args.output_dir or (
        ROOT / ".runs" / f"arbiter_panel_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    per_candidate: dict[str, list[dict[str, Any]]] = {candidate_id: [] for candidate_id in candidates}
    raw_judgments: dict[str, Any] = {}
    for case in cases:
        responses = {
            candidate_id: extract_case_response(candidate, case["id"])
            for candidate_id, candidate in candidates.items()
        }
        prompt = build_panel_prompt(case, responses, rubric)
        grouped_judgments: dict[str, list[dict[str, Any]]] = {
            candidate_id: [] for candidate_id in candidates
        }
        for judge_id in judges:
            print(f"Judging {case['id']} with {judge_id}...", flush=True)
            raw_path = output_dir / "raw" / judge_id / f"{case['id']}.txt"
            raw_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if raw_path.exists():
                    raw = raw_path.read_text(encoding="utf-8")
                    try:
                        parse_json_output(raw)
                    except ValueError:
                        print(f"Retrying invalid cached result for {case['id']} with {judge_id}...", flush=True)
                        raw = run_judge(judge_id, prompt)
                        raw_path.write_text(raw, encoding="utf-8")
                    else:
                        print(f"Reusing {case['id']} with {judge_id}...", flush=True)
                else:
                    raw = run_judge(judge_id, prompt)
                    raw_path.write_text(raw, encoding="utf-8")
                result = parse_json_output(raw)
                judgments = result.get("judgments")
                if not isinstance(judgments, list):
                    raise ValueError("Judge result must contain a judgments array")
                returned_ids = {judgment.get("candidate_id") for judgment in judgments}
                if returned_ids != set(candidates) or len(judgments) != len(candidates):
                    raise ValueError(f"{judge_id} did not return every candidate exactly once")
                for judgment in judgments:
                    candidate_id = judgment.pop("candidate_id")
                    validate_judgment(judgment, rubric)
                    grouped_judgments[candidate_id].append(judgment)
                    raw_judgments.setdefault(case["id"], {}).setdefault(candidate_id, {})[
                        judge_id
                    ] = judgment
            except Exception as exc:
                error_path = raw_path.with_suffix(".error.txt")
                error_path.write_text(str(exc), encoding="utf-8")
                raise
        for candidate_id, judgments in grouped_judgments.items():
            per_candidate[candidate_id].append(aggregate_judgments(judgments, rubric))

    candidate_summaries = {}
    for candidate_id, scores in per_candidate.items():
        candidate_summaries[candidate_id] = {
            "average_judge_score": round(sum(score["judge_score"] for score in scores) / len(scores), 1),
            "methodology_pass_rate": round(
                sum(bool(score["methodology_pass"]) for score in scores) / len(scores), 3
            ),
            "critical_case_count": sum(bool(score["critical_errors"]) for score in scores),
        }

    payload = {
        "rubric_id": rubric["id"],
        "rubric_version": rubric["version"],
        "arbiter_id": rubric["arbiter"]["id"],
        "judges": judges,
        "candidate_summaries": candidate_summaries,
        "case_panels": per_candidate,
        "raw_judgments": raw_judgments,
    }
    (output_dir / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("\nCandidate\tAvg judge score\tMethodology pass\tCritical cases")
    for candidate_id, summary in sorted(candidate_summaries.items()):
        print(
            f"{candidate_id}\t{summary['average_judge_score']}\t"
            f"{summary['methodology_pass_rate']:.0%}\t{summary['critical_case_count']}"
        )
    print(f"\nWrote {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
