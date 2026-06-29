from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ConceptResult:
    concept_id: str
    label: str
    matched: bool
    matched_phrase: str | None
    weight: float
    skills: list[str]


def load_case(path: str | Path) -> dict[str, Any]:
    case_path = Path(path)
    with case_path.open("r", encoding="utf-8") as f:
        case = json.load(f)
    validate_case(case, case_path)
    return case


def validate_case(case: dict[str, Any], path: Path | None = None) -> None:
    required = ["id", "title", "provider", "category", "difficulty", "business", "user_prompt", "data", "expected"]
    missing = [key for key in required if key not in case]
    if missing:
        where = f" in {path}" if path else ""
        raise ValueError(f"Missing required case fields{where}: {', '.join(missing)}")
    expected = case["expected"]
    if "required_concepts" not in expected:
        raise ValueError(f"Case {case['id']} must define expected.required_concepts")
    if not expected["required_concepts"]:
        raise ValueError(f"Case {case['id']} must include at least one required concept")
    _validate_rubric_structure(case, expected.get("rubric"))


def _validate_rubric_structure(case: dict[str, Any], rubric: Any) -> None:
    """Structural checks for an optional rubric block.

    Criterion *existence* is validated separately against the library
    (mediabuyerbench.rubric.validate_rubric) to keep this module dependency-free.
    """
    if rubric is None:
        return
    if not isinstance(rubric, list):
        raise ValueError(f"Case {case['id']} expected.rubric must be a list")
    allowed = {"flag", "act", "respect", "avoid"}
    for item in rubric:
        if "criterion" not in item:
            raise ValueError(f"Case {case['id']} rubric item missing 'criterion'")
        expectation = item.get("expected", "flag")
        if expectation not in allowed:
            raise ValueError(f"Case {case['id']} rubric item {item['criterion']} has invalid expected: {expectation}")
        data_check = item.get("data_check")
        if data_check is not None:
            for key in ("block", "select"):
                if key not in data_check:
                    raise ValueError(f"Case {case['id']} rubric item {item['criterion']} data_check missing '{key}'")


def render_prompt(case: dict[str, Any]) -> str:
    lines = [
        f"# {case['title']}",
        "",
        "You are a senior paid media buyer. Diagnose the situation and recommend what to do next.",
        "",
        "## Business context",
    ]
    for key, value in case["business"].items():
        lines.append(f"- {key}: {value}")
    lines.extend(["", "## User request", case["user_prompt"], "", "## Available data"])
    for block in case["data"]:
        lines.extend(["", f"### {block['name']}"])
        if "notes" in block:
            lines.append(block["notes"])
        rows = block.get("rows", [])
        if rows:
            headers = list(rows[0].keys())
            lines.append(" | ".join(headers))
            lines.append(" | ".join(["---"] * len(headers)))
            for row in rows:
                lines.append(" | ".join(str(row.get(h, "")) for h in headers))
    lines.extend([
        "",
        "## Response requirements",
        "- Lead with the top diagnosis.",
        "- Explain the evidence and denominator for any rates.",
        "- Recommend the smallest safe next actions.",
        "- State what not to do yet if relevant.",
        "- Include what should be measured after the change.",
    ])
    return "\n".join(lines) + "\n"


def _find_phrase(text: str, phrases: list[str]) -> str | None:
    lowered = text.lower()
    for phrase in phrases:
        if phrase.lower() in lowered:
            return phrase
    return None


def _is_negated_match(text: str, phrase: str) -> bool:
    """Return True when a forbidden phrase is clearly negated nearby.

    This keeps simple deterministic scoring from penalizing answers like
    "do not kill all old creatives" for the forbidden concept "kill all".
    """
    lowered = text.lower()
    phrase_lower = phrase.lower()
    start = lowered.find(phrase_lower)
    if start < 0:
        return False
    window = lowered[max(0, start - 40):start]
    negators = ["do not ", "don't ", "dont ", "not ", "never ", "avoid ", "without "]
    return any(negator in window for negator in negators)


def score_response(case: dict[str, Any], response: str) -> dict[str, Any]:
    expected = case["expected"]
    required = expected.get("required_concepts", [])
    forbidden = expected.get("forbidden_concepts", [])

    concept_results: list[ConceptResult] = []
    total_weight = 0.0
    earned_weight = 0.0
    skill_totals: dict[str, float] = {}
    skill_earned: dict[str, float] = {}

    for concept in required:
        weight = float(concept.get("weight", 1.0))
        skills = list(concept.get("skills", []))
        phrase = _find_phrase(response, concept.get("phrases", []))
        matched = phrase is not None
        total_weight += weight
        if matched:
            earned_weight += weight
        for skill in skills:
            skill_totals[skill] = skill_totals.get(skill, 0.0) + weight
            if matched:
                skill_earned[skill] = skill_earned.get(skill, 0.0) + weight
        concept_results.append(
            ConceptResult(
                concept_id=concept["id"],
                label=concept.get("label", concept["id"]),
                matched=matched,
                matched_phrase=phrase,
                weight=weight,
                skills=skills,
            )
        )

    forbidden_hits: list[dict[str, Any]] = []
    penalty = 0.0
    for concept in forbidden:
        phrase = _find_phrase(response, concept.get("phrases", []))
        if phrase and not _is_negated_match(response, phrase):
            concept_penalty = float(concept.get("penalty", 10.0))
            penalty += concept_penalty
            forbidden_hits.append(
                {
                    "id": concept["id"],
                    "label": concept.get("label", concept["id"]),
                    "matched_phrase": phrase,
                    "penalty": concept_penalty,
                }
            )

    raw_score = 100.0 * earned_weight / total_weight if total_weight else 0.0
    overall = max(0.0, min(100.0, raw_score - penalty))
    skill_scores = {
        skill: round(100.0 * skill_earned.get(skill, 0.0) / total, 1) if total else 0.0
        for skill, total in sorted(skill_totals.items())
    }

    return {
        "case_id": case["id"],
        "provider": case["provider"],
        "category": case["category"],
        "difficulty": case["difficulty"],
        "overall_score": round(overall, 1),
        "raw_required_score": round(raw_score, 1),
        "penalty": round(penalty, 1),
        "required": [result.__dict__ for result in concept_results],
        "forbidden_hits": forbidden_hits,
        "skill_scores": skill_scores,
    }


def summarize_score(score: dict[str, Any]) -> str:
    lines = [
        f"Case: {score['case_id']}",
        f"Provider: {score['provider']}",
        f"Overall: {score['overall_score']}/100",
        f"Required concepts: {score['raw_required_score']}/100",
        f"Penalty: {score['penalty']}",
        "",
        "Matched required concepts:",
    ]
    for item in score["required"]:
        mark = "✓" if item["matched"] else "✗"
        phrase = f" ({item['matched_phrase']})" if item["matched_phrase"] else ""
        lines.append(f"- {mark} {item['label']}{phrase}")
    if score["forbidden_hits"]:
        lines.extend(["", "Forbidden hits:"])
        for hit in score["forbidden_hits"]:
            lines.append(f"- {hit['label']} ({hit['matched_phrase']}) -{hit['penalty']}")
    if score["skill_scores"]:
        lines.extend(["", "Skill scores:"])
        for skill, value in score["skill_scores"].items():
            lines.append(f"- {skill}: {value}/100")
    return "\n".join(lines)
