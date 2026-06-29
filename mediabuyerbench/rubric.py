"""Rubric scoring against the source-backed criteria library.

An additive, deterministic layer on top of the v0 concept scorer. It implements
the design in ``rubrics/README.md``:

- A case's ``expected.rubric`` references criteria by id from
  ``rubrics/criteria_library.json`` (provenance, machine-checkability, source).
- Where a rubric item carries a ``data_check``, the *ground truth* (e.g. which
  search terms are wasteful) is computed directly from the case's own synthetic
  ``data`` tables. There is no outcome data in paid media, so correctness is
  derived from how the case was constructed.
- A response is scored on (a) whether it references those data-derived findings
  (``coverage``) and (b) whether it names the concept (``detect`` phrases).

Honest limitation: ``detect``/``coverage`` are substring matches. They are a
transparent but GAMEABLE proxy — a response crafted to echo the right tokens can
score well without real reasoning. Robust grading of the ``judge``/``hybrid``
criteria is the job of a pinned LLM judge (future work); the criteria are tagged
for exactly that. Do not read ``rubric_score`` as an un-gameable ground truth.

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
ALL_EXPECTATIONS = POSITIVE_EXPECTATIONS | GUARDRAIL_EXPECTATIONS

# When an item has both a data_check and detect phrases, coverage and diagnosis
# each contribute half of the item's credit.
COVERAGE_WEIGHT = 0.5
DIAGNOSIS_WEIGHT = 0.5

_ORDERING_OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
}


def load_criteria_library(path: str | Path = LIBRARY_PATH) -> dict[str, dict[str, Any]]:
    """Load the criteria library, indexed by criterion id."""
    with Path(path).open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {c["id"]: c for c in data["criteria"]}


def _coerce_number(value: Any) -> Any:
    """Best-effort numeric coercion so '6.2%' or '$1,420' compare as numbers."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        stripped = value.strip().rstrip("%").replace("$", "").replace(",", "")
        try:
            return float(stripped)
        except ValueError:
            return value
    return value


def _apply_operator(op: str, actual: Any, expected: Any) -> bool:
    if op == "==":
        return _coerce_number(actual) == _coerce_number(expected)
    if op == "!=":
        return _coerce_number(actual) != _coerce_number(expected)
    if op == "in":
        if not isinstance(expected, (list, tuple)):
            raise ValueError("data_check 'in' operator requires a list operand")
        return actual in expected
    if op == "contains":
        # field value (string) contains the given substring
        return isinstance(actual, str) and str(expected).lower() in actual.lower()
    if op in _ORDERING_OPS:
        a, b = _coerce_number(actual), _coerce_number(expected)
        if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
            raise ValueError(
                f"data_check ordering operator '{op}' needs numeric operands, got {actual!r} {op} {expected!r}"
            )
        return _ORDERING_OPS[op](a, b)
    raise ValueError(f"Unknown operator in data_check: {op}")


def _row_matches(row: dict[str, Any], where: dict[str, Any]) -> bool:
    for field, condition in where.items():
        actual = row.get(field)
        for op, expected in condition.items():
            if not _apply_operator(op, actual, expected):
                return False
    return True


def compute_findings(case: dict[str, Any], data_check: dict[str, Any]) -> list[Any]:
    """Compute the ground-truth findings for a data_check against case data.

    data_check shape:
        {"block": "<data block name>", "where": {field: {op: value}}, "select": "<field>"}

    Raises ValueError when the data_check is misconfigured (block or fields that
    do not exist), so a typo can never silently masquerade as "nothing found".
    Returns [] only when the block genuinely contains no matching rows.
    """
    block_name = data_check["block"]
    where = data_check.get("where", {})
    select = data_check["select"]

    blocks = [b for b in case.get("data", []) if b.get("name") == block_name]
    if not blocks:
        raise ValueError(f"data_check block not found in case {case.get('id')}: {block_name!r}")

    rows: list[dict[str, Any]] = []
    for block in blocks:
        rows.extend(block.get("rows", []))

    known_fields: set[str] = set()
    for row in rows:
        known_fields.update(row.keys())
    if rows:
        if select not in known_fields:
            raise ValueError(f"data_check select field {select!r} not present in block {block_name!r}")
        for field in where:
            if field not in known_fields:
                raise ValueError(f"data_check where field {field!r} not present in block {block_name!r}")

    return [row.get(select) for row in rows if _row_matches(row, where)]


