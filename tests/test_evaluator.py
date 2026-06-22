import unittest
from pathlib import Path

from mediabuyerbench.evaluator import (
    _is_negated_match,
    load_case,
    render_prompt,
    score_response,
    summarize_score,
    validate_case,
)

ROOT = Path(__file__).resolve().parent.parent


class EvaluatorTest(unittest.TestCase):
    def test_cases_load_and_render(self):
        for path in (ROOT / "cases" / "public_lite").rglob("*.json"):
            case = load_case(path)
            prompt = render_prompt(case)
            self.assertIn(case["title"], prompt)
            self.assertIn(case["user_prompt"], prompt)

    def test_good_sample_scores_high(self):
        case_path = ROOT / "cases" / "public_lite" / "google" / "search_term_waste_001.json"
        response_path = ROOT / "examples" / "responses" / "google_search_term_waste_001.md"
        score = score_response(load_case(case_path), response_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(score["overall_score"], 80)
        self.assertEqual(score["forbidden_hits"], [])

    def test_forbidden_penalty_applies(self):
        case_path = ROOT / "cases" / "public_lite" / "cross_channel" / "platform_cpa_lies_001.json"
        bad_response = "Move budget to Meta because platform CPA is cheaper. Maximize leads."
        score = score_response(load_case(case_path), bad_response)
        self.assertLess(score["overall_score"], 50)
        self.assertGreaterEqual(len(score["forbidden_hits"]), 1)

    def test_negated_forbidden_phrase_is_not_penalized(self):
        case_path = ROOT / "cases" / "public_lite" / "meta" / "creative_fatigue_001.json"
        response = "Creative fatigue is the issue. Do not kill all old creatives; keep the control while testing new hooks. Do not overhaul the landing page. CTR fell while CVR is stable and frequency rose."
        score = score_response(load_case(case_path), response)
        self.assertEqual(score["forbidden_hits"], [])

    def test_zero_weight_concept_does_not_crash_skill_scores(self):
        case = {
            "id": "zero_weight",
            "provider": "test",
            "category": "test",
            "difficulty": "easy",
            "expected": {
                "required_concepts": [
                    {"id": "a", "weight": 0, "skills": ["diagnosis"], "phrases": ["foo"]}
                ]
            },
        }
        score = score_response(case, "no matching phrase here")
        self.assertEqual(score["skill_scores"], {"diagnosis": 0.0})


class ValidateCaseTest(unittest.TestCase):
    def _valid_case(self):
        return {
            "id": "c1",
            "title": "t",
            "provider": "google_ads",
            "category": "search",
            "difficulty": "easy",
            "business": {},
            "user_prompt": "p",
            "data": [],
            "expected": {"required_concepts": [{"id": "a", "phrases": ["x"]}]},
        }

    def test_valid_case_passes(self):
        validate_case(self._valid_case())  # should not raise

    def test_missing_top_level_field_raises(self):
        case = self._valid_case()
        del case["provider"]
        with self.assertRaises(ValueError) as ctx:
            validate_case(case)
        self.assertIn("provider", str(ctx.exception))

    def test_missing_required_concepts_raises(self):
        case = self._valid_case()
        case["expected"] = {}
        with self.assertRaises(ValueError) as ctx:
            validate_case(case)
        self.assertIn("required_concepts", str(ctx.exception))

    def test_empty_required_concepts_raises(self):
        case = self._valid_case()
        case["expected"]["required_concepts"] = []
        with self.assertRaises(ValueError) as ctx:
            validate_case(case)
        self.assertIn("at least one required concept", str(ctx.exception))


class NegatedMatchTest(unittest.TestCase):
    def test_negator_immediately_before_phrase_is_negated(self):
        self.assertTrue(_is_negated_match("do not kill all old creatives", "kill all"))

    def test_no_negator_is_not_negated(self):
        self.assertFalse(_is_negated_match("kill all old creatives", "kill all"))

    def test_negator_inside_window_is_negated(self):
        self.assertTrue(_is_negated_match("never kill all", "kill all"))

    def test_negator_beyond_window_is_not_negated(self):
        # The negator sits far enough before the phrase to fall outside the
        # 40-character lookbehind window, so it must not suppress the match.
        self.assertFalse(_is_negated_match("never" + " " * 50 + "kill all", "kill all"))

    def test_absent_phrase_is_not_negated(self):
        self.assertFalse(_is_negated_match("great campaign here", "kill all"))


class SummarizeScoreTest(unittest.TestCase):
    def test_summary_includes_core_fields_and_omits_empty_forbidden(self):
        case_path = ROOT / "cases" / "public_lite" / "google" / "search_term_waste_001.json"
        response = (ROOT / "examples" / "responses" / "google_search_term_waste_001.md").read_text(encoding="utf-8")
        summary = summarize_score(score_response(load_case(case_path), response))
        self.assertIn("Case: google_search_term_waste_001", summary)
        self.assertIn("Overall: ", summary)
        self.assertIn("Matched required concepts:", summary)
        self.assertIn("Skill scores:", summary)
        # No forbidden phrases in a good response, so that section is omitted.
        self.assertNotIn("Forbidden hits:", summary)

    def test_summary_renders_forbidden_hits_section(self):
        case_path = ROOT / "cases" / "public_lite" / "cross_channel" / "platform_cpa_lies_001.json"
        bad_response = "Move budget to Meta because platform CPA is cheaper. Maximize leads."
        summary = summarize_score(score_response(load_case(case_path), bad_response))
        self.assertIn("Forbidden hits:", summary)
        self.assertIn("Case: cross_channel_platform_cpa_lies_001", summary)


class RenderPromptTableTest(unittest.TestCase):
    def test_data_table_renders_with_ragged_rows_and_numeric_cells(self):
        case = {
            "title": "T",
            "business": {"goal": "leads"},
            "user_prompt": "diagnose",
            "data": [
                {
                    "name": "Spend",
                    "notes": "last 7 days",
                    "rows": [
                        {"campaign": "A", "cost": 100},  # numeric value is coerced to str
                        {"campaign": "B"},               # ragged row: missing 'cost'
                    ],
                }
            ],
        }
        out = render_prompt(case)
        self.assertIn("last 7 days", out)          # block notes rendered
        self.assertIn("campaign | cost", out)      # header row from first row's keys
        self.assertIn("--- | ---", out)            # markdown separator
        self.assertIn("A | 100", out)              # non-string cell coerced
        self.assertIn("B | ", out)                 # missing key renders as empty cell, no crash


if __name__ == "__main__":
    unittest.main()
