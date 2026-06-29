"""Rubric scoring against the source-backed criteria library.

This is an additive, still-deterministic layer on top of the v0 concept scorer.
It demonstrates the design described in ``rubrics/README.md``:

- A case's ``expected.rubric`` references criteria by id from
  ``rubrics/criteria_library.json`` (provenance, thresholds, machine-checkability).
- Where a rubric item carries a ``data_check``, the *ground truth* (e.g. which
  search terms are wasteful) is computed directly from the case's own synthetic
  ``data`` tables — there is no outcome data, so correctness is derived from how
  the case was constructed.
- The response is then scored on whether it addresses those findings and the
  expected behaviour, with full traceability back to the cited source.

No third-party dependencies; pure stdlib, fully deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mediabuyerbench.evaluator import _find_phrase, _is_negated_match

ROOT = Path(__file__).resolve().parent.parent
LIBRARY_PATH = ROOT / "rubrics" / "criteria_library.json"

# Expected-behaviour semantics for a rubric item.
#   flag / act / respect  -> the response SHOULD address it (positive scoring)
#   avoid                 -> the response should NOT do it (guardrail / penalty)
POSITIVE_EXPECTATIONS = {"flag", "act", "respect"}
GUARDRAIL_EXPECTATIONS = {"avoid"}

_OPERATORS = {
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "in": lambda a, b: a in b,
}


def load_criteria_library(path: str | Path = LIBRARY_PATH) -> dict[str, dict[str, Any]]:
    """Load the criteria library, indexed by criterion id."""
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {c["id"]: c for c in data["criteria"]}


def _coerce_number(value: Any) -> Any:
    """Best-effort numeric coercion so '6.2%' or '101' compare sensibly."""
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        stripped = value.strip().rstrip("%").replace("$", "").replace(",", "")
        try:
            return float(stripped)
        except ValueError:
            return value
    return value


def _row_matches(row: dict[str, Any], where: dict[str, Any]) -> bool:
    for field, condition in where.items():
        actual = _coerce_number(row.get(field))
        for op, expected in condition.items():
            func = _OPERATORS.get(op)
            if func is None:
                raise ValueError(f"Unknown operator in data_check: {op}")
            try:
                if not func(actual, _coerce_number(expected)):
                    return False
            except TypeError:
                return False
    return True


def compute_findings(case: dict[str, Any], data_check: dict[str, Any]) -> list[Any]:
    """Compute the ground-truth findings for a data_check against case data.

    data_check shape:
        {"block": "<data block name>", "where": {field: {op: value}}, "select": "<field>"}
    Returns the selected field value for every row in the named block that
    matches every condition.
    """
    block_name = data_check["block"]
    where = data_check.get("where", {})
    select = data_check["select"]
    findings: list[Any] = []
    for block in case.get("data", []):
        if block.get("name") != block_name:
            continue
        for row in block.get("rows", []):
            if _row_matches(row, where):
                findings.append(row.get(select))
    return findings


def _coverage(response: str, findings: list[Any]) -> list[Any]:
    """Which findings are explicitly referenced in the response text."""
    lowered = response.lower()
    return [f for f in findings if isinstance(f, str) and f.lower() in lowered]


def score_response_rubric(
    case: dict[str, Any],
    response: str,
    library: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Score a response against a case's ``expected.rubric`` block."""
    if library is None:
        library = load_criteria_library()

    rubric = case.get("expected", {}).get("rubric", [])
    items: list[dict[str, Any]] = []
    total_weight = 0.0
    earned_weight = 0.0
    penalty = 0.0
    skill_totals: dict[str, float] = {}
    skill_earned: dict[str, float] = {}

    for entry in rubric:
        criterion_id = entry["criterion"]
        criterion = library.get(criterion_id)
        if criterion is None:
            raise ValueError(f"Case {case['id']} references unknown criterion: {criterion_id}")

        weight = float(entry.get("weight", 1.0))
        skills = list(entry.get("skills", []))
        expectation = entry.get("expected", "flag")
        phrases = entry.get("detect", [])

        findings: list[Any] = []
        findings_covered: list[Any] = []
        if "data_check" in entry:
            findings = compute_findings(case, entry["data_check"])
            findings_covered = _coverage(response, findings)

        matched_phrase = _find_phrase(response, phrases) if phrases else None

        if expectation in GUARDRAIL_EXPECTATIONS:
            # Guardrail: full weight by default, lose it (and take a penalty) if violated.
            total_weight += weight
            violated = matched_phrase is not None and not _is_negated_match(response, matched_phrase)
            satisfied = not violated
            if satisfied:
                earned_weight += weight
            else:
                penalty += float(entry.get("penalty", 0.0))
            for skill in skills:
                skill_totals[skill] = skill_totals.get(skill, 0.0) + weight
                if satisfied:
                    skill_earned[skill] = skill_earned.get(skill, 0.0) + weight
        else:
            # Positive expectation. Detect phrases drive the headline score (same
            # semantics as the v0 concept scorer). When no detect phrases are given,
            # fall back to coverage of the data-derived findings. The findings are
            # always reported as a ground-truth + completeness sub-metric.
            total_weight += weight
            if phrases:
                satisfied = matched_phrase is not None
                earned = weight if satisfied else 0.0
            elif findings:
                fraction = len(findings_covered) / len(findings)
                satisfied = fraction > 0
                earned = weight * fraction
            else:
                satisfied = False
                earned = 0.0
            earned_weight += earned
            for skill in skills:
                skill_totals[skill] = skill_totals.get(skill, 0.0) + weight
                skill_earned[skill] = skill_earned.get(skill, 0.0) + earned

        items.append(
            {
                "criterion": criterion_id,
                "title": criterion["title"],
                "type": criterion["type"],
                "authority": criterion["authority"],
                "expected": expectation,
                "weight": weight,
                "matched_phrase": matched_phrase,
                "findings": findings,
                "findings_covered": findings_covered,
                "satisfied": satisfied,
                "skills": skills,
                "source": criterion["source"],
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
        "rubric_score": round(overall, 1),
        "raw_rubric_score": round(raw_score, 1),
        "penalty": round(penalty, 1),
        "items": items,
        "skill_scores": skill_scores,
    }


def summarize_rubric(result: dict[str, Any]) -> str:
    lines = [
        f"Case: {result['case_id']}",
        f"Rubric score: {result['rubric_score']}/100 (raw {result['raw_rubric_score']}, penalty {result['penalty']})",
        "",
        "Rubric items:",
    ]
    for item in result["items"]:
        mark = "✓" if item["satisfied"] else "✗"
        lines.append(
            f"- {mark} [{item['type']}/{item['authority']}] {item['title']} "
            f"({item['criterion']}, expected={item['expected']}, w={item['weight']})"
        )
        if item["findings"]:
            covered = ", ".join(str(f) for f in item["findings_covered"]) or "none"
            expected = ", ".join(str(f) for f in item["findings"])
            lines.append(f"    findings: {expected}")
            lines.append(f"    covered:  {covered} ({len(item['findings_covered'])}/{len(item['findings'])})")
        elif item["matched_phrase"]:
            lines.append(f"    matched phrase: {item['matched_phrase']}")
        lines.append(f"    source: {item['source']['name']}")
    if result["skill_scores"]:
        lines.extend(["", "Skill scores:"])
        for skill, value in result["skill_scores"].items():
            lines.append(f"- {skill}: {value}/100")
    return "\n".join(lines)
