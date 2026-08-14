from __future__ import annotations

import json
from collections import Counter
from statistics import median
from pathlib import Path
from typing import Any

from mediabuyerbench.evaluator import render_prompt


ROOT = Path(__file__).resolve().parent.parent
RUBRIC_PATH = ROOT / "rubrics" / "google_search_v2.json"

DECISION_RECORD_LABELS = (
    "diagnosis",
    "decisive evidence",
    "uncertainty or confounder",
    "preconditions and smallest safe action",
    "do not do yet / rejected alternative",
    "measurement and explicit go/no-go rule",
)


def _dimension_ids(rubric: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(dimension["id"]) for dimension in rubric["dimensions"])


def _arbiter_rule_ids(rubric: dict[str, Any]) -> tuple[str, ...]:
    return tuple(str(rule["id"]) for rule in rubric["arbiter"]["rules"])


def load_rubric(path: str | Path = RUBRIC_PATH) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return json.load(f)


def render_arbiter(rubric: dict[str, Any]) -> str:
    """Render the pinned Google Search operating method for a blind judge."""
    arbiter = rubric.get("arbiter")
    if not isinstance(arbiter, dict) or not arbiter.get("id"):
        raise ValueError("Judge rubric must define an arbiter")
    provenance = "; ".join(str(item) for item in arbiter.get("provenance", []))
    lines = [
        f"## Google Search operator arbiter ({arbiter['id']})",
        str(arbiter["purpose"]),
        "",
        "Precedence:",
        *(f"- {item}" for item in arbiter.get("precedence", [])),
        "",
        "Binding operator rules:",
        *(f"- {item['id']}: {item['rule']}" for item in arbiter.get("rules", [])),
    ]
    if provenance:
        lines.extend(["", f"Provenance: {provenance}."])
    return "\n".join(lines)


def decision_record_check(response: str) -> dict[str, Any]:
    """Check the explicitly requested response structure without judging content."""
    lowered = response.lower()
    missing = [label for label in DECISION_RECORD_LABELS if label not in lowered]
    return {"passed": not missing, "missing_labels": missing}


def validate_judgment(judgment: dict[str, Any], rubric: dict[str, Any] | None = None) -> None:
    rubric = rubric or load_rubric()
    dimensions = _dimension_ids(rubric)
    missing = [
        field
    for field in (
        "case_id",
        *dimensions,
        "critical_errors",
        "methodology_failures",
        "evidence",
        "rationale",
    )
        if field not in judgment
    ]
    if missing:
        raise ValueError(f"Judgment missing fields: {', '.join(missing)}")
    if not isinstance(judgment["critical_errors"], list):
        raise ValueError("Judgment critical_errors must be an array")
    if not isinstance(judgment["methodology_failures"], list):
        raise ValueError("Judgment methodology_failures must be an array")
    if not isinstance(judgment["rationale"], str):
        raise ValueError("Judgment rationale must be a string")
    evidence = judgment["evidence"]
    if not isinstance(evidence, dict):
        raise ValueError("Judgment evidence must be an object")
    known_arbiter_rules = set(_arbiter_rule_ids(rubric))
    for dimension in dimensions:
        value = judgment[dimension]
        if not isinstance(value, int) or not 0 <= value <= 4:
            raise ValueError(f"Judgment {dimension} must be an integer from 0 through 4")
        trace = evidence.get(dimension)
        if not isinstance(trace, dict):
            raise ValueError(f"Judgment evidence must include {dimension}")
        if not isinstance(trace.get("response_excerpt"), str):
            raise ValueError(f"Judgment evidence {dimension} needs response_excerpt")
        if not isinstance(trace.get("packet_facts"), list) or not all(
            isinstance(fact, str) for fact in trace["packet_facts"]
        ):
            raise ValueError(f"Judgment evidence {dimension} needs packet_facts strings")
        rule_ids = trace.get("arbiter_rule_ids")
        if not isinstance(rule_ids, list) or not all(
            isinstance(rule_id, str) and rule_id in known_arbiter_rules for rule_id in rule_ids
        ):
            raise ValueError(
                f"Judgment evidence {dimension} needs known arbiter_rule_ids strings"
            )
    if rubric.get("id") != "google_search_v2":
        raise ValueError("Unsupported judge rubric")


