import unittest

from scripts.run_arbiter_panel import build_panel_prompt, extract_case_response, parse_json_output


class ArbiterPanelTest(unittest.TestCase):
    def test_extracts_only_the_requested_case_response(self):
        candidate = "CASE one\nFirst\n\nCASE two\nSecond"
        self.assertEqual(extract_case_response(candidate, "one"), "First")
        self.assertEqual(extract_case_response(candidate, "two"), "Second")

    def test_missing_case_is_an_error(self):
        with self.assertRaisesRegex(ValueError, "does not contain"):
            extract_case_response("CASE one\nFirst", "missing")

    def test_parses_fenced_json(self):
        self.assertEqual(parse_json_output("```json\n{\"x\": 1}\n```"), {"x": 1})

    def test_panel_prompt_keeps_candidates_anonymous_and_requires_every_item(self):
        prompt = build_panel_prompt(
            {"id": "case", "title": "Case", "business": {}, "user_prompt": "Do work", "data": []},
            {"alpha": "First response", "bravo": "Second response"},
            {
                "judge_instructions": "Judge.",
                "critical_error_rule": "No unsafe actions.",
                "output_schema": {"case_id": "string"},
                "dimensions": [],
                "arbiter": {
                    "id": "arbiter",
                    "purpose": "Purpose.",
                    "precedence": [],
                    "rules": [],
                    "provenance": [],
                },
            },
        )
        self.assertIn("Anonymous candidate (alpha)", prompt)
        self.assertIn("Anonymous candidate (bravo)", prompt)
        self.assertIn("Use each provided candidate_id exactly once", prompt)
