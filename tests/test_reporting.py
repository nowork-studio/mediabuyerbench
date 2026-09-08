import unittest

from mediabuyerbench.reporting import summarize_panel_results, wilson_interval


def _protocol_for(case_ids=("one", "two")):
    cohorts = {}
    for model_id, runtime, run_id in (
        ("model-a", "runtime-a", "a-1"),
        ("model-b", "runtime-b", "b-1"),
    ):
        cohort_id = f"{model_id} + {runtime}"
        cohorts[cohort_id] = {
            "model_id": model_id,
            "runtime": runtime,
            "run_ids": [run_id],
            "run_count": 1,
            "case_ids": list(case_ids),
            "results_per_case": {case_id: 1 for case_id in case_ids},
        }
    return {
        "verified": True,
        "suite_id": "suite-v1",
        "case_ids": list(case_ids),
        "runs_per_model_per_case": 1,
        "required_harness": {"tools": "none"},
        "recorded_harness": {"tools": "none"},
        "cohorts": cohorts,
    }


class ReportingTest(unittest.TestCase):
    def test_wilson_interval_handles_empty_sample(self):
        self.assertEqual(wilson_interval(0, 0), [0.0, 1.0])

    def test_summarizes_safety_rates_with_confidence_intervals(self):
        payload = {
            "case_panels": {
                "model-a + runtime-a": [
                    {
                        "case_id": "one",
                        "model_id": "model-a",
                        "runtime": "runtime-a",
                        "judge_score": 90,
                        "methodology_pass": True,
                        "critical_errors": [],
                        "safe_completion": True,
                        "decision_safety": {
                            "passed": True,
                            "critical_gate_failures": [],
                        },
                    },
                    {
                        "case_id": "two",
                        "model_id": "model-a",
                        "runtime": "runtime-a",
                        "judge_score": 40,
                        "methodology_pass": False,
                        "critical_errors": ["unsafe"],
                        "safe_completion": False,
                        "decision_safety": {
                            "passed": False,
                            "critical_gate_failures": [{"id": "unsafe"}],
                        },
                    },
                ],
                "model-b + runtime-b": [
                    {
                        "case_id": "one",
                        "run_id": "b-1",
                        "model_id": "model-b",
                        "runtime": "runtime-b",
                        "judge_score": 75,
                        "methodology_pass": True,
                        "critical_errors": [],
                        "safe_completion": False,
                        "decision_safety": {
                            "passed": False,
                            "critical_gate_failures": [{"id": "unsafe_action"}],
                        },
                    },
                    {
                        "case_id": "two",
                        "run_id": "b-1",
                        "model_id": "model-b",
                        "runtime": "runtime-b",
                        "judge_score": 65,
                        "methodology_pass": False,
                        "critical_errors": ["unsafe"],
                        "safe_completion": False,
                        "decision_safety": {
                            "passed": False,
                            "critical_gate_failures": [{"id": "unsafe_action"}],
                        },
                    },
                ],
            },
            "suite_id": "suite-v1",
            "protocol_verification": _protocol_for(),
        }
        payload["case_panels"]["model-a + runtime-a"][0]["run_id"] = "a-1"
        payload["case_panels"]["model-a + runtime-a"][1]["run_id"] = "a-1"

        report = summarize_panel_results(payload)
        model = report["models"]["model-a + runtime-a"]
        self.assertEqual(model["responses"], 2)
        self.assertEqual(model["tasks_completed_safely"]["count"], 1)
        self.assertEqual(model["tasks_completed_safely"]["rate"], 0.5)
        self.assertEqual(model["responses_with_serious_errors"]["count"], 1)
        self.assertEqual(model["median_judge_score"], 65.0)
        self.assertEqual(report["models"]["model-b + runtime-b"]["tasks_completed_safely"]["count"], 0)
        self.assertEqual(report["models"]["model-b + runtime-b"]["responses_with_serious_errors"]["count"], 2)
        self.assertEqual(report["claim_scope"], "source-grounded decision safety under the recorded harness")

    def test_rejects_missing_case_panels(self):
        with self.assertRaisesRegex(ValueError, "case_panels"):
            summarize_panel_results({})

    def test_rejects_missing_protocol_verification(self):
        payload = {
            "suite_id": "suite-v1",
            "case_panels": {
                "model-a + runtime-a": [
                    {
                        "case_id": "one",
                        "run_id": "a-1",
                        "model_id": "model-a",
                        "runtime": "runtime-a",
                        "methodology_pass": True,
                        "safe_completion": True,
                        "decision_safety": {
                            "passed": True,
                            "critical_gate_failures": [],
                        },
                    }
                ]
            },
        }
        with self.assertRaisesRegex(ValueError, "protocol_verification"):
            summarize_panel_results(payload)

    def test_rejects_unequal_case_coverage(self):
        payload = {
            "suite_id": "suite-v1",
            "case_panels": {
                "model-a + runtime-a": [
                    {
                        "case_id": "one",
                        "run_id": "a-1",
                        "model_id": "model-a",
                        "runtime": "runtime-a",
                        "methodology_pass": True,
                        "safe_completion": True,
                        "decision_safety": {
                            "passed": True,
                            "critical_gate_failures": [],
                        },
                    },
                    {
                        "case_id": "two",
                        "run_id": "a-1",
                        "model_id": "model-a",
                        "runtime": "runtime-a",
                        "methodology_pass": True,
                        "safe_completion": True,
                        "decision_safety": {
                            "passed": True,
                            "critical_gate_failures": [],
                        },
                    },
                ],
                "model-b + runtime-b": [
                    {
                        "case_id": "one",
                        "run_id": "b-1",
                        "model_id": "model-b",
                        "runtime": "runtime-b",
                        "methodology_pass": True,
                        "safe_completion": True,
                        "decision_safety": {
                            "passed": True,
                            "critical_gate_failures": [],
                        },
                    },
                    {
                        "case_id": "one",
                        "run_id": "b-1",
                        "model_id": "model-b",
                        "runtime": "runtime-b",
                        "methodology_pass": True,
                        "safe_completion": True,
                        "decision_safety": {
                            "passed": True,
                            "critical_gate_failures": [],
                        },
                    },
                ],
            },
            "protocol_verification": _protocol_for(),
        }
        with self.assertRaisesRegex(ValueError, "unequal case coverage"):
            summarize_panel_results(payload)

    def test_methodology_pass_alone_cannot_count_as_safe(self):
        payload = {
            "case_panels": {
                "model-a": [
                    {
                        "case_id": "one",
                        "methodology_pass": True,
                    }
                ]
            }
        }

        with self.assertRaisesRegex(ValueError, "safe_completion"):
            summarize_panel_results(payload)

    def test_rejects_malformed_safety_records(self):
        malformed_results = [
            {
                "case_id": "non_boolean_safe_completion",
                "safe_completion": "true",
                "decision_safety": {
                    "passed": True,
                    "critical_gate_failures": [],
                },
            },
            {
                "case_id": "missing_decision_safety",
                "safe_completion": True,
            },
            {
                "case_id": "non_boolean_passed",
                "safe_completion": True,
                "decision_safety": {
                    "passed": 1,
                    "critical_gate_failures": [],
                },
            },
            {
                "case_id": "non_array_failures",
                "safe_completion": True,
                "decision_safety": {
                    "passed": True,
                    "critical_gate_failures": {},
                },
            },
        ]

        for result in malformed_results:
            with self.subTest(case_id=result["case_id"]), self.assertRaisesRegex(
                ValueError, "case_panels.model-a"
            ):
                summarize_panel_results({"case_panels": {"model-a": [result]}})

    def test_rejects_safe_completion_that_disagrees_with_its_gates(self):
        protocol = _protocol_for(("one",))
        del protocol["cohorts"]["model-b + runtime-b"]
        payload = {
            "suite_id": "suite-v1",
            "protocol_verification": protocol,
            "case_panels": {
                "model-a + runtime-a": [
                    {
                        "case_id": "one",
                        "run_id": "a-1",
                        "model_id": "model-a",
                        "runtime": "runtime-a",
                        "methodology_pass": True,
                        "safe_completion": True,
                        "decision_safety": {
                            "passed": False,
                            "critical_gate_failures": [{"id": "unsafe"}],
                        },
                    }
                ]
            },
        }

        with self.assertRaisesRegex(ValueError, "does not match"):
            summarize_panel_results(payload)
