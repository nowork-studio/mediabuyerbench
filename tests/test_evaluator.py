import unittest
from pathlib import Path

from mediabuyerbench.evaluator import load_case, render_prompt, score_response

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
        score = score_response(load_case(case_path), response_path.read_text())
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
