import unittest

from mediabuyerbench.judge import (
    aggregate_quality_contract_assessments,
    render_quality_contract,
    score_quality_contract_assessment,
    validate_quality_contract_assessment,
)
from scripts.run_arbiter_panel import build_panel_prompt
from mediabuyerbench.judge import load_rubric


def _case():
    return {
        "id": "case-1",
        "title": "Hidden contract case",
        "provider": "google",
        "category": "decision_safety",
        "difficulty": "hard",
        "business": {"goal": "qualified leads"},
        "user_prompt": "Choose one action from the packet.",
        "data": [{"name": "facts", "rows": [{"spend": 100}]}],
        "expected": {
            "quality_contract": {
                "criteria": [
                    {
                        "id": "math",
                        "description": "Reports the required calculation",
                        "weight": 1,
                        "severity": "required",
                    },
                    {
                        "id": "safe_scope",
                        "description": "Keeps the action at the safe scope",
                        "weight": 1,
                        "severity": "critical",
                        "acceptable_alternatives": ["Recommend no mutation"],
                    },
                ]
            }
        },
    }


def _judgment(math="met", safe_scope="met"):
    return {
        "criterion_assessments": [
            {"criterion_id": "math", "rating": math, "evidence": "calculation"},
            {
                "criterion_id": "safe_scope",
                "rating": safe_scope,
                "evidence": "scope excerpt",
            },
        ]
    }


def _pillared_case():
    case = _case()
    case["expected"]["quality_contract"]["criteria"] = [
        {
            "id": "math",
            "description": "Reports the calculation",
            "weight": 25,
            "severity": "required",
            "pillar": "evidence_math",
        },
        {
            "id": "inference",
            "description": "Draws the supported inference",
            "weight": 25,
            "severity": "required",
            "pillar": "inference",
        },
        {
            "id": "action",
            "description": "Chooses the safe action",
            "weight": 25,
            "severity": "critical",
            "pillar": "action_safety",
        },
        {
            "id": "validation",
            "description": "Defines the validation rule",
            "weight": 25,
            "severity": "required",
            "pillar": "validation",
        },
    ]
    return case


def _pillared_judgment(**ratings):
    return {
        "criterion_assessments": [
            {
                "criterion_id": criterion_id,
                "rating": ratings.get(criterion_id, "met"),
                "evidence": criterion_id,
            }
            for criterion_id in ("math", "inference", "action", "validation")
        ]
    }


class QualityContractTest(unittest.TestCase):
    def test_scores_atomic_criteria_and_caps_critical_failure(self):
        complete = score_quality_contract_assessment(_judgment(), _case())
        self.assertEqual(complete["score"], 100.0)
        failed = score_quality_contract_assessment(
            _judgment(math="met", safe_scope="not_met"), _case()
        )
        self.assertEqual(failed["raw_score"], 50.0)
        self.assertEqual(failed["score"], 49.0)
        self.assertEqual(failed["critical_failures"][0]["criterion_id"], "safe_scope")

    def test_requires_exactly_one_assessment_per_criterion(self):
        with self.assertRaisesRegex(ValueError, "missing criterion assessments"):
            validate_quality_contract_assessment(
                {"criterion_assessments": [_judgment()["criterion_assessments"][0]]},
                _case(),
            )

    def test_aggregates_each_criterion_by_panel_majority(self):
        result = aggregate_quality_contract_assessments(
            [
                _judgment(math="met", safe_scope="met"),
                _judgment(math="met", safe_scope="not_met"),
                _judgment(math="not_met", safe_scope="met"),
            ],
            _case(),
        )
        self.assertEqual(result["score"], 100.0)
        self.assertEqual(result["rating_votes"]["math"]["met"], 2)

    def test_integrated_score_requires_every_essential_pillar(self):
        complete = score_quality_contract_assessment(
            _pillared_judgment(), _pillared_case()
        )
        self.assertEqual(complete["score"], 100.0)
        self.assertEqual(
            complete["scoring_method"],
            "atomic_coverage_times_weakest_pillar",
        )

        missing_validation = score_quality_contract_assessment(
            _pillared_judgment(validation="not_met"), _pillared_case()
        )
        self.assertEqual(missing_validation["atomic_coverage_score"], 75.0)
        self.assertEqual(missing_validation["weakest_pillar_score"], 0.0)
        self.assertEqual(missing_validation["score"], 0.0)

    def test_pillar_contract_rejects_partial_or_unknown_pillars(self):
        case = _pillared_case()
        case["expected"]["quality_contract"]["criteria"][0]["pillar"] = "unknown"
        with self.assertRaisesRegex(ValueError, "pillar must be one of"):
            score_quality_contract_assessment(_pillared_judgment(), case)

        case = _pillared_case()
        del case["expected"]["quality_contract"]["criteria"][0]["pillar"]
        with self.assertRaisesRegex(ValueError, "pillar must be one of"):
            score_quality_contract_assessment(_pillared_judgment(), case)

    def test_panel_prompt_shows_contract_to_judge_but_not_as_candidate_packet(self):
        prompt = build_panel_prompt(
            _case(), {"candidate-a": "No calculation."}, load_rubric()
        )
        self.assertIn("Case-specific quality contract (judge only)", prompt)
        self.assertIn("safe_scope", prompt)
        self.assertIn("criterion_assessments", prompt)
        contract = render_quality_contract(_case())
        self.assertIn("Recommend no mutation", contract)


if __name__ == "__main__":
    unittest.main()