def _coverage(response: str, findings: list[Any]) -> list[Any]:
    """Which findings are explicitly referenced (substring) in the response."""
    lowered = response.lower()
    return [f for f in findings if isinstance(f, str) and f.lower() in lowered]


def validate_rubric(case: dict[str, Any], library: dict[str, dict[str, Any]] | None = None) -> None:
    """Validate a case's rubric against the library (raises on any problem)."""
    if library is None:
        library = load_criteria_library()
    for entry in case.get("expected", {}).get("rubric", []):
        criterion_id = entry.get("criterion")
        if criterion_id not in library:
            raise ValueError(f"Case {case.get('id')} references unknown criterion: {criterion_id}")
        expectation = entry.get("expected", "flag")
        if expectation not in ALL_EXPECTATIONS:
            raise ValueError(f"Case {case.get('id')} item {criterion_id} has invalid expected: {expectation}")
        if "data_check" in entry:
            # Surfaces misconfigured block/field references immediately.
            compute_findings(case, entry["data_check"])


def _score_positive(weight: float, has_data_check: bool, cov: float | None,
                    has_detect: bool, diag: bool | None) -> tuple[float, float]:
    """Return (fraction, earned) for a positive rubric item.

    Coverage of data-derived findings is a first-class signal: when present it is
    half the credit, so the data_check is never merely cosmetic.
    """
    if has_data_check and has_detect:
        frac = COVERAGE_WEIGHT * (cov or 0.0) + DIAGNOSIS_WEIGHT * (1.0 if diag else 0.0)
    elif has_data_check:
        frac = cov or 0.0
    elif has_detect:
        frac = 1.0 if diag else 0.0
    else:
        frac = 0.0
    return frac, weight * frac


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
        cov: float | None = None
        has_data_check = "data_check" in entry
        if has_data_check:
            findings = compute_findings(case, entry["data_check"])
            findings_covered = _coverage(response, findings)
            cov = (len(findings_covered) / len(findings)) if findings else 0.0

        total_weight += weight

        if expectation in GUARDRAIL_EXPECTATIONS:
            # Guardrail: full weight unless ANY forbidden phrase appears non-negated.
            violated_phrase = None
            for phrase in phrases:
                if phrase.lower() in response.lower() and not _is_negated_match(response, phrase):
                    violated_phrase = phrase
                    break
            satisfied = violated_phrase is None
            matched_phrase = violated_phrase
            if satisfied:
                earned_weight += weight
            else:
                penalty += float(entry.get("penalty", 0.0))
            for skill in skills:
                skill_totals[skill] = skill_totals.get(skill, 0.0) + weight
                if satisfied:
                    skill_earned[skill] = skill_earned.get(skill, 0.0) + weight
        else:
            matched_phrase = _find_phrase(response, phrases) if phrases else None
            diag = matched_phrase is not None
            frac, earned = _score_positive(weight, has_data_check, cov, bool(phrases), diag)
            satisfied = frac > 0
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
                "coverage": round(cov, 2) if cov is not None else None,
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
            lines.append(
                f"    covered:  {covered} ({len(item['findings_covered'])}/{len(item['findings'])}, "
                f"coverage={item['coverage']})"
            )
        if item["matched_phrase"]:
            tag = "violated by" if item["expected"] in GUARDRAIL_EXPECTATIONS else "matched phrase"
            lines.append(f"    {tag}: {item['matched_phrase']}")
        lines.append(f"    source: {item['source']['name']}")
    if result["skill_scores"]:
        lines.extend(["", "Skill scores:"])
        for skill, value in result["skill_scores"].items():
            lines.append(f"- {skill}: {value}/100")
    return "\n".join(lines)
