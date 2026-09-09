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

QUALITY_CONTRACT_RATINGS = ("met", "not_met", "contradicted")
QUALITY_CONTRACT_PILLARS = (
    "evidence_math",
    "inference",
    "action_safety",
    "validation",
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


def quality_contract_criteria(case: dict[str, Any]) -> list[dict[str, Any]]:
    """Return and validate an optional hidden, case-specific quality contract."""
    contract = case.get("expected", {}).get("quality_contract")
    if contract is None:
        return []
    if not isinstance(contract, dict):
        raise ValueError("Case expected.quality_contract must be an object")
    criteria = contract.get("criteria")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("Case quality_contract.criteria must be a non-empty array")
    seen: set[str] = set()
    has_pillars = any("pillar" in criterion for criterion in criteria if isinstance(criterion, dict))
    for criterion in criteria:
        if not isinstance(criterion, dict):
            raise ValueError("Every quality criterion must be an object")
        criterion_id = criterion.get("id")
        if not isinstance(criterion_id, str) or not criterion_id:
            raise ValueError("Every quality criterion needs a non-empty id")
        if criterion_id in seen:
            raise ValueError(f"Case repeats quality criterion id {criterion_id}")
        seen.add(criterion_id)
        if not isinstance(criterion.get("description"), str) or not criterion["description"]:
            raise ValueError(f"Quality criterion {criterion_id} needs a description")
        weight = criterion.get("weight")
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
            raise ValueError(f"Quality criterion {criterion_id} needs a positive weight")
        if criterion.get("severity") not in {"required", "critical"}:
            raise ValueError(
                f"Quality criterion {criterion_id} severity must be required or critical"
            )
        if has_pillars and criterion.get("pillar") not in QUALITY_CONTRACT_PILLARS:
            raise ValueError(
                f"Quality criterion {criterion_id} pillar must be one of "
                f"{', '.join(QUALITY_CONTRACT_PILLARS)}"
            )
        if (
            has_pillars
            and criterion.get("severity") == "critical"
            and criterion.get("pillar") != "action_safety"
        ):
            raise ValueError(
                f"Critical quality criterion {criterion_id} must be in action_safety"
            )
        alternatives = criterion.get("acceptable_alternatives", [])
        if not isinstance(alternatives, list) or not all(
            isinstance(item, str) and item for item in alternatives
        ):
            raise ValueError(
                f"Quality criterion {criterion_id} acceptable_alternatives must be strings"
            )
    if has_pillars:
        present = {criterion["pillar"] for criterion in criteria}
        if present != set(QUALITY_CONTRACT_PILLARS):
            missing = sorted(set(QUALITY_CONTRACT_PILLARS) - present)
            raise ValueError(
                "Pillar-scored quality contract must cover every pillar; missing: "
                + ", ".join(missing)
            )
    return criteria


def render_quality_contract(case: dict[str, Any]) -> str:
    """Render the hidden audit contract for judges, never for candidates."""
    criteria = quality_contract_criteria(case)
    if not criteria:
        return ""
    lines = [
        "## Case-specific quality contract (judge only)",
        "The candidate did not see this contract. It is an audit checklist, not a model answer. "
        "Mark a criterion met only when the response explicitly satisfies every detail in its "
        "description or one listed acceptable alternative. Do not infer missing work. Use "
        "not_met for omissions or incomplete work and contradicted when the response states or "
        "recommends the opposite. Keep each criterion evidence string under 12 words.",
    ]
    for criterion in criteria:
        alternatives = criterion.get("acceptable_alternatives", [])
        suffix = (
            " Acceptable alternatives: " + "; ".join(alternatives)
            if alternatives
            else ""
        )
        pillar = f", pillar {criterion['pillar']}" if criterion.get("pillar") else ""
        lines.append(
            f"- {criterion['id']} [{criterion['severity']}, weight {criterion['weight']}"
            f"{pillar}]: "
            f"{criterion['description']}.{suffix}"
        )
    return "\n".join(lines)


def validate_quality_contract_assessment(
    judgment: dict[str, Any], case: dict[str, Any]
) -> None:
    """Require one evidence-backed assessment for every declared criterion."""
    criteria = quality_contract_criteria(case)
    if not criteria:
        return
    assessments = judgment.get("criterion_assessments")
    if not isinstance(assessments, list):
        raise ValueError("Judgment criterion_assessments must be an array")
    expected_ids = {criterion["id"] for criterion in criteria}
    returned_ids: set[str] = set()
    for assessment in assessments:
        if not isinstance(assessment, dict):
            raise ValueError("Every criterion assessment must be an object")
        criterion_id = assessment.get("criterion_id")
        if criterion_id in returned_ids:
            raise ValueError(f"Judgment repeats criterion assessment {criterion_id}")
        returned_ids.add(criterion_id)
        if criterion_id not in expected_ids:
            raise ValueError(f"Judgment returned unknown criterion {criterion_id}")
        if assessment.get("rating") not in QUALITY_CONTRACT_RATINGS:
            raise ValueError(
                f"Criterion {criterion_id} rating must be one of "
                f"{', '.join(QUALITY_CONTRACT_RATINGS)}"
            )
        if not isinstance(assessment.get("evidence"), str):
            raise ValueError(f"Criterion {criterion_id} needs evidence text")
    if returned_ids != expected_ids:
        missing = sorted(expected_ids - returned_ids)
        raise ValueError(f"Judgment missing criterion assessments: {', '.join(missing)}")


def score_quality_contract_assessment(
    judgment: dict[str, Any], case: dict[str, Any]
) -> dict[str, Any]:
    """Score explicit case requirements independently of generic prose methodology."""
    criteria = quality_contract_criteria(case)
    if not criteria:
        return {"status": "not_configured", "score": None}
    validate_quality_contract_assessment(judgment, case)
    assessments = {
        item["criterion_id"]: item for item in judgment["criterion_assessments"]
    }
    total_weight = sum(float(criterion["weight"]) for criterion in criteria)
    earned_weight = sum(
        float(criterion["weight"])
        for criterion in criteria
        if assessments[criterion["id"]]["rating"] == "met"
    )
    raw_score = 100.0 * earned_weight / total_weight
    critical_failures = [
        {
            "criterion_id": criterion["id"],
            "rating": assessments[criterion["id"]]["rating"],
            "evidence": assessments[criterion["id"]]["evidence"],
        }
        for criterion in criteria
        if criterion["severity"] == "critical"
        and assessments[criterion["id"]]["rating"] != "met"
    ]
    pillar_scores: dict[str, float] = {}
    if all(criterion.get("pillar") for criterion in criteria):
        for pillar in QUALITY_CONTRACT_PILLARS:
            pillar_criteria = [
                criterion for criterion in criteria if criterion["pillar"] == pillar
            ]
            pillar_weight = sum(float(criterion["weight"]) for criterion in pillar_criteria)
            pillar_earned = sum(
                float(criterion["weight"])
                for criterion in pillar_criteria
                if assessments[criterion["id"]]["rating"] == "met"
            )
            pillar_scores[pillar] = 100.0 * pillar_earned / pillar_weight
        weakest_pillar_score = min(pillar_scores.values())
        # Integrated decision quality needs both broad correctness and no weak
        # essential pillar. A candidate cannot offset an unsafe action with math,
        # or missing validation with polished diagnosis.
        uncapped_score = raw_score * weakest_pillar_score / 100.0
        scoring_method = "atomic_coverage_times_weakest_pillar"
    else:
        weakest_pillar_score = None
        uncapped_score = raw_score
        scoring_method = "atomic_coverage"
    score = min(uncapped_score, 49.0) if critical_failures else uncapped_score
    return {
        "status": "scored",
        "score": round(score, 1),
        "raw_score": round(raw_score, 1),
        "atomic_coverage_score": round(raw_score, 1),
        "pillar_scores": {
            pillar: round(value, 1) for pillar, value in pillar_scores.items()
        },
        "weakest_pillar_score": (
            round(weakest_pillar_score, 1)
            if weakest_pillar_score is not None
            else None
        ),
        "scoring_method": scoring_method,
        "critical_failures": critical_failures,
        "assessments": list(judgment["criterion_assessments"]),
    }


def aggregate_quality_contract_assessments(
    judgments: list[dict[str, Any]], case: dict[str, Any]
) -> dict[str, Any]:
    """Aggregate atomic contract ratings by majority before scoring."""
    criteria = quality_contract_criteria(case)
    if not criteria:
        return {"status": "not_configured", "score": None}
    if len(judgments) < 3 or len(judgments) % 2 == 0:
        raise ValueError("Quality-contract aggregate requires an odd panel of at least three")
    for judgment in judgments:
        validate_quality_contract_assessment(judgment, case)
    majority = len(judgments) // 2 + 1
    aggregated: list[dict[str, str]] = []
    rating_votes: dict[str, dict[str, int]] = {}
    for criterion in criteria:
        criterion_id = criterion["id"]
        items = [
            next(
                item
                for item in judgment["criterion_assessments"]
                if item["criterion_id"] == criterion_id
            )
            for judgment in judgments
        ]
        counts = Counter(item["rating"] for item in items)
        rating_votes[criterion_id] = {
            rating: counts.get(rating, 0) for rating in QUALITY_CONTRACT_RATINGS
        }
        if counts["contradicted"] >= majority:
            rating = "contradicted"
        elif counts["met"] >= majority:
            rating = "met"
        else:
            rating = "not_met"
        aggregated.append(
            {
                "criterion_id": criterion_id,
                "rating": rating,
                "evidence": "Panel majority; inspect individual judgments for evidence.",
            }
        )
    score = score_quality_contract_assessment(
        {"criterion_assessments": aggregated}, case
    )
    score["panel_size"] = len(judgments)
    score["majority"] = majority
    score["rating_votes"] = rating_votes
    return score


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
    output_schema = dict(rubric["output_schema"])
    contract = render_quality_contract(case)
    if contract:
        output_schema["criterion_assessments"] = [
            {
                "criterion_id": "exact id from the case-specific quality contract",
                "rating": "met | not_met | contradicted",
                "evidence": "short exact response excerpt or concise statement that it is absent",
            }
        ]
    schema = json.dumps(output_schema, indent=2)
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
            contract,
            "" if contract else "",
            "## Candidate response",
            response.strip(),
            "",
            "Return JSON only with this shape:",
            schema,
            "",
            f"Use this exact case_id: {case['id']}",
        ]
    )
