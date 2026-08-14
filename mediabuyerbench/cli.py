from __future__ import annotations

import argparse
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from mediabuyerbench.evaluator import load_case, render_prompt, score_response, summarize_score
from mediabuyerbench.expert_referee import (
    build_reference_prompt,
    build_review_prompt,
    load_source_pack,
    score_expert_review,
)
from mediabuyerbench.judge import aggregate_judgments, build_judge_prompt, calibration_report

ROOT = Path(__file__).resolve().parent.parent


def iter_cases(split: str = "public_lite") -> Iterator[Path]:
    case_root = ROOT / "cases" / split
    yield from sorted(case_root.rglob("*.json"))


def cmd_list(args: argparse.Namespace) -> int:
    for path in iter_cases(args.split):
        case = load_case(path)
        rel = path.relative_to(ROOT)
        print(f"{case['id']}\t{case['provider']}\t{case['difficulty']}\t{rel}")
    return 0


def cmd_prompt(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    print(render_prompt(case))
    return 0


def cmd_score(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    response = Path(args.response).read_text(encoding="utf-8")
    judge_output = getattr(args, "judge_output", None)
    judgment = json.loads(Path(judge_output).read_text(encoding="utf-8")) if judge_output else None
    score = score_response(case, response, judgment)
    if args.json:
        print(json.dumps(score, indent=2))
    else:
        print(summarize_score(score))
    return 0 if score["overall_score"] >= args.min_score else 1


def cmd_judge_prompt(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    response = Path(args.response).read_text(encoding="utf-8")
    print(build_judge_prompt(case, response))
    return 0


def cmd_aggregate_judgments(args: argparse.Namespace) -> int:
    judgments = [json.loads(Path(path).read_text(encoding="utf-8")) for path in args.judgment]
    print(json.dumps(aggregate_judgments(judgments), indent=2))
    return 0


def cmd_calibrate_judge(args: argparse.Namespace) -> int:
    calibration = json.loads(Path(args.input).read_text(encoding="utf-8"))
    print(json.dumps(calibration_report(calibration), indent=2))
    return 0


def cmd_expert_reference_prompt(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    source_pack = load_source_pack(args.source_pack) if args.source_pack else None
    print(build_reference_prompt(case, source_pack))
    return 0


def cmd_expert_review_prompt(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    source_pack = load_source_pack(args.source_pack) if args.source_pack else None
    response = Path(args.response).read_text(encoding="utf-8")
    reference = json.loads(Path(args.reference).read_text(encoding="utf-8"))
    print(build_review_prompt(case, response, reference, source_pack))
    return 0


def cmd_expert_score(args: argparse.Namespace) -> int:
    case = load_case(args.case)
    source_pack = load_source_pack(args.source_pack) if args.source_pack else None
    review = json.loads(Path(args.review).read_text(encoding="utf-8"))
    print(json.dumps(score_expert_review(review, case, source_pack), indent=2))
    return 0


def cmd_run_samples(args: argparse.Namespace) -> int:
    sample_dir = ROOT / args.response_dir if args.response_dir else ROOT / "examples" / "responses"
    pairs: list[tuple[Path, Path]] = []
    for case_path in iter_cases(args.split):
        case = load_case(case_path)
        sample_path = sample_dir / f"{case['id']}.md"
        if sample_path.exists():
            pairs.append((case_path, sample_path))

    if not pairs:
        raise SystemExit("No sample responses found")

    scores: list[dict[str, Any]] = []
    for case_path, sample_path in pairs:
        case = load_case(case_path)
        response = sample_path.read_text(encoding="utf-8")
        score = score_response(case, response)
        scores.append(score)
        print(f"{score['case_id']}: {score['overall_score']}/100")

    avg = sum(score["overall_score"] for score in scores) / len(scores)
    print(f"Average: {avg:.1f}/100 across {len(scores)} sample responses")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MediaBuyerBench CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list", help="List benchmark cases")
    list_parser.add_argument("--split", default="public_lite")
    list_parser.set_defaults(func=cmd_list)

    prompt_parser = sub.add_parser("prompt", help="Render the prompt for a case")
    prompt_parser.add_argument("case")
    prompt_parser.set_defaults(func=cmd_prompt)

    score_parser = sub.add_parser("score", help="Score a response file")
    score_parser.add_argument("--case", required=True)
    score_parser.add_argument("--response", required=True)
    score_parser.add_argument("--judge-output", help="Blind-judge JSON matching rubrics/google_search_v2.json")
    score_parser.add_argument("--json", action="store_true")
    score_parser.add_argument("--min-score", type=float, default=0.0)
    score_parser.set_defaults(func=cmd_score)

    judge_parser = sub.add_parser("judge-prompt", help="Render a blind Google Search judge prompt")
    judge_parser.add_argument("--case", required=True)
    judge_parser.add_argument("--response", required=True)
    judge_parser.set_defaults(func=cmd_judge_prompt)

    aggregate_parser = sub.add_parser(
        "aggregate-judgments", help="Median-aggregate an odd, blinded panel of judge JSON files"
    )
    aggregate_parser.add_argument("--judgment", action="append", required=True)
    aggregate_parser.set_defaults(func=cmd_aggregate_judgments)

    calibration_parser = sub.add_parser(
        "calibrate-judge", help="Report blind-panel agreement against reviewer labels"
    )
    calibration_parser.add_argument("--input", required=True)
    calibration_parser.set_defaults(func=cmd_calibrate_judge)

    reference_parser = sub.add_parser(
        "expert-reference-prompt", help="Render the source-grounded expert referee reference prompt"
    )
    reference_parser.add_argument("--case", required=True)
    reference_parser.add_argument("--source-pack")
    reference_parser.set_defaults(func=cmd_expert_reference_prompt)

    review_parser = sub.add_parser(
        "expert-review-prompt", help="Render the final source-grounded expert referee prompt"
    )
    review_parser.add_argument("--case", required=True)
    review_parser.add_argument("--response", required=True)
    review_parser.add_argument("--reference", required=True)
    review_parser.add_argument("--source-pack")
    review_parser.set_defaults(func=cmd_expert_review_prompt)

    expert_score_parser = sub.add_parser(
        "expert-score", help="Validate and score a source-grounded expert referee review"
    )
    expert_score_parser.add_argument("--case", required=True)
    expert_score_parser.add_argument("--review", required=True)
    expert_score_parser.add_argument("--source-pack")
    expert_score_parser.set_defaults(func=cmd_expert_score)

    sample_parser = sub.add_parser("run-samples", help="Score sample responses for a case split")
    sample_parser.add_argument("--split", default="public_lite")
    sample_parser.add_argument(
        "--response-dir",
        help="Response directory relative to the repository root; defaults to examples/responses",
    )
    sample_parser.set_defaults(func=cmd_run_samples)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
