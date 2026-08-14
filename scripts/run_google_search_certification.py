#!/usr/bin/env python3
"""Run the fixed private Google Search certification with a grounded referee.

Candidate answers must already exist. The referee first creates and saves one
reference decision per case without candidate answers, then reviews each
anonymous candidate against that frozen decision. Reusing the reference files
makes every model comparison within a certification version comparable.
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

from mediabuyerbench.evaluator import load_case
from mediabuyerbench.expert_referee import (
    build_reference_prompt,
    build_review_prompt,
    load_source_pack,
    score_expert_review,
    validate_expert_review,
    validate_reference,
)


DEFAULT_SUITE = ROOT / "suites" / "google_search_public_demo_v1.json"


def load_suite(path: Path) -> dict[str, Any]:
    """Load the frozen suite manifest and prove it matches the case files."""
    suite = json.loads(path.read_text(encoding="utf-8"))
    required = ("id", "status", "source_pack_id", "case_split", "case_ids")
    missing = [field for field in required if field not in suite]
    if missing:
        raise ValueError(f"Suite missing fields: {', '.join(missing)}")
    if not isinstance(suite["case_ids"], list) or not suite["case_ids"]:
        raise ValueError("Suite must declare at least one case id")
    if len(set(suite["case_ids"])) != len(suite["case_ids"]):
        raise ValueError("Suite case_ids must be unique")
    return suite


def parse_json_output(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Referee did not return valid JSON: {raw[-800:]}") from exc


def run_referee(model: str, prompt: str) -> str:
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
            timeout=360,
        )
        if proc.returncode:
            raise RuntimeError(f"Referee call failed: {(proc.stdout + proc.stderr)[-1200:]}")
        return Path(output_file.name).read_text(encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--response-dir", type=Path, required=True)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--referee-model", default="gpt-5.6-sol")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument(
        "--case-dir",
        type=Path,
        help="Override the case directory for a new suite version; cannot change the declared case IDs.",
    )
    parser.add_argument("--source-pack")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--reference-dir",
        type=Path,
        help="Reusable approved/provisional references. Defaults to <output-dir>/references.",
    )
    args = parser.parse_args()

    suite = load_suite(args.suite)
    source_pack = load_source_pack(args.source_pack) if args.source_pack else load_source_pack()
    if suite["source_pack_id"] != source_pack["id"]:
        raise SystemExit(
            f"Suite expects source pack {suite['source_pack_id']}, got {source_pack['id']}"
        )
    case_dir = args.case_dir or ROOT / suite["case_split"]
    cases_by_id = {case["id"]: case for case in (load_case(path) for path in sorted(case_dir.glob("*.json")))}
    declared_case_ids = list(suite["case_ids"])
    if set(cases_by_id) != set(declared_case_ids):
        raise SystemExit(
            "Suite case files do not exactly match its declared case_ids: "
            f"declared={declared_case_ids}, found={sorted(cases_by_id)}"
        )
    cases = [cases_by_id[case_id] for case_id in declared_case_ids]
    output_dir = args.output_dir or (
        ROOT / ".runs" / f"certification_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{args.candidate_id}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    reference_dir = args.reference_dir or output_dir / "references"
    reference_dir.mkdir(parents=True, exist_ok=True)

    scores = []
    for case in cases:
        response_path = args.response_dir / f"{case['id']}.md"
        if not response_path.exists():
            raise SystemExit(f"Missing candidate response: {response_path}")
        response = response_path.read_text(encoding="utf-8")
        reference_path = reference_dir / f"{case['id']}.json"
        if reference_path.exists():
            reference = json.loads(reference_path.read_text(encoding="utf-8"))
            validate_reference(reference, case, source_pack)
            print(f"Reusing reference decision for {case['id']}...", flush=True)
        else:
            print(f"Creating source-grounded reference decision for {case['id']}...", flush=True)
            reference = parse_json_output(run_referee(args.referee_model, build_reference_prompt(case, source_pack)))
            validate_reference(reference, case, source_pack)
            reference_path.write_text(json.dumps(reference, indent=2), encoding="utf-8")

        print(f"Reviewing anonymous candidate response for {case['id']}...", flush=True)
        review = parse_json_output(
            run_referee(args.referee_model, build_review_prompt(case, response, reference, source_pack))
        )
        validate_expert_review(review, case, source_pack)
        score = score_expert_review(review, case, source_pack)
        score["candidate_id"] = args.candidate_id
        (output_dir / f"{case['id']}.reference.json").write_text(
            json.dumps(reference, indent=2), encoding="utf-8"
        )
        (output_dir / f"{case['id']}.review.json").write_text(
            json.dumps(review, indent=2), encoding="utf-8"
        )
        (output_dir / f"{case['id']}.score.json").write_text(
            json.dumps(score, indent=2), encoding="utf-8"
        )
        scores.append(score)

    summary = {
        "suite_id": suite["id"],
        "suite_status": suite["status"],
        "source_pack_id": source_pack["id"],
        "source_pack_version": source_pack["version"],
        "referee_model": args.referee_model,
        "candidate_id": args.candidate_id,
        "cases": scores,
        "mean_expert_score": round(sum(score["expert_score"] for score in scores) / len(scores), 1),
        "methodology_pass_rate": round(
            sum(bool(score["methodology_pass"]) for score in scores) / len(scores), 3
        ),
        "critical_error_rate": round(
            sum(bool(score["critical_errors"]) for score in scores) / len(scores), 3
        ),
    }
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