def score_judgment(judgment: dict[str, Any], rubric: dict[str, Any] | None = None) -> dict[str, Any]:
    rubric = rubric or load_rubric()
    validate_judgment(judgment, rubric)
    dimensions = _dimension_ids(rubric)
    weights = {dimension["id"]: float(dimension["weight"]) for dimension in rubric["dimensions"]}
    raw = sum(100.0 * weights[dimension] * judgment[dimension] / 4.0 for dimension in dimensions)
    critical_errors = list(judgment["critical_errors"])
    score = min(raw, 49.0) if critical_errors else raw
    methodology_gate_dimensions = tuple(
        dimension["id"] for dimension in rubric["dimensions"] if dimension.get("methodology_gate")
    )
    methodology_failures = list(judgment["methodology_failures"])
    methodology_pass = not critical_errors and all(
        judgment[dimension] >= 3 for dimension in methodology_gate_dimensions
    )
    return {
        "rubric_id": rubric["id"],
        "arbiter_id": rubric["arbiter"]["id"],
        "judge_score": round(score, 1),
        "raw_judge_score": round(raw, 1),
        "critical_errors": critical_errors,
        "methodology_pass": methodology_pass,
        "methodology_failures": methodology_failures,
        "dimensions": {dimension: judgment[dimension] for dimension in dimensions},
        "rationale": judgment["rationale"],
    }


