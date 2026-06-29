import unittest
from pathlib import Path

from mediabuyerbench.evaluator import load_case, validate_case
from mediabuyerbench.rubric import (
    compute_findings,
    load_criteria_library,
    score_response_rubric,
    summarize_rubric,
    validate_rubric,
)

ROOT = Path(__file__).resolve().parent.parent
GOOGLE_CASE = ROOT / "cases" / "public_lite" / "google" / "search_term_waste_001.json"
GOOGLE_RESPONSE = ROOT / "examples" / "responses" / "google_search_term_waste_001.md"

WASTE_TERMS = {"cheap dog kennel seattle", "dog boarding jobs", "free dog sitting", "cat boarding seattle"}


class CriteriaLibraryTest(unittest.TestCase):
    def test_library_loads_and_is_indexed_by_id(self):
        library = load_criteria_library()
        self.assertIn("g.kw.zero_conversion_waste", library)
        self.assertEqual(library["g.kw.zero_conversion_waste"]["type"], "programmatic")

    def test_every_case_rubric_validates_against_library(self):
        for path in (ROOT / "cases" / "public_lite").rglob("*.json"):
            validate_rubric(load_case(path))  # raises on unknown criterion / bad data_check


class ComputeFindingsTest(unittest.TestCase):
    def test_data_check_derives_ground_truth_waste_terms(self):
        case = load_case(GOOGLE_CASE)
        findings = compute_findings(
            case,
            {"block": "Search terms, current period", "where": {"conversions": {"==": 0}, "clicks": {">=": 25}}, "select": "query"},
        )
        self.assertEqual(set(findings), WASTE_TERMS)
        self.assertNotIn("dog boarding near me", findings)  # converter must not be flagged

    def test_data_check_identifies_converters(self):
        case = load_case(GOOGLE_CASE)
        findings = compute_findings(
            case,
            {"block": "Search terms, current period", "where": {"conversions": {">=": 1}}, "select": "query"},
        )
        self.assertEqual(findings, ["dog boarding near me"])

    def test_unknown_block_raises_not_silently_empty(self):
        case = load_case(GOOGLE_CASE)
        with self.assertRaises(ValueError):
            compute_findings(case, {"block": "Nonexistent block", "select": "query"})

    def test_unknown_select_field_raises(self):
        case = load_case(GOOGLE_CASE)
        with self.assertRaises(ValueError):
            compute_findings(case, {"block": "Search terms, current period", "select": "not_a_field"})

    def test_ordering_operator_on_non_numeric_raises(self):
        case = load_case(GOOGLE_CASE)
        with self.assertRaises(ValueError):
            compute_findings(case, {"block": "Search terms, current period", "where": {"query": {">": 5}}, "select": "query"})

    def test_contains_operator(self):
        case = load_case(GOOGLE_CASE)
        findings = compute_findings(
            case,
            {"block": "Search terms, current period", "where": {"query": {"contains": "cat boarding"}}, "select": "query"},
        )
        self.assertEqual(findings, ["cat boarding seattle"])

    def test_in_operator_requires_list(self):
        case = load_case(GOOGLE_CASE)
        with self.assertRaises(ValueError):
            compute_findings(case, {"block": "Search terms, current period", "where": {"query": {"in": "jobs"}}, "select": "query"})


class ScoreResponseRubricTest(unittest.TestCase):
    def test_good_sample_scores_high_with_full_coverage(self):
        case = load_case(GOOGLE_CASE)
        result = score_response_rubric(case, GOOGLE_RESPONSE.read_text(encoding="utf-8"))
        self.assertGreaterEqual(result["rubric_score"], 95)
        self.assertEqual(result["penalty"], 0.0)
        waste = next(i for i in result["items"] if i["criterion"] == "g.kw.zero_conversion_waste")
        self.assertEqual(waste["coverage"], 1.0)  # all 4 wasteful terms named
        for item in result["items"]:
            self.assertIn("url", item["source"])  # full traceability

    def test_data_check_materially_affects_score(self):
        # Same response but WITHOUT naming the specific wasteful queries.
        case = load_case(GOOGLE_CASE)
        full = GOOGLE_RESPONSE.read_text(encoding="utf-8")
        without_terms = full
        for term in WASTE_TERMS:
            without_terms = without_terms.replace(term, "some irrelevant query")
        full_result = score_response_rubric(case, full)
        weak_result = score_response_rubric(case, without_terms)
        self.assertLess(weak_result["rubric_score"], full_result["rubric_score"])
        weak_item = next(i for i in weak_result["items"] if i["criterion"] == "g.kw.zero_conversion_waste")
        self.assertEqual(weak_item["coverage"], 0.0)
        # Diagnosis still earns partial credit, coverage half is lost.
        self.assertTrue(weak_item["satisfied"])

    def test_guardrail_negation_evasion_is_caught(self):
        # Negates the first listed phrase but commits the violation via another.
        case = load_case(GOOGLE_CASE)
        sneaky = "Do not raise the budget yet. As the first step, scale budget on the winners."
        result = score_response_rubric(case, sneaky)
        self.assertGreater(result["penalty"], 0)
        guardrail = next(i for i in result["items"] if i["expected"] == "avoid")
        self.assertEqual(guardrail["matched_phrase"], "scale budget")

    def test_guardrail_not_falsely_triggered_by_negation(self):
        case = load_case(GOOGLE_CASE)
        safe = "Do not raise the budget. Do not scale budget until the waste is fixed."
        result = score_response_rubric(case, safe)
        self.assertEqual(result["penalty"], 0.0)

    def test_unknown_criterion_raises(self):
        case = load_case(GOOGLE_CASE)
        case["expected"]["rubric"] = [{"criterion": "does.not.exist", "weight": 1}]
        with self.assertRaises(ValueError) as ctx:
            score_response_rubric(case, "anything")
        self.assertIn("does.not.exist", str(ctx.exception))

    def test_summary_includes_traceability_and_coverage(self):
        case = load_case(GOOGLE_CASE)
        summary = summarize_rubric(score_response_rubric(case, GOOGLE_RESPONSE.read_text(encoding="utf-8")))
        self.assertIn("Rubric score:", summary)
        self.assertIn("g.kw.zero_conversion_waste", summary)
        self.assertIn("coverage=", summary)
        self.assertIn("source:", summary)


