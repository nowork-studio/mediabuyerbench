import unittest
from pathlib import Path

from mediabuyerbench.evaluator import load_case
from mediabuyerbench.rubric import (
    compute_findings,
    load_criteria_library,
    score_response_rubric,
    summarize_rubric,
)

ROOT = Path(__file__).resolve().parent.parent
GOOGLE_CASE = ROOT / "cases" / "public_lite" / "google" / "search_term_waste_001.json"
GOOGLE_RESPONSE = ROOT / "examples" / "responses" / "google_search_term_waste_001.md"


class CriteriaLibraryTest(unittest.TestCase):
    def test_library_loads_and_is_indexed_by_id(self):
        library = load_criteria_library()
        self.assertIn("g.kw.zero_conversion_waste", library)
        self.assertEqual(library["g.kw.zero_conversion_waste"]["type"], "programmatic")

    def test_every_case_rubric_references_an_existing_criterion(self):
        library = load_criteria_library()
        for path in (ROOT / "cases" / "public_lite").rglob("*.json"):
            case = load_case(path)
            for item in case.get("expected", {}).get("rubric", []):
                self.assertIn(
                    item["criterion"],
                    library,
                    f"{case['id']} references unknown criterion {item['criterion']}",
                )


class ComputeFindingsTest(unittest.TestCase):
    def test_data_check_derives_ground_truth_waste_terms(self):
        case = load_case(GOOGLE_CASE)
        data_check = {
            "block": "Search terms, current period",
            "where": {"conversions": {"==": 0}, "clicks": {">=": 25}},
            "select": "query",
        }
        findings = compute_findings(case, data_check)
        self.assertEqual(
            set(findings),
            {"cheap dog kennel seattle", "dog boarding jobs", "free dog sitting", "cat boarding seattle"},
        )
        # The converting query must NOT be flagged as waste.
        self.assertNotIn("dog boarding near me", findings)

    def test_data_check_identifies_converters(self):
        case = load_case(GOOGLE_CASE)
        findings = compute_findings(
            case,
            {"block": "Search terms, current period", "where": {"conversions": {">=": 1}}, "select": "query"},
        )
        self.assertEqual(findings, ["dog boarding near me"])


class ScoreResponseRubricTest(unittest.TestCase):
    def test_good_sample_scores_high(self):
        case = load_case(GOOGLE_CASE)
        result = score_response_rubric(case, GOOGLE_RESPONSE.read_text(encoding="utf-8"))
        self.assertGreaterEqual(result["rubric_score"], 90)
        self.assertEqual(result["penalty"], 0.0)
        # Every rubric item is traceable to a sourced criterion.
        for item in result["items"]:
            self.assertIn("url", item["source"])

    def test_guardrail_violation_penalizes(self):
        case = load_case(GOOGLE_CASE)
        bad = "CPA got worse so we should raise the budget to win more auctions."
        result = score_response_rubric(case, bad)
        self.assertGreater(result["penalty"], 0)
        self.assertLess(result["rubric_score"], 60)

    def test_findings_reported_as_ground_truth(self):
        case = load_case(GOOGLE_CASE)
        result = score_response_rubric(case, GOOGLE_RESPONSE.read_text(encoding="utf-8"))
        waste_item = next(i for i in result["items"] if i["criterion"] == "g.kw.zero_conversion_waste")
        self.assertEqual(len(waste_item["findings"]), 4)

    def test_unknown_criterion_raises(self):
        case = load_case(GOOGLE_CASE)
        case["expected"]["rubric"] = [{"criterion": "does.not.exist", "weight": 1}]
        with self.assertRaises(ValueError) as ctx:
            score_response_rubric(case, "anything")
        self.assertIn("does.not.exist", str(ctx.exception))

    def test_summary_includes_traceability(self):
        case = load_case(GOOGLE_CASE)
        summary = summarize_rubric(score_response_rubric(case, GOOGLE_RESPONSE.read_text(encoding="utf-8")))
        self.assertIn("Rubric score:", summary)
        self.assertIn("g.kw.zero_conversion_waste", summary)
        self.assertIn("source:", summary)


if __name__ == "__main__":
    unittest.main()
