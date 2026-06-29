import unittest
from pathlib import Path

from mediabuyerbench.evaluator import load_case
from mediabuyerbench.judge import (
    JudgeVerdict,
    build_judge_prompt,
    claude_cli_judge,
    deterministic_judge,
    parse_verdict,
)
from mediabuyerbench.rubric import load_criteria_library, score_response_rubric

ROOT = Path(__file__).resolve().parent.parent
CROSS_CASE = ROOT / "cases" / "public_lite" / "cross_channel" / "platform_cpa_lies_001.json"
CROSS_RESPONSE = ROOT / "examples" / "responses" / "cross_channel_platform_cpa_lies_001.md"


class ParseVerdictTest(unittest.TestCase):
    def test_parses_json_object(self):
        v = parse_verdict('Sure: {"satisfied": true, "score": 0.8, "rationale": "ok"} done')
        self.assertTrue(v.satisfied)
        self.assertEqual(v.score, 0.8)
        self.assertEqual(v.rationale, "ok")

    def test_score_is_clamped(self):
        self.assertEqual(parse_verdict('{"satisfied": true, "score": 5}').score, 1.0)
        self.assertEqual(parse_verdict('{"satisfied": false, "score": -2}').score, 0.0)

    def test_satisfied_inferred_from_score_when_absent(self):
        self.assertTrue(parse_verdict('{"score": 0.9}').satisfied)
        self.assertFalse(parse_verdict('{"score": 0.2}').satisfied)

    def test_no_json_raises(self):
        with self.assertRaises(ValueError):
            parse_verdict("the model refused to answer")


class BuildPromptTest(unittest.TestCase):
    def test_prompt_contains_response_and_guidance(self):
        library = load_criteria_library()
        crit = library["xc.attribution.platform_cpa_vs_truth"]
        item = {"criterion": crit["id"], "expected": "flag", "detect": []}
        prompt = build_judge_prompt(crit, item, "MY RESPONSE TEXT", {"business": {}, "user_prompt": "Q?"})
        self.assertIn("MY RESPONSE TEXT", prompt)
        self.assertIn(crit["judge_guidance"][:20], prompt)
        self.assertIn("flag", prompt)


class CliJudgeWithFakeRunnerTest(unittest.TestCase):
    def test_judge_uses_injected_runner(self):
        captured = {}

        def fake_runner(prompt: str) -> str:
            captured["prompt"] = prompt
            return '{"satisfied": true, "score": 1.0, "rationale": "good"}'

        judge = claude_cli_judge(runner=fake_runner)
        library = load_criteria_library()
        crit = library["xc.attribution.platform_cpa_vs_truth"]
        verdict = judge(crit, {"expected": "flag"}, "resp", {"business": {}, "user_prompt": "q"})
        self.assertTrue(verdict.satisfied)
        self.assertIn("resp", captured["prompt"])


class JudgeIntegrationTest(unittest.TestCase):
    def test_default_is_deterministic_and_offline(self):
        # No judge passed -> identical to deterministic scoring (no network/CLI).
        case = load_case(CROSS_CASE)
        baseline = score_response_rubric(case, CROSS_RESPONSE.read_text(encoding="utf-8"))
        self.assertGreater(baseline["rubric_score"], 0)
        for item in baseline["items"]:
            self.assertIsNone(item["rationale"])  # deterministic path sets no rationale

    def test_judge_only_rescores_judge_and_hybrid_positive_items(self):
        case = load_case(CROSS_CASE)
        library = load_criteria_library()
        response = CROSS_RESPONSE.read_text(encoding="utf-8")

        def zero_judge(criterion, item, resp, c):
            return JudgeVerdict(False, 0.0, "rejected by test judge")

        baseline = score_response_rubric(case, response, library=library)
        judged = score_response_rubric(case, response, library=library, judge=zero_judge)
        # A zero-judge zeroes the positive judge/hybrid items, lowering the score.
        self.assertLess(judged["rubric_score"], baseline["rubric_score"])

        for item in judged["items"]:
            crit_type = library[item["criterion"]]["type"]
            if item["expected"] == "avoid":
                # Guardrails stay deterministic regardless of the judge.
                self.assertIsNone(item["rationale"])
            elif crit_type in ("judge", "hybrid"):
                self.assertEqual(item["rationale"], "rejected by test judge")
                self.assertFalse(item["satisfied"])
            else:  # programmatic positive items untouched by the judge
                self.assertIsNone(item["rationale"])

    def test_perfect_judge_satisfies_judge_items(self):
        case = load_case(CROSS_CASE)

        def perfect_judge(criterion, item, resp, c):
            return JudgeVerdict(True, 1.0, "great")

        result = score_response_rubric(case, "anything", judge=perfect_judge)
        judged_items = [i for i in result["items"] if i["rationale"] == "great"]
        self.assertTrue(judged_items)
        self.assertTrue(all(i["satisfied"] for i in judged_items))


class DeterministicJudgeTest(unittest.TestCase):
    def test_detects_phrase(self):
        v = deterministic_judge({}, {"detect": ["pipeline"]}, "we should track pipeline", {})
        self.assertTrue(v.satisfied)
        v2 = deterministic_judge({}, {"detect": ["pipeline"]}, "nothing relevant", {})
        self.assertFalse(v2.satisfied)


if __name__ == "__main__":
    unittest.main()
