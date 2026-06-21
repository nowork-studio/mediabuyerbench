from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from mediabuyerbench.evaluator import load_case, render_prompt, score_response, summarize_score

ROOT = Path(__file__).resolve().parent.parent


def iter_cases(split: str = "public_lite"):
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
    score = score_response(case, response)
    if args.json:
        print(json.dumps(score, indent=2))
    else:
        print(summarize_score(score))
    return 0 if score["overall_score"] >= args.min_score else 1


def cmd_run_samples(args: argparse.Namespace) -> int:
    sample_dir = ROOT / "examples" / "responses"
    pairs: list[tuple[Path, Path]] = []
    for case_path in iter_cases("public_lite"):
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
    score_parser.add_argument("--json", action="store_true")
    score_parser.add_argument("--min-score", type=float, default=0.0)
    score_parser.set_defaults(func=cmd_score)

    sample_parser = sub.add_parser("run-samples", help="Score bundled sample responses")
    sample_parser.set_defaults(func=cmd_run_samples)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
