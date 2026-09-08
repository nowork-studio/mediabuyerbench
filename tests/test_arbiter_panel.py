import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import run_arbiter_panel as panel

from scripts.run_arbiter_panel import (
    DEFAULT_SUITE,
    JUDGES,
    JudgeConfig,
    anonymize_candidates,
    build_panel_prompt,
    combine_panel_and_safety,
    compute_judgment_cache_digest,
    load_candidate_manifest,
    load_suite,
    run_or_reuse_judgment,
    validate_judgment_case_ids,
    extract_case_response,
    parse_json_output,
)


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

    def test_default_panel_uses_three_model_families(self):
        families = {config.family for config in JUDGES.values()}
        self.assertEqual(families, {"openai", "anthropic", "google"})

    def test_default_suite_advertises_five_runs_per_case(self):
        suite = load_suite(DEFAULT_SUITE)
        self.assertEqual(
            suite["candidate_protocol"]["runs_per_model_per_case"], 5
        )

    def test_default_suite_requires_recorded_prompt_limits_and_retry_policy(self):
        suite = load_suite(DEFAULT_SUITE)
        with self.assertRaisesRegex(ValueError, "prompt_sha256"):
            panel._validate_recorded_harness(
                suite,
                {
                    "tools": "none",
                    "external_research": "disallowed",
                    "same_prompt_and_limits": True,
                },
            )
        harness = panel._validate_recorded_harness(
            suite,
            {
                "tools": "none",
                "external_research": "disallowed",
                "same_prompt_and_limits": True,
                "prompt_sha256": "a" * 64,
                "limits": {"max_output_tokens": 4000},
                "retry_policy": "one transport retry",
            },
        )
        self.assertEqual(harness["prompt_sha256"], "a" * 64)

    def test_anonymization_hides_source_model_names_and_changes_mapping_by_judge(self):
        candidates = {
            "gpt-5.6-sol": "GPT response",
            "claude-opus-5": "Claude response",
            "gemini-3.1-pro": "Gemini response",
        }
        first, first_map = anonymize_candidates(candidates, "case-1", "judge-a")
        second, second_map = anonymize_candidates(candidates, "case-1", "judge-b")

        self.assertEqual(set(first), {"candidate-a", "candidate-b", "candidate-c"})
        self.assertNotIn("gpt", " ".join(first))
        self.assertEqual(set(first_map.values()), set(candidates))
        self.assertEqual(set(second_map.values()), set(candidates))
        self.assertNotEqual(first_map, second_map)

    def test_panel_prompt_reports_format_check_per_candidate(self):
        prompt = build_panel_prompt(
            {"id": "case", "title": "Case", "business": {}, "user_prompt": "Do work", "data": []},
            {
                "candidate-a": "Diagnosis only",
                "candidate-b": "Diagnosis\nDecisive evidence",
            },
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
        self.assertIn("candidate-a format check", prompt)
        self.assertIn("candidate-b format check", prompt)

    def test_manifest_requires_exact_suite_run_count(self):
        suite = {
            "id": "suite-v1",
            "case_ids": ["one"],
            "candidate_protocol": {
                "runs_per_model_per_case": 5,
                "harness_requirements": {
                    "tools": "none",
                    "external_research": "disallowed",
                },
            },
            "case_split": "unused",
        }
        with tempfile.TemporaryDirectory() as directory:
            candidate_dir = Path(directory)
            (candidate_dir / "run-1.md").write_text("CASE one\nResponse\n", encoding="utf-8")
            (candidate_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "suite_id": "suite-v1",
                        "harness": {
                            "tools": "none",
                            "external_research": "disallowed",
                        },
                        "runs": [
                            {
                                "model_id": "model-a",
                                "runtime": "runtime-a",
                                "run_id": "run-1",
                                "response_file": "run-1.md",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "exactly 5"):
                load_candidate_manifest(candidate_dir, suite)

    def test_five_runs_are_grouped_into_one_cohort_with_five_case_results(self):
        source_case = Path(
            panel.ROOT / "cases" / "public_lite" / "google" / "retrieval_scope_001.json"
        )
        case_id = json.loads(source_case.read_text(encoding="utf-8"))["id"]
        suite = {
            "id": "suite-v1",
            "case_ids": [case_id],
            "candidate_protocol": {
                "runs_per_model_per_case": 5,
                "harness_requirements": {
                    "tools": "none",
                    "external_research": "disallowed",
                },
            },
            "case_split": "unused",
        }
        with tempfile.TemporaryDirectory() as directory:
            candidate_dir = Path(directory)
            response = f"CASE {case_id}\nDiagnosis\n"
            runs = []
            for index in range(5):
                response_file = f"run-{index + 1}.md"
                (candidate_dir / response_file).write_text(response, encoding="utf-8")
                runs.append(
                    {
                        "model_id": "model-a",
                        "runtime": "runtime-a",
                        "run_id": f"run-{index + 1}",
                        "response_file": response_file,
                    }
                )
            (candidate_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "suite_id": "suite-v1",
                        "harness": {
                            "tools": "none",
                            "external_research": "disallowed",
                        },
                        "runs": runs,
                    }
                ),
                encoding="utf-8",
            )
            _, cohorts = load_candidate_manifest(candidate_dir, suite)
            cohort = cohorts["model-a + runtime-a"]
            self.assertEqual(cohort["run_count"], 5)
            self.assertEqual(
                [run["run_id"] for run in cohort["runs"]],
                [f"run-{index}" for index in range(1, 6)],
            )
            self.assertEqual(cohort["case_ids"], [case_id])

    def test_panel_summary_contains_five_results_per_case_for_one_cohort(self):
        source_case = Path(
            panel.ROOT / "cases" / "public_lite" / "google" / "retrieval_scope_001.json"
        )
        case_id = json.loads(source_case.read_text(encoding="utf-8"))["id"]
        suite = {
            "id": "suite-v1",
            "case_ids": [case_id],
            "candidate_protocol": {
                "runs_per_model_per_case": 5,
                "harness_requirements": {
                    "tools": "none",
                    "external_research": "disallowed",
                },
            },
            "case_split": "cases",
        }

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidate_dir = root / "candidates"
            case_dir = root / "cases"
            output_dir = root / "output"
            candidate_dir.mkdir()
            case_dir.mkdir()
            (case_dir / "case.json").write_text(
                source_case.read_text(encoding="utf-8"), encoding="utf-8"
            )
            runs = []
            for index in range(5):
                response_file = f"run-{index + 1}.md"
                (candidate_dir / response_file).write_text(
                    f"CASE {case_id}\nDiagnosis\n", encoding="utf-8"
                )
                runs.append(
                    {
                        "model_id": "model-a",
                        "runtime": "runtime-a",
                        "run_id": f"run-{index + 1}",
                        "response_file": response_file,
                    }
                )
            (candidate_dir / "manifest.json").write_text(
                json.dumps(
                    {
                        "suite_id": "suite-v1",
                        "harness": {
                            "tools": "none",
                            "external_research": "disallowed",
                        },
                        "runs": runs,
                    }
                ),
                encoding="utf-8",
            )
            (root / "suite.json").write_text(json.dumps(suite), encoding="utf-8")

            def fake_judge(judge_id, prompt):
                labels = re.findall(
                    r"^## Anonymous candidate \((candidate-[a-z]+)\)$",
                    prompt,
                    re.MULTILINE,
                )
                return json.dumps(
                    {
                        "judgments": [
                            {"candidate_id": label, "case_id": case_id}
                            for label in labels
                        ]
                    }
                )

            def fake_aggregate(judgments, rubric):
                return {
                    "case_id": case_id,
                    "judge_score": 80,
                    "methodology_pass": True,
                    "critical_errors": [],
                }

            def fake_combine(case, response, panel_score):
                return {
                    **panel_score,
                    "safe_completion": True,
                    "decision_safety": {
                        "passed": True,
                        "critical_gate_failures": [],
                    },
                }

            with patch.object(panel, "run_judge", side_effect=fake_judge), patch.object(
                panel, "validate_judgment", return_value=None
            ), patch.object(panel, "aggregate_judgments", side_effect=fake_aggregate), patch.object(
                panel, "combine_panel_and_safety", side_effect=fake_combine
            ):
                self.assertEqual(
                    panel.main(
                        [
                            "--candidate-dir",
                            str(candidate_dir),
                            "--suite",
                            str(root / "suite.json"),
                            "--case-dir",
                            str(case_dir),
                            "--output-dir",
                            str(output_dir),
                            "--judge",
                            "gpt-5.6-sol",
                            "--judge",
                            "claude-opus-5",
                            "--judge",
                            "gemini-3.1-pro-high",
                        ]
                    ),
                    0,
                )

            summary = json.loads((output_dir / "summary.json").read_text(encoding="utf-8"))
            results = summary["case_panels"]["model-a + runtime-a"]
            self.assertEqual(len(results), 5)
            self.assertEqual([result["case_id"] for result in results], [case_id] * 5)
            self.assertEqual(
                summary["protocol_verification"]["cohorts"]["model-a + runtime-a"][
                    "results_per_case"
                ][case_id],
                5,
            )

    def test_judgment_cache_reuses_only_an_exact_input_digest(self):
        calls = []

        def fake_runner(judge_id, prompt):
            calls.append((judge_id, prompt))
            return json.dumps({"judgments": []})

        with tempfile.TemporaryDirectory() as directory:
            raw_path = Path(directory) / "case.txt"
            mapping = {"candidate-a": "run-1"}
            raw, reused = run_or_reuse_judgment(
                "gpt-5.6-sol", "prompt-a", raw_path, mapping, fake_runner
            )
            self.assertFalse(reused)
            self.assertEqual(len(calls), 1)
            self.assertEqual(raw, json.dumps({"judgments": []}))
            _, reused = run_or_reuse_judgment(
                "gpt-5.6-sol", "prompt-a", raw_path, mapping, fake_runner
            )
            self.assertTrue(reused)
            self.assertEqual(len(calls), 1)
            _, reused = run_or_reuse_judgment(
                "gpt-5.6-sol", "prompt-b", raw_path, mapping, fake_runner
            )
            self.assertFalse(reused)
            self.assertEqual(len(calls), 2)
            _, reused = run_or_reuse_judgment(
                "gpt-5.6-sol", "prompt-b", raw_path, {"candidate-a": "run-2"}, fake_runner
            )
            self.assertFalse(reused)
            self.assertEqual(len(calls), 3)
            original_config = panel.JUDGES["gpt-5.6-sol"]
            panel.JUDGES["gpt-5.6-sol"] = JudgeConfig(
                original_config.runner, "changed-model", original_config.family
            )
            try:
                _, reused = run_or_reuse_judgment(
                    "gpt-5.6-sol",
                    "prompt-b",
                    raw_path,
                    {"candidate-a": "run-2"},
                    fake_runner,
                )
            finally:
                panel.JUDGES["gpt-5.6-sol"] = original_config
            self.assertFalse(reused)
            self.assertEqual(len(calls), 4)
            _, reused = run_or_reuse_judgment(
                "gpt-5.6-sol",
                "prompt-b",
                raw_path,
                {"candidate-a": "run-2"},
                fake_runner,
            )
            self.assertFalse(reused)
            self.assertEqual(len(calls), 5)
            self.assertEqual(
                panel.judgment_cache_digest_path(raw_path)
                .read_text(encoding="utf-8")
                .strip(),
                compute_judgment_cache_digest(
                    "prompt-b", "gpt-5.6-sol", {"candidate-a": "run-2"}
                ),
            )

    def test_judgment_case_id_must_match_scored_case(self):
        with self.assertRaisesRegex(ValueError, "expected 'case-1'"):
            validate_judgment_case_ids(
                [{"candidate_id": "candidate-a", "case_id": "case-2"}],
                "case-1",
                "judge-a",
            )

    def test_safe_completion_requires_deterministic_and_judge_pass(self):
        case = {
            "id": "case",
            "expected": {
                "safety_gates": [
                    {
                        "id": "no_scale",
                        "type": "contains_none",
                        "phrases": ["increase budget"],
                        "severity": "critical",
                    }
                ]
            },
        }
        panel = {
            "case_id": "case",
            "methodology_pass": True,
            "critical_errors": [],
        }
        combined = combine_panel_and_safety(case, "Increase budget today.", panel)
        self.assertFalse(combined["safe_completion"])
        self.assertEqual(
            combined["decision_safety"]["critical_gate_failures"][0]["id"],
            "no_scale",
        )
