import argparse
import io
import unittest
from contextlib import redirect_stdout

from mediabuyerbench import cli

ROOT = cli.ROOT
CASE = str(ROOT / "cases" / "public_lite" / "google" / "search_term_waste_001.json")
RESPONSE = str(ROOT / "examples" / "responses" / "google_search_term_waste_001.md")


def _run_score(min_score: float, as_json: bool = False) -> int:
    args = argparse.Namespace(case=CASE, response=RESPONSE, json=as_json, min_score=min_score)
    with redirect_stdout(io.StringIO()):
        return cli.cmd_score(args)


class CmdScoreExitCodeTest(unittest.TestCase):
    """Pin the CLI pass/fail contract that CI relies on."""

    def test_returns_zero_when_score_meets_threshold(self):
        # The bundled good sample scores 100, so a threshold of 80 must pass.
        self.assertEqual(_run_score(80), 0)

    def test_returns_one_when_score_below_threshold(self):
        # A threshold above any achievable score must fail.
        self.assertEqual(_run_score(101), 1)

    def test_exit_code_is_independent_of_json_flag(self):
        self.assertEqual(_run_score(80, as_json=True), 0)
        self.assertEqual(_run_score(101, as_json=True), 1)


if __name__ == "__main__":
    unittest.main()