class MultiCaseRubricTest(unittest.TestCase):
    def _score(self, case_rel, response_id):
        case = load_case(ROOT / "cases" / "public_lite" / case_rel)
        resp = (ROOT / "examples" / "responses" / f"{response_id}.md").read_text(encoding="utf-8")
        return score_response_rubric(case, resp)

    def test_meta_reference_scores_well(self):
        result = self._score("meta/creative_fatigue_001.json", "meta_creative_fatigue_001")
        self.assertGreaterEqual(result["rubric_score"], 80)
        self.assertEqual(result["penalty"], 0.0)

    def test_cross_channel_reference_scores_high_with_channel_coverage(self):
        result = self._score("cross_channel/platform_cpa_lies_001.json", "cross_channel_platform_cpa_lies_001")
        self.assertGreaterEqual(result["rubric_score"], 90)
        protect = next(i for i in result["items"] if i["criterion"] == "gen.diagnosis.preserve_converters")
        self.assertEqual(protect["findings"], ["Google Search"])  # data-derived from CRM
        self.assertEqual(protect["coverage"], 1.0)

    def test_hard_case_data_check_isolates_underwater_campaign(self):
        case = load_case(ROOT / "cases" / "public_lite" / "google" / "blended_roas_trap_001.json")
        findings = compute_findings(
            case,
            {"block": "Account ROAS by campaign, last 30 days", "where": {"roas": {"<": 1.8}}, "select": "campaign"},
        )
        self.assertEqual(findings, ["Non-brand Search (prospecting)"])

    def test_hard_case_reference_scores_high(self):
        result = self._score("google/blended_roas_trap_001.json", "google_blended_roas_trap_001")
        self.assertGreaterEqual(result["rubric_score"], 90)
        self.assertEqual(result["penalty"], 0.0)

    def test_hard_case_penalizes_scaling_on_blended(self):
        case = load_case(ROOT / "cases" / "public_lite" / "google" / "blended_roas_trap_001.json")
        bad = "Blended ROAS is above target, so yes, scale spend 50% next month across all campaigns."
        result = score_response_rubric(case, bad)
        self.assertGreater(result["penalty"], 0)
        self.assertLess(result["rubric_score"], 50)


class ValidateCaseRubricTest(unittest.TestCase):
    def _case_with_rubric(self, rubric):
        return {
            "id": "c1", "title": "t", "provider": "google_ads", "category": "search",
            "difficulty": "easy", "business": {}, "user_prompt": "p", "data": [],
            "expected": {"required_concepts": [{"id": "a", "phrases": ["x"]}], "rubric": rubric},
        }

    def test_valid_rubric_passes_structural_validation(self):
        validate_case(self._case_with_rubric([{"criterion": "g.kw.zero_conversion_waste", "expected": "flag"}]))

    def test_rubric_item_missing_criterion_raises(self):
        with self.assertRaises(ValueError):
            validate_case(self._case_with_rubric([{"expected": "flag"}]))

    def test_rubric_item_bad_expected_raises(self):
        with self.assertRaises(ValueError):
            validate_case(self._case_with_rubric([{"criterion": "x", "expected": "bogus"}]))

    def test_data_check_missing_block_raises(self):
        with self.assertRaises(ValueError):
            validate_case(self._case_with_rubric([{"criterion": "x", "data_check": {"select": "query"}}]))


if __name__ == "__main__":
    unittest.main()
