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
from mediabuyerbench.judge import (
    aggregate_judgments,
    build_judge_prompt,
    calibration_report,
    decision_record_check,
    load_rubric,
    render_arbiter,
    score_judgment,
    validate_judgment,
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
        case_path = ROOT / "cases" / "public_lite" / "google" / "retrieval_scope_001.json"
        response_path = ROOT / "examples" / "responses" / "google_retrieval_scope_001.md"
        score = score_response(load_case(case_path), response_path.read_text(encoding="utf-8"))
        self.assertGreaterEqual(score["overall_score"], 90)
        self.assertEqual(score["forbidden_hits"], [])

    def test_forbidden_penalty_applies(self):
        case_path = ROOT / "cases" / "public_lite" / "google" / "noncomparable_conversions_001.json"
        bad_response = "Move 20% of budget to Local Search because platform CPA is cheaper."
        score = score_response(load_case(case_path), bad_response)
        self.assertLess(score["overall_score"], 50)
        self.assertGreaterEqual(len(score["forbidden_hits"]), 1)

    def test_negated_forbidden_phrase_is_not_penalized(self):
        case_path = ROOT / "cases" / "public_lite" / "google" / "noncomparable_conversions_001.json"
        response = "Do not move 20% of Brand Search budget today; the two platform CPA values are not comparable."
        score = score_response(load_case(case_path), response)
        self.assertEqual(score["forbidden_hits"], [])

    def test_numeric_assertion_matches_within_tolerance(self):
        case_path = ROOT / "cases" / "public_lite" / "google" / "rank_before_budget_001.json"
        score = score_response(load_case(case_path), "CPA is $64.7 and rank lost impression share is 38%.")
        matched = {item["assertion_id"] for item in score["assertions"] if item["matched"]}
        self.assertEqual(matched, {"cpa", "rank_loss"})

    def test_cpa_spike_case_rewards_calibrated_diagnosis_and_penalizes_overclaim(self):
        case_path = ROOT / "cases" / "public_lite" / "google" / "cpa_spike_diagnosis_001.json"
        response_path = ROOT / "examples" / "responses" / "google_cpa_spike_diagnosis_001.md"
        score = score_response(load_case(case_path), response_path.read_text(encoding="utf-8"))
        self.assertEqual(score["overall_score"], 100.0)

        overclaim = score_response(load_case(case_path), "Broad match caused the CPA spike.")
        self.assertIn("unsupported_causality", {hit["id"] for hit in overclaim["forbidden_hits"]})

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
        self.assertIn("required concept or assertion", str(ctx.exception))

    def test_empty_required_concepts_raises(self):
        case = self._valid_case()
        case["expected"]["required_concepts"] = []
        with self.assertRaises(ValueError) as ctx:
            validate_case(case)
        self.assertIn("required concept or assertion", str(ctx.exception))

    def test_case_with_assertions_but_no_concepts_passes(self):
        case = self._valid_case()
        case["expected"] = {"required_assertions": [{"id": "a", "type": "number", "value": 1}]}
        validate_case(case)  # should not raise


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
        case_path = ROOT / "cases" / "public_lite" / "google" / "retrieval_scope_001.json"
        response = (ROOT / "examples" / "responses" / "google_retrieval_scope_001.md").read_text(encoding="utf-8")
        summary = summarize_score(score_response(load_case(case_path), response))
        self.assertIn("Case: google_retrieval_scope_001", summary)
        self.assertIn("Overall: ", summary)
        self.assertIn("Matched required concepts:", summary)
        self.assertIn("Required assertions:", summary)
        self.assertIn("Skill scores:", summary)
        # No forbidden phrases in a good response, so that section is omitted.
        self.assertNotIn("Forbidden hits:", summary)

    def test_summary_renders_forbidden_hits_section(self):
        case_path = ROOT / "cases" / "public_lite" / "google" / "noncomparable_conversions_001.json"
        bad_response = "Move 20% of budget to Local Search because platform CPA is cheaper."
        summary = summarize_score(score_response(load_case(case_path), bad_response))
        self.assertIn("Forbidden hits:", summary)
        self.assertIn("Case: google_noncomparable_conversions_001", summary)


class BlindJudgeTest(unittest.TestCase):
    def _judgment(self, case_id="c1", critical_errors=None):
        dimensions = (
            "evidence_trace",
            "causal_discipline",
            "preconditions_and_sequence",
            "intervention_scope",
            "decision_rule",
            "alternatives_and_tradeoffs",
        )
        return {
            "case_id": case_id,
            "evidence_trace": 3,
            "causal_discipline": 4,
            "preconditions_and_sequence": 4,
            "intervention_scope": 3,
            "decision_rule": 3,
            "alternatives_and_tradeoffs": 3,
            "critical_errors": critical_errors or [],
            "methodology_failures": [],
            "evidence": {
                dimension: {
                    "response_excerpt": "excerpt",
                    "packet_facts": ["packet fact"],
                    "arbiter_rule_ids": ["scope_and_validation"],
                }
                for dimension in dimensions
            },
            "rationale": "Uses the packet well and defers unsafe changes.",
        }

    def test_judge_score_uses_weighted_dimensions(self):
        score = score_judgment(self._judgment())
        self.assertEqual(score["judge_score"], 85.0)
        self.assertEqual(score["arbiter_id"], "google_search_operator_arbiter_v1")
        self.assertTrue(score["methodology_pass"])

    def test_judge_critical_error_caps_score(self):
        score = score_judgment(self._judgment(critical_errors=["unsafe global negative"]))
        self.assertEqual(score["judge_score"], 49.0)
        self.assertFalse(score["methodology_pass"])

    def test_methodology_gate_fails_even_with_a_high_numeric_score(self):
        judgment = self._judgment()
        judgment["preconditions_and_sequence"] = 2
        judgment["methodology_failures"] = ["Promotes a bid goal before validating import coverage."]
        score = score_judgment(judgment)
        self.assertGreater(score["judge_score"], 70)
        self.assertFalse(score["methodology_pass"])

    def test_judge_prompt_hides_canonical_answer_and_checks(self):
        case = self._valid_case_for_prompt()
        prompt = build_judge_prompt(case, "Candidate response")
        self.assertIn("Candidate response", prompt)
        self.assertIn("Use this exact case_id: c1", prompt)
        self.assertNotIn("required_concepts", prompt)
        self.assertIn("auditable decision method", prompt)
        self.assertIn("response-format check", prompt)
        self.assertIn("Google Search operator arbiter", prompt)
        self.assertIn("case packet is the source of truth", prompt.lower())
        self.assertIn("arbiter rule ids", prompt.lower())

    def test_rubric_has_a_pinned_operator_arbiter(self):
        arbiter = render_arbiter(load_rubric())
        self.assertIn("conversion_goal_integrity", arbiter)
        self.assertIn("smart_bidding_maturity", arbiter)
        self.assertIn("search_term_control", arbiter)

    def test_judgment_rejects_an_unknown_arbiter_rule(self):
        judgment = self._judgment()
        judgment["evidence"]["evidence_trace"]["arbiter_rule_ids"] = ["made_up_rule"]
        with self.assertRaisesRegex(ValueError, "known arbiter_rule_ids"):
            validate_judgment(judgment)

    def _valid_case_for_prompt(self):
        return {
            "id": "c1",
            "title": "Case",
            "business": {"goal": "leads"},
            "user_prompt": "Diagnose",
            "data": [],
        }

    def test_hybrid_requires_matching_case_id(self):
        case = ValidateCaseTest()._valid_case()
        with self.assertRaises(ValueError):
            score_response(case, "x", self._judgment(case_id="other"))

    def test_decision_record_check_finds_missing_labels(self):
        result = decision_record_check("Diagnosis\nDecisive evidence")
        self.assertFalse(result["passed"])
        self.assertIn("measurement and explicit go/no-go rule", result["missing_labels"])

    def test_aggregate_uses_median_and_majority_critical_errors(self):
        judgments = [self._judgment(), self._judgment(), self._judgment()]
        judgments[0]["evidence_trace"] = 1
        judgments[1]["evidence_trace"] = 3
        judgments[2]["evidence_trace"] = 4
        judgments[0]["critical_errors"] = ["unsafe action"]
        score = aggregate_judgments(judgments)
        self.assertEqual(score["dimensions"]["evidence_trace"], 3)
        self.assertEqual(score["critical_error_votes"], 1)
        self.assertEqual(score["critical_errors"], [])

    def test_aggregate_caps_when_critical_error_has_majority(self):
        judgments = [self._judgment(), self._judgment(), self._judgment()]
        judgments[0]["critical_errors"] = ["unsafe action"]
        judgments[1]["critical_errors"] = ["unsafe action"]
        score = aggregate_judgments(judgments)
        self.assertEqual(score["judge_score"], 49.0)

    def test_calibration_reports_error_and_critical_confusion(self):
        human = self._judgment()
        panel = [self._judgment(), self._judgment(), self._judgment()]
        report = calibration_report(
            {"examples": [{"human_judgment": human, "judge_judgments": panel}]}
        )
        self.assertEqual(report["status"], "insufficient_human_labels")
        self.assertEqual(report["dimensions"]["evidence_trace"]["mean_absolute_error"], 0.0)
        self.assertEqual(report["critical_error_confusion"]["true_negative"], 1)


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
