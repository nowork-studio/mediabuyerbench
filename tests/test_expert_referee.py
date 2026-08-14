import importlib.util
import json
import unittest
from pathlib import Path

from mediabuyerbench.expert_referee import (
    build_reference_prompt,
    build_review_prompt,
    load_source_pack,
    score_expert_review,
    validate_expert_review,
    validate_reference,
)


ROOT = Path(__file__).resolve().parent.parent


def _load_runner_module():
    spec = importlib.util.spec_from_file_location(
        "run_google_search_certification", ROOT / "scripts" / "run_google_search_certification.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ExpertRefereeTest(unittest.TestCase):
    def setUp(self):
        self.case = {
            "id": "case_1",
            "title": "Case",
            "provider": "google_ads",
            "category": "testing",
            "difficulty": "expert",
            "business": {"goal": "qualified leads"},
            "user_prompt": "What should we do?",
            "data": [],
            "expected": {"required_concepts": [{"id": "x", "phrases": ["x"]}]},
        }
        self.pack = load_source_pack()

    def _reference(self):
        return {
            "case_id": "case_1",
            "source_pack_id": self.pack["id"],
            "source_pack_version": self.pack["version"],
            "reference_decision": {
                "decision": "Hold until the signal is valid.",
                "acceptable_alternatives": ["Run a narrow audit first."],
                "decisive_packet_facts": ["The packet has no mature cohort."],
                "material_uncertainties": ["The downstream conversion is incomplete."],
                "prerequisite_gates": ["Reconcile the conversion source."],
                "smallest_safe_action": {"scope": "one campaign", "action": "audit the import"},
                "validation": {
                    "outcome": "qualified leads",
                    "denominator": "mature clicks",
                    "window": "21 days",
                    "comparison": "pre-change mature cohort",
                    "go": "change only after stable measurement",
                    "no_go": "hold while data is incomplete",
                },
                "source_ids": ["google_reporting_discrepancies"],
                "confidence": "medium",
            },
            "rationale": "The safe action depends on measurement integrity.",
        }

    def _review(self):
        evidence = {
            dimension: {
                "candidate_excerpt": "Hold and audit the import.",
                "packet_facts": ["No mature cohort is reported."],
                "reference_alignment": "Matches the reference's safe sequence.",
                "source_ids": ["google_reporting_discrepancies"],
            }
            for dimension in (
                "evidence_trace",
                "causal_discipline",
                "preconditions_and_sequence",
                "intervention_scope",
                "decision_rule",
                "alternatives_and_tradeoffs",
            )
        }
        return {
            "case_id": "case_1",
            "source_pack_id": self.pack["id"],
            "source_pack_version": self.pack["version"],
            "reference_decision_id": "case_1:google_search_certification_v1:v1",
            "verdict": "pass",
            "evidence_trace": 4,
            "causal_discipline": 4,
            "preconditions_and_sequence": 4,
            "intervention_scope": 4,
            "decision_rule": 4,
            "alternatives_and_tradeoffs": 4,
            "critical_errors": [],
            "methodology_failures": [],
            "evidence": evidence,
            "rationale": "The candidate follows the reference decision.",
        }

    def test_reference_prompt_is_grounded_in_the_source_pack(self):
        prompt = build_reference_prompt(self.case, self.pack)
        self.assertIn("Source pack: google_search_operator_sources_v1", prompt)
        self.assertIn("Google Ads Help", prompt)
        self.assertIn("before any candidate response is shown", prompt)

    def test_review_prompt_contains_frozen_reference_and_anonymous_candidate(self):
        prompt = build_review_prompt(self.case, "Candidate response", self._reference(), self.pack)
        self.assertIn("Frozen expert reference decision", prompt)
        self.assertIn("Anonymous candidate response", prompt)
        self.assertIn("candidate_excerpt", prompt)

    def test_scores_a_valid_expert_review(self):
        score = score_expert_review(self._review(), self.case, self.pack)
        self.assertEqual(score["expert_score"], 100.0)
        self.assertTrue(score["methodology_pass"])

    def test_critical_error_caps_expert_score(self):
        review = self._review()
        review["critical_errors"] = ["Unsafe change before measurement integrity."]
        score = score_expert_review(review, self.case, self.pack)
        self.assertEqual(score["expert_score"], 49.0)

    def test_unknown_source_id_is_rejected(self):
        review = self._review()
        review["evidence"]["decision_rule"]["source_ids"] = ["made_up_source"]
        with self.assertRaisesRegex(ValueError, "known source_ids"):
            validate_expert_review(review, self.case, self.pack)

    def test_reference_requires_known_source_id(self):
        reference = self._reference()
        reference["reference_decision"]["source_ids"] = ["made_up_source"]
        with self.assertRaisesRegex(ValueError, "known source IDs"):
            validate_reference(reference, self.case, self.pack)

    def test_public_suite_declares_exact_case_files(self):
        runner = _load_runner_module()
        suite = runner.load_suite(ROOT / "suites" / "google_search_public_demo_v1.json")
        case_dir = ROOT / suite["case_split"]
        found = {json.loads(path.read_text())["id"] for path in case_dir.glob("*.json")}
        self.assertEqual(found, set(suite["case_ids"]))
        self.assertEqual(suite["source_pack_id"], self.pack["id"])