def aggregate_judgments(
    judgments: list[dict[str, Any]], rubric: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Aggregate an odd panel of blinded judgments conservatively.

    Numeric dimensions use the median. A critical error requires a majority vote,
    so one unusually harsh or generous judge cannot determine a result alone.
    """
    rubric = rubric or load_rubric()
    if len(judgments) < 3 or len(judgments) % 2 == 0:
        raise ValueError("Aggregate requires an odd panel of at least three judgments")
    for judgment in judgments:
        validate_judgment(judgment, rubric)

    case_ids = {judgment["case_id"] for judgment in judgments}
    if len(case_ids) != 1:
        raise ValueError("All judgments in an aggregate must use the same case_id")

    dimensions = _dimension_ids(rubric)
    per_dimension = {
        dimension: [judgment[dimension] for judgment in judgments] for dimension in dimensions
    }
    aggregate = {
        "case_id": next(iter(case_ids)),
        **{dimension: int(median(values)) for dimension, values in per_dimension.items()},
        "critical_errors": [],
        "methodology_failures": [],
        "evidence": {
            dimension: {
                "response_excerpt": "Panel median; inspect individual judgments for cited excerpts.",
                "packet_facts": [],
                "arbiter_rule_ids": [],
            }
            for dimension in dimensions
        },
        "rationale": "Median aggregation of blinded judge panel.",
    }
    majority = len(judgments) // 2 + 1
    critical_votes = [bool(judgment["critical_errors"]) for judgment in judgments]
    if sum(critical_votes) >= majority:
        aggregate["critical_errors"] = sorted(
            {
                reason
                for judgment in judgments
                if judgment["critical_errors"]
                for reason in judgment["critical_errors"]
            }
        )
    aggregate["methodology_failures"] = sorted(
        {
            failure
            for judgment in judgments
            for failure in judgment["methodology_failures"]
        }
    )
    score = score_judgment(aggregate, rubric)
    score["case_id"] = aggregate["case_id"]
    raw_scores = [score_judgment(judgment, rubric)["judge_score"] for judgment in judgments]
    score.update(
        {
            "panel_size": len(judgments),
            "critical_error_votes": sum(critical_votes),
            "critical_error_majority": majority,
            "judge_score_range": [min(raw_scores), max(raw_scores)],
            "dimension_votes": per_dimension,
            "individual_judge_scores": raw_scores,
        }
    )
    return score


def calibration_report(
    calibration: dict[str, Any], rubric: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Measure a judge panel against paid-media-reviewer labels.

    Input is intentionally reviewer-first: each example has one human judgment
    and an odd panel of blind judge judgments. It reports agreement, not a
    misleading claim that the panel has become correct.
    """
    rubric = rubric or load_rubric()
    examples = calibration.get("examples")
    if not isinstance(examples, list) or not examples:
        raise ValueError("Calibration file must contain a non-empty examples array")
    dimensions = _dimension_ids(rubric)
    dimension_errors: dict[str, list[float]] = {dimension: [] for dimension in dimensions}
    exact_agreements: dict[str, int] = {dimension: 0 for dimension in dimensions}
    within_one: dict[str, int] = {dimension: 0 for dimension in dimensions}
    critical = Counter()
    methodology_matches = 0

    for example in examples:
        human = example.get("human_judgment")
        panel = example.get("judge_judgments")
        if not isinstance(human, dict) or not isinstance(panel, list):
            raise ValueError("Each calibration example needs human_judgment and judge_judgments")
        validate_judgment(human, rubric)
        panel_score = aggregate_judgments(panel, rubric)
        if human["case_id"] != panel_score["case_id"]:
            raise ValueError("Human and panel case_id must match")
        human_score = score_judgment(human, rubric)
        for dimension in dimensions:
            error = abs(human[dimension] - panel_score["dimensions"][dimension])
            dimension_errors[dimension].append(error)
            exact_agreements[dimension] += int(error == 0)
            within_one[dimension] += int(error <= 1)
        human_critical = bool(human["critical_errors"])
        panel_critical = bool(panel_score["critical_errors"])
        critical[(human_critical, panel_critical)] += 1
        methodology_matches += int(human_score["methodology_pass"] == panel_score["methodology_pass"])

    total = len(examples)
    return {
        "rubric_id": rubric["id"],
        "arbiter_id": rubric["arbiter"]["id"],
        "examples": total,
        "human_labels_required": 20,
        "status": "insufficient_human_labels" if total < 20 else "review_agreement_metrics",
        "dimensions": {
            dimension: {
                "mean_absolute_error": round(sum(errors) / total, 3),
                "exact_agreement_rate": round(exact_agreements[dimension] / total, 3),
                "within_one_rate": round(within_one[dimension] / total, 3),
            }
            for dimension, errors in dimension_errors.items()
        },
        "critical_error_confusion": {
            "true_positive": critical[(True, True)],
            "false_negative": critical[(True, False)],
            "false_positive": critical[(False, True)],
            "true_negative": critical[(False, False)],
        },
        "methodology_pass_agreement_rate": round(methodology_matches / total, 3),
    }


def build_judge_prompt(case: dict[str, Any], response: str, rubric: dict[str, Any] | None = None) -> str:
    rubric = rubric or load_rubric()
    dimension_lines = "\n".join(
        f"- {dimension['id']} ({int(dimension['weight'] * 100)}%): {dimension['description']}"
        for dimension in rubric["dimensions"]
    )
    schema = json.dumps(rubric["output_schema"], indent=2)
    format_check = decision_record_check(response)
    return "\n".join(
        [
            rubric["judge_instructions"],
            "",
            render_arbiter(rubric),
            "",
            "Scoring dimensions:",
            dimension_lines,
            "",
            f"Critical-error rule: {rubric['critical_error_rule']}",
            "",
            "The candidate has no model/provider identity. Score the response itself, not writing style.",
            "For every dimension, cite a short response excerpt, packet facts, and applicable operator-arbiter rule ids used to justify the score.",
            "A score of 4 requires all elements in that dimension's definition; do not infer missing thresholds or prerequisites.",
            f"Deterministic response-format check (not a canonical answer): {json.dumps(format_check)}",
            "",
            "## Case packet",
            render_prompt(case).rstrip(),
            "",
            "## Candidate response",
            response.strip(),
            "",
            "Return JSON only with this shape:",
            schema,
            "",
            f"Use this exact case_id: {case['id']}",
        ]
    )
