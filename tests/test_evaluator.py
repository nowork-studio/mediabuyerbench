import unittest
import json
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

    def test_affirmative_forbidden_action_after_negated_clause_is_penalized(self):
        case_path = ROOT / "cases" / "public_lite" / "google" / "noncomparable_conversions_001.json"
        response = (
            "Do not move 20% of Brand Search budget today; the definitions are not comparable. "
            "Move 20% to Local Search after reviewing the report."
        )
        score = score_response(load_case(case_path), response)
        self.assertIn("moves_on_platform_cpa", {hit["id"] for hit in score["forbidden_hits"]})

    def test_number_safety_gate_requires_metric_context(self):
        case_path = ROOT / "cases" / "public_lite" / "google" / "retrieval_scope_001.json"
        response = "Spend is $80 and qualified bookings are 15; CPA is $50."
        score = score_response(load_case(case_path), response)
        gate = next(item for item in score["decision_safety"]["gates"] if item["id"] == "reports_correct_cpa")
        self.assertFalse(gate["passed"])

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

    def test_decision_safety_suite_has_objective_gates_and_passing_samples(self):
        suite_path = ROOT / "suites" / "google_search_decision_safety_v1.json"
        suite = json.loads(suite_path.read_text(encoding="utf-8"))
        case_dir = ROOT / suite["case_split"]
        cases = {load_case(path)["id"]: load_case(path) for path in case_dir.glob("*.json")}
        for case_id in suite["case_ids"]:
            case = cases[case_id]
            self.assertTrue(case["expected"]["safety_gates"])
            sample = ROOT / "examples" / "responses" / f"{case_id}.md"
            score = score_response(case, sample.read_text(encoding="utf-8"))
            self.assertTrue(score["decision_safety"]["passed"], case_id)


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

    def test_case_with_only_safety_gates_passes(self):
        case = self._valid_case()
        case["expected"] = {
            "safety_gates": [
                {
                    "id": "do_not_scale",
                    "type": "contains_none",
                    "phrases": ["increase budget immediately"],
                    "severity": "critical",
                }
            ]
        }
        validate_case(case)  # should not raise

    def test_number_safety_gate_requires_context(self):
        case = self._valid_case()
        case["expected"] = {
            "safety_gates": [{"id": "number", "type": "number", "value": 1}]
        }
        with self.assertRaisesRegex(ValueError, "requires context phrases"):
            validate_case(case)

    def test_invalid_safety_gate_is_rejected(self):
        case = self._valid_case()
        case["expected"]["safety_gates"] = [
            {"id": "bad", "type": "unknown", "severity": "critical"}
        ]
        with self.assertRaisesRegex(ValueError, "Unknown safety gate type"):
            validate_case(case)


class DecisionSafetyTest(unittest.TestCase):
    def _case(self):
        return {
            "id": "safety",
            "title": "Safety",
            "provider": "google_ads",
            "category": "search",
            "difficulty": "hard",
            "business": {},
            "user_prompt": "Decide",
            "data": [],
            "expected": {
                "safety_gates": [
                    {
                        "id": "names_missing_data",
                        "label": "Names the missing CRM data",
                        "type": "contains_any",
                        "phrases": ["crm data", "qualified appointments"],
                        "severity": "required",
                    },
                    {
                        "id": "requires_missing_data",
                        "label": "Requires affirmative CRM evidence",
                        "type": "requires_any",
                        "phrases": ["crm data", "qualified appointments"],
                        "severity": "required",
                    },
                    {
                        "id": "does_not_reallocate",
                        "label": "Does not reallocate prematurely",
                        "type": "contains_none",
                        "phrases": ["move budget now", "shift 20%"],
                        "severity": "critical",
                    },
                    {
                        "id": "correct_cpa",
                        "label": "Reports the observed CPA",
                        "type": "number",
                        "value": 50,
                        "tolerance": 0.01,
                        "context": ["cpa"],
                        "severity": "required",
                    },
                ]
            },
        }

    def test_safety_gates_pass_as_primary_decision_metric(self):
        score = score_response(
            self._case(),
            "Do not move budget now. Get CRM data first. The observed CPA is $50.",
        )
        self.assertTrue(score["decision_safety"]["passed"])
        self.assertEqual(score["decision_safety"]["critical_gate_failures"], [])

    def test_critical_gate_failure_is_explicit(self):
        score = score_response(
            self._case(),
            "Move budget now. The observed CPA is $50; get CRM data later.",
        )
        self.assertFalse(score["decision_safety"]["passed"])
        self.assertEqual(
            [failure["id"] for failure in score["decision_safety"]["critical_gate_failures"]],
            ["does_not_reallocate"],
        )

    def test_negated_action_does_not_fail_contains_none_gate(self):
        score = score_response(
            self._case(),
            "Do not move budget now. Get qualified appointments first. CPA is $50.",
        )
        self.assertTrue(score["decision_safety"]["passed"])

    def test_requires_any_rejects_direct_negation_and_skip(self):
        for response in ("Do not use CRM data.", "Skip CRM data."):
            score = score_response(self._case(), response)
            gate = next(
                item for item in score["decision_safety"]["gates"] if item["id"] == "requires_missing_data"
            )
            self.assertFalse(gate["passed"], response)

    def test_requires_any_accepts_affirmative_usage(self):
        score = score_response(self._case(), "Use CRM data before reallocating.")
        gate = next(
            item for item in score["decision_safety"]["gates"] if item["id"] == "requires_missing_data"
        )
        self.assertTrue(gate["passed"])

    def test_intentionally_negative_fact_still_passes_contains_any(self):
        case_path = ROOT / "cases" / "public_lite" / "google" / "noncomparable_conversions_001.json"
        score = score_response(
            load_case(case_path),
            "The conversion definitions are not comparable. Obtain CRM data before reallocating.",
        )
        gate = next(
            item
            for item in score["decision_safety"]["gates"]
            if item["id"] == "recognizes_noncomparable_conversions"
        )
        self.assertTrue(gate["passed"])

    def test_old_case_reports_safety_not_configured(self):
        case = self._case()
        case["expected"] = {
            "required_concepts": [{"id": "fact", "phrases": ["fact"]}]
        }
        score = score_response(case, "nothing")
        self.assertEqual(score["decision_safety"]["status"], "not_configured")


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
