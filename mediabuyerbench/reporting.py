from __future__ import annotations

import math
from collections import Counter
from statistics import median
from typing import Any


def wilson_interval(successes: int, total: int, z: float = 1.96) -> list[float]:
    """Return a 95% Wilson score interval for a binomial rate."""
    if total < 0 or successes < 0 or successes > total:
        raise ValueError("successes and total must satisfy 0 <= successes <= total")
    if total == 0:
        return [0.0, 1.0]
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    margin = (
        z
        * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return [round(max(0.0, center - margin), 3), round(min(1.0, center + margin), 3)]


def _rate_summary(count: int, total: int) -> dict[str, Any]:
    return {
        "count": count,
        "rate": round(count / total, 3) if total else 0.0,
        "confidence_interval_95": wilson_interval(count, total),
    }


def is_serious_error(result: dict[str, Any]) -> bool:
    """Return whether either the objective gates or judge panel found a critical error."""
    return bool(result.get("critical_errors")) or bool(
        result.get("decision_safety", {}).get("critical_gate_failures")
    )


def _validate_safety_result(
    result: dict[str, Any], model_id: str, result_index: int
) -> None:
    """Require the explicit safety fields used by the headline completion metric."""
    path = f"case_panels.{model_id}[{result_index}]"
    if not isinstance(result.get("safe_completion"), bool):
        raise ValueError(f"{path}.safe_completion must be a boolean")

    decision_safety = result.get("decision_safety")
    if not isinstance(decision_safety, dict):
        raise ValueError(f"{path}.decision_safety must be an object")
    if not isinstance(decision_safety.get("passed"), bool):
        raise ValueError(f"{path}.decision_safety.passed must be a boolean")
    if not isinstance(decision_safety.get("critical_gate_failures"), list):
        raise ValueError(
            f"{path}.decision_safety.critical_gate_failures must be an array"
        )
    if not isinstance(result.get("methodology_pass"), bool):
        raise ValueError(f"{path}.methodology_pass must be a boolean")
    expected_completion = (
        decision_safety["passed"] is True and result["methodology_pass"] is True
    )
    if result["safe_completion"] is not expected_completion:
        raise ValueError(
            f"{path}.safe_completion does not match decision_safety and methodology_pass"
        )
    if decision_safety["passed"] is True and decision_safety["critical_gate_failures"]:
        raise ValueError(
            f"{path}.decision_safety cannot pass with critical gate failures"
        )


def _validate_protocol(
    payload: dict[str, Any], case_panels: dict[str, Any]
) -> tuple[list[str], int, dict[str, dict[str, Any]]]:
    """Validate the run manifest evidence before calculating any headline rate."""
    protocol = payload.get("protocol_verification")
    if not isinstance(protocol, dict):
        raise ValueError("Panel summary must contain protocol_verification metadata")
    if protocol.get("verified") is not True:
        raise ValueError("Panel protocol_verification must be marked verified")

    suite_id = payload.get("suite_id")
    if not isinstance(suite_id, str) or not suite_id:
        raise ValueError("Panel summary must contain a suite_id")
    if protocol.get("suite_id") != suite_id:
        raise ValueError("protocol_verification.suite_id must match suite_id")

    case_ids = protocol.get("case_ids")
    if not isinstance(case_ids, list) or not case_ids or not all(
        isinstance(case_id, str) and case_id for case_id in case_ids
    ):
        raise ValueError("protocol_verification.case_ids must be a non-empty array of strings")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("protocol_verification.case_ids must be unique")

    runs_per_model = protocol.get("runs_per_model_per_case")
    if (
        isinstance(runs_per_model, bool)
        or not isinstance(runs_per_model, int)
        or runs_per_model < 1
    ):
        raise ValueError(
            "protocol_verification.runs_per_model_per_case must be a positive integer"
        )

    required_harness = protocol.get("required_harness")
    recorded_harness = protocol.get("recorded_harness")
    if not isinstance(required_harness, dict) or not required_harness:
        raise ValueError("protocol_verification.required_harness must be a non-empty object")
    if not isinstance(recorded_harness, dict) or not recorded_harness:
        raise ValueError("protocol_verification.recorded_harness must be a non-empty object")
    for field, value in required_harness.items():
        if recorded_harness.get(field) != value:
            raise ValueError(
                f"protocol_verification.recorded_harness.{field} does not match requirement"
            )

    cohorts = protocol.get("cohorts")
    if not isinstance(cohorts, dict) or not cohorts:
        raise ValueError("protocol_verification.cohorts must be a non-empty object")
    if set(cohorts) != set(case_panels):
        raise ValueError("protocol_verification cohorts must exactly match case_panels")

    all_run_ids: set[str] = set()
    for cohort_id, metadata in cohorts.items():
        if not isinstance(metadata, dict):
            raise ValueError(f"protocol_verification.cohorts.{cohort_id} must be an object")
        model_id = metadata.get("model_id")
        runtime = metadata.get("runtime")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError(f"protocol_verification.cohorts.{cohort_id}.model_id is required")
        if not isinstance(runtime, str) or not runtime:
            raise ValueError(f"protocol_verification.cohorts.{cohort_id}.runtime is required")
        if f"{model_id} + {runtime}" != cohort_id:
            raise ValueError(
                f"protocol_verification cohort {cohort_id} is not model_id + runtime"
            )
        run_ids = metadata.get("run_ids")
        if not isinstance(run_ids, list) or not run_ids or not all(
            isinstance(run_id, str) and run_id for run_id in run_ids
        ):
            raise ValueError(f"protocol_verification.cohorts.{cohort_id}.run_ids is invalid")
        if len(set(run_ids)) != len(run_ids) or len(run_ids) != runs_per_model:
            raise ValueError(
                f"protocol_verification.cohorts.{cohort_id} must have exactly "
                f"{runs_per_model} unique run_ids"
            )
        if all_run_ids.intersection(run_ids):
            raise ValueError("protocol_verification run_ids must be globally unique")
        all_run_ids.update(run_ids)
        run_count = metadata.get("run_count")
        if (
            isinstance(run_count, bool)
            or not isinstance(run_count, int)
            or run_count != runs_per_model
        ):
            raise ValueError(
                f"protocol_verification.cohorts.{cohort_id}.run_count is incomplete"
            )
        if metadata.get("case_ids") != case_ids:
            raise ValueError(
                f"protocol_verification.cohorts.{cohort_id}.case_ids do not match suite"
            )
        expected_counts = metadata.get("results_per_case")
        if expected_counts != {case_id: runs_per_model for case_id in case_ids}:
            raise ValueError(
                f"protocol_verification.cohorts.{cohort_id}.results_per_case is incomplete"
            )

        results = case_panels[cohort_id]
        if not isinstance(results, list) or len(results) != runs_per_model * len(case_ids):
            raise ValueError(
                f"case_panels.{cohort_id} must contain exactly "
                f"{runs_per_model} results for each suite case"
            )
        result_case_ids: list[str] = []
        for result_index, result in enumerate(results):
            if not isinstance(result, dict) or not isinstance(result.get("case_id"), str):
                raise ValueError(f"case_panels.{cohort_id}[{result_index}] has no valid case_id")
            run_id = result.get("run_id")
            if not isinstance(run_id, str) or run_id not in run_ids:
                raise ValueError(f"case_panels.{cohort_id}[{result_index}] has an unknown run_id")
            result_case_ids.append(result["case_id"])
            if result.get("model_id") != model_id:
                raise ValueError(f"case_panels.{cohort_id}[{result_index}] model_id mismatches cohort")
            if result.get("runtime") != runtime:
                raise ValueError(f"case_panels.{cohort_id}[{result_index}] runtime mismatches cohort")
        if set(result_case_ids) != set(case_ids):
            raise ValueError(f"case_panels.{cohort_id} has unequal case coverage")
        for case_id in case_ids:
            case_results = [
                result for result in results if result["case_id"] == case_id
            ]
            if len(case_results) != runs_per_model:
                raise ValueError(
                    f"case_panels.{cohort_id} case {case_id} does not have exactly "
                    f"{runs_per_model} results"
                )
            if Counter(result["run_id"] for result in case_results) != Counter(run_ids):
                raise ValueError(
                    f"case_panels.{cohort_id} case {case_id} does not cover every run exactly once"
                )

    return case_ids, runs_per_model, cohorts


def _case_difficulty_audit(
    case_panels: dict[str, Any], case_ids: list[str]
) -> dict[str, Any]:
    """Identify cases that no longer distinguish any compared model."""
    hardening_options = [
        "Add conflicting but relevant evidence that must be reconciled.",
        "Make the safe action depend on explicit measurement or policy prerequisites.",
        "Include a plausible high-impact action that is unsafe under the supplied facts.",
        "Require a denominator, lag, or like-aged cohort calculation.",
        "Require a scoped test with a falsifiable go/no-go rule and rollback condition.",
    ]
    cases: dict[str, Any] = {}
    ceiling_case_ids: list[str] = []

    for case_id in case_ids:
        cohort_summaries: dict[str, Any] = {}
        all_scores_present = True
        every_score_is_100 = True
        every_response_is_safe = True

        for cohort_id, results in sorted(case_panels.items()):
            case_results = [result for result in results if result["case_id"] == case_id]
            scores = [
                float(result["judge_score"])
                for result in case_results
                if "judge_score" in result
            ]
            scores_complete = len(scores) == len(case_results)
            all_scores_present = all_scores_present and scores_complete
            every_score_is_100 = every_score_is_100 and scores_complete and all(
                score == 100.0 for score in scores
            )
            every_response_is_safe = every_response_is_safe and all(
                result["safe_completion"] is True for result in case_results
            )
            cohort_summaries[cohort_id] = {
                "responses": len(case_results),
                "safe_completion_rate": round(
                    sum(result["safe_completion"] is True for result in case_results)
                    / len(case_results),
                    3,
                ),
                "minimum_judge_score": min(scores) if scores_complete else None,
                "median_judge_score": (
                    round(float(median(scores)), 1) if scores_complete else None
                ),
                "maximum_judge_score": max(scores) if scores_complete else None,
            }

        if not all_scores_present:
            status = "insufficient_score_data"
        elif every_score_is_100 and every_response_is_safe:
            status = "ceiling"
            ceiling_case_ids.append(case_id)
        else:
            status = "discriminating"
        cases[case_id] = {
            "status": status,
            "cohorts": cohort_summaries,
        }

    return {
        "ceiling_definition": (
            "Every repeated response from every compared model under its recorded "
            "runtime receives a judge score of 100 and completes safely."
        ),
        "ceiling_case_ids": ceiling_case_ids,
        "recommended_action": (
            "Replace each ceiling case with a harder successor in a new suite version, "
            "then rerun every compared model on the full suite."
        ),
        "review_queue": [
            {
                "case_id": case_id,
                "action": "author_harder_successor",
                "hardening_options": hardening_options,
            }
            for case_id in ceiling_case_ids
        ],
        "cases": cases,
    }


def summarize_panel_results(payload: dict[str, Any]) -> dict[str, Any]:
    """Create neutral, uncertainty-aware reporting from an arbiter-panel summary."""
    case_panels = payload.get("case_panels")
    if not isinstance(case_panels, dict) or not case_panels:
        raise ValueError("Panel summary must contain a non-empty case_panels object")

    models: dict[str, Any] = {}
    for model_id, results in sorted(case_panels.items()):
        if not isinstance(results, list) or not results:
            raise ValueError(f"case_panels.{model_id} must be a non-empty array")
        for result_index, result in enumerate(results):
            if not isinstance(result, dict) or "case_id" not in result:
                raise ValueError(f"case_panels.{model_id} contains an invalid result")
            _validate_safety_result(result, str(model_id), result_index)
        safe_count = sum(result["safe_completion"] is True for result in results)
        serious_count = sum(is_serious_error(result) for result in results)
        scores = [float(result["judge_score"]) for result in results if "judge_score" in result]
        models[model_id] = {
            "responses": len(results),
            "unique_cases": len({str(result["case_id"]) for result in results}),
            "tasks_completed_safely": _rate_summary(safe_count, len(results)),
            "responses_with_serious_errors": _rate_summary(serious_count, len(results)),
            "median_judge_score": round(float(median(scores)), 1) if scores else None,
        }

    case_ids, _, _ = _validate_protocol(payload, case_panels)

    return {
        "claim_scope": "source-grounded decision safety under the recorded harness",
        "headline_metrics": ["tasks_completed_safely", "responses_with_serious_errors"],
        "models": models,
        "case_difficulty_audit": _case_difficulty_audit(case_panels, case_ids),
        "limitations": [
            "Automated judge agreement is not proof of real-world media-buying competence.",
            "Compare results only when suite, harness, tools, prompts, and run counts match.",
        ],
    }
