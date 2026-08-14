import argparse
import io
import unittest
from contextlib import redirect_stdout

from mediabuyerbench import cli

ROOT = cli.ROOT
CASE = str(ROOT / "cases" / "public_lite" / "google" / "retrieval_scope_001.json")
RESPONSE = str(ROOT / "examples" / "responses" / "google_retrieval_scope_001.md")


def _run_score(min_score: float, as_json: bool = False) -> int:
    args = argparse.Namespace(case=CASE, response=RESPONSE, json=as_json, min_score=min_score)
    with redirect_stdout(io.StringIO()):
        return cli.cmd_score(args)


class CmdScoreExitCodeTest(unittest.TestCase):
    """Pin the CLI pass/fail contract that CI relies on."""

    def test_returns_zero_when_score_meets_threshold(self):
        # The bundled good sample clears the standard public-lite threshold.
        self.assertEqual(_run_score(80), 0)

    def test_returns_one_when_score_below_threshold(self):
        # A threshold above any achievable score must fail.
        self.assertEqual(_run_score(101), 1)

    def test_exit_code_is_independent_of_json_flag(self):
        self.assertEqual(_run_score(80, as_json=True), 0)
        self.assertEqual(_run_score(101, as_json=True), 1)


class RunSamplesParserTest(unittest.TestCase):
    def test_accepts_an_alternate_split_and_response_directory(self):
        parser = cli.build_parser()
        args = parser.parse_args(
            [
                "run-samples",
                "--split",
                "private_google_review",
                "--response-dir",
                "examples/responses/private_google_review",
            ]
        )
        self.assertEqual(args.split, "private_google_review")
        self.assertEqual(args.response_dir, "examples/responses/private_google_review")

    def test_accepts_panel_aggregation_and_calibration_commands(self):
        parser = cli.build_parser()
        aggregate = parser.parse_args(
            ["aggregate-judgments", "--judgment", "one.json", "--judgment", "two.json", "--judgment", "three.json"]
        )
        calibration = parser.parse_args(["calibrate-judge", "--input", "labels.json"])
        self.assertEqual(aggregate.command, "aggregate-judgments")
        self.assertEqual(calibration.command, "calibrate-judge")

    def test_accepts_source_grounded_expert_referee_commands(self):
        parser = cli.build_parser()
        reference = parser.parse_args(["expert-reference-prompt", "--case", "case.json"])
        review = parser.parse_args(
            [
                "expert-review-prompt",
                "--case",
                "case.json",
                "--response",
                "candidate.md",
                "--reference",
                "reference.json",
            ]
        )
        score = parser.parse_args(["expert-score", "--case", "case.json", "--review", "review.json"])
        self.assertEqual(reference.command, "expert-reference-prompt")
        self.assertEqual(review.command, "expert-review-prompt")
        self.assertEqual(score.command, "expert-score")


if __name__ == "__main__":
    unittest.main()
