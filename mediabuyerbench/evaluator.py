from __future__ import annotations

import json
import re
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


@dataclass(frozen=True)
class AssertionResult:
    assertion_id: str
    label: str
    assertion_type: str
    matched: bool
    matched_value: float | str | None
    weight: float
    skills: list[str]


@dataclass(frozen=True)
class SafetyGateResult:
    id: str
    label: str
    gate_type: str
    passed: bool
    matched_value: float | str | None
    severity: str


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
    concepts = expected.get("required_concepts", [])
    assertions = expected.get("required_assertions", [])
    safety_gates = expected.get("safety_gates", [])
    if not concepts and not assertions and not safety_gates:
        raise ValueError(
            f"Case {case['id']} must include at least one required concept or assertion or safety gate"
        )
    known_gate_types = {"contains_any", "contains_none", "requires_any", "number"}
    known_severities = {"required", "critical"}
    seen_gate_ids: set[str] = set()
    for gate in safety_gates:
        gate_id = gate.get("id")
        if not isinstance(gate_id, str) or not gate_id:
            raise ValueError(f"Case {case['id']} has a safety gate without an id")
        if gate_id in seen_gate_ids:
            raise ValueError(f"Case {case['id']} repeats safety gate id {gate_id}")
        seen_gate_ids.add(gate_id)
        gate_type = gate.get("type")
        if gate_type not in known_gate_types:
            raise ValueError(f"Unknown safety gate type: {gate_type}")
        severity = gate.get("severity", "required")
        if severity not in known_severities:
            raise ValueError(f"Unknown safety gate severity: {severity}")
        if gate_type in {"contains_any", "contains_none", "requires_any"} and not gate.get("phrases"):
            raise ValueError(f"Safety gate {gate_id} requires phrases")
        if gate_type == "number":
            if "value" not in gate:
                raise ValueError(f"Safety gate {gate_id} requires value")
            context = gate.get("context")
            if not isinstance(context, list) or not context or not all(
                isinstance(phrase, str) and phrase for phrase in context
            ):
                raise ValueError(f"Safety gate {gate_id} requires context phrases")


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
        "- Include a concise, auditable decision record: decisive facts/calculations; uncertainty or confounder; action and preconditions; rejected alternative and why; measurement and go/no-go rule.",
        "- Explain the evidence and denominator for any rates.",
        "- Recommend the smallest safe next actions at the exact scope.",
        "- State what not to do yet if relevant.",
        "- Do not provide private chain-of-thought; provide only the decision record needed to audit the recommendation.",
    ])
    return "\n".join(lines) + "\n"


def _find_phrase(text: str, phrases: list[str]) -> str | None:
    lowered = text.lower()
    for phrase in phrases:
        if phrase.lower() in lowered:
            return phrase
    return None


_CLAUSE_BOUNDARIES = frozenset(".!?;,:\n\r\u2013\u2014")
_NEGATOR_RE = re.compile(
    r"\b(?:do\s+not|does\s+not|did\s+not|can(?:not|'t)|could\s+not|"
    r"should(?:n't|\s+not)|must\s+not|don't|dont|doesn't|didn't|"
    r"never|avoid|without|skip(?:ping|ped|s)?|no\s+need\s+to|not|no)\b",
    re.IGNORECASE,
)
_NEGATION_MAX_INTERVENING_TOKENS = 6
_CLAUSE_CONJUNCTION_RE = re.compile(r"\b(?:but|however|yet|instead|except)\b", re.IGNORECASE)


def _clause_start(text: str, match_at: int) -> int:
    """Return the start of the clause containing a phrase match.

    Commas are clause boundaries except when they are thousands separators;
    this keeps phrases such as ``$1,620`` intact while preventing a negator in
    an earlier comma-separated clause from governing a later action.
    """
    index = match_at - 1
    boundary_start = 0
    while index >= 0:
        character = text[index]
        if character in _CLAUSE_BOUNDARIES:
            if (
                character == ","
                and index > 0
                and index + 1 < len(text)
                and text[index - 1].isdigit()
                and text[index + 1].isdigit()
            ):
                index -= 1
                continue
            boundary_start = index + 1
            break
        index -= 1
    conjunctions = list(_CLAUSE_CONJUNCTION_RE.finditer(text[:match_at]))
    conjunction_start = conjunctions[-1].end() if conjunctions else 0
    return max(boundary_start, conjunction_start)


def _is_negated_at(lowered: str, match_at: int) -> bool:
    """Return whether the phrase at ``match_at`` is negated in its clause."""
    clause_prefix = lowered[_clause_start(lowered, match_at):match_at]
    for negator in _NEGATOR_RE.finditer(clause_prefix):
        intervening = clause_prefix[negator.end():]
        if len(intervening) > 40:
            continue
        if any(
            token in {"but", "however", "although", "though", "yet", "instead", "except"}
            for token in re.findall(r"[a-z0-9]+", intervening)
        ):
            continue
        if len(re.findall(r"[a-z0-9]+", intervening)) <= _NEGATION_MAX_INTERVENING_TOKENS:
            return True
    return False


def _is_negated_match(text: str, phrase: str) -> bool:
    """Return True when the first matching phrase is negated in its clause."""
    lowered = text.lower()
    phrase_lower = phrase.lower()
    start = lowered.find(phrase_lower)
    return start >= 0 and _is_negated_at(lowered, start)


def _numbers_in(text: str) -> list[float]:
    """Extract ordinary metric values from a response for tolerance-based checks."""
    values: list[float] = []
    for token in re.findall(r"(?<![\w.])-?\$?\d[\d,]*(?:\.\d+)?", text):
        normalized = token.replace("$", "").replace(",", "")
        try:
            values.append(float(normalized))
        except ValueError:
            continue
    return values


def _bounded_units(text: str) -> list[str]:
    """Split response text into sentence/line/clause-bounded units.

    Semicolons and commas separate independent clauses for context matching;
    commas inside ordinary thousands-formatted numbers do not.
    """
    units: list[str] = []
    start = 0
    for index, character in enumerate(text):
        if character not in _CLAUSE_BOUNDARIES or character == ":":
            continue
        if (
            character == ","
            and index > 0
            and index + 1 < len(text)
            and text[index - 1].isdigit()
            and text[index + 1].isdigit()
        ):
            continue
        unit = text[start:index].strip()
        if unit:
            units.append(unit)
        start = index + 1
    tail = text[start:].strip()
    if tail:
        units.append(tail)
    return units


def _find_unnegated_phrase(text: str, phrases: list[str]) -> str | None:
    """Find a phrase used as an action rather than a negated warning."""
    lowered = text.lower()
    for phrase in phrases:
        phrase_lower = phrase.lower()
        start = 0
        while True:
            match_at = lowered.find(phrase_lower, start)
            if match_at < 0:
                break
            if not _is_negated_at(lowered, match_at):
                return phrase
            start = match_at + len(phrase_lower)
    return None


def _score_assertion(assertion: dict[str, Any], response: str) -> AssertionResult:
    assertion_type = assertion.get("type", "contains_any")
    matched_value: float | str | None = None
    if assertion_type == "contains_any":
        phrase = _find_phrase(response, assertion.get("phrases", []))
        matched = phrase is not None
        matched_value = phrase
    elif assertion_type == "number":
        target = float(assertion["value"])
        tolerance = float(assertion.get("tolerance", 0.0))
        matched_number = next(
            (value for value in _numbers_in(response) if abs(value - target) <= tolerance),
            None,
        )
        matched = matched_number is not None
        matched_value = matched_number
    else:
        raise ValueError(f"Unknown assertion type: {assertion_type}")

    return AssertionResult(
        assertion_id=assertion["id"],
        label=assertion.get("label", assertion["id"]),
        assertion_type=assertion_type,
        matched=matched,
        matched_value=matched_value,
        weight=float(assertion.get("weight", 1.0)),
        skills=list(assertion.get("skills", [])),
    )


def _score_safety_gate(gate: dict[str, Any], response: str) -> SafetyGateResult:
    gate_type = gate["type"]
    matched_value: float | str | None = None
    if gate_type == "contains_any":
        matched_value = _find_phrase(response, gate.get("phrases", []))
        passed = matched_value is not None
    elif gate_type == "requires_any":
        matched_value = _find_unnegated_phrase(response, gate.get("phrases", []))
        passed = matched_value is not None
    elif gate_type == "contains_none":
        matched_value = _find_unnegated_phrase(response, gate.get("phrases", []))
        passed = matched_value is None
    elif gate_type == "number":
        target = float(gate["value"])
        tolerance = float(gate.get("tolerance", 0.0))
        contexts = [str(context).lower() for context in gate.get("context", [])]
        matched_value = next(
            (
                value
                for unit in _bounded_units(response)
                if contexts
                and all(context in unit.lower() for context in contexts)
                for value in _numbers_in(unit)
                if abs(value - target) <= tolerance
            ),
            None,
        )
        passed = matched_value is not None
    else:  # validate_case rejects this; keep direct callers safe too.
        raise ValueError(f"Unknown safety gate type: {gate_type}")
    return SafetyGateResult(
        id=gate["id"],
        label=gate.get("label", gate["id"]),
        gate_type=gate_type,
        passed=passed,
        matched_value=matched_value,
        severity=gate.get("severity", "required"),
    )


def evaluate_decision_safety(case: dict[str, Any], response: str) -> dict[str, Any]:
    """Evaluate objective required and critical gates independently of prose scoring."""
    safety_gates = case.get("expected", {}).get("safety_gates", [])
    gate_results = [_score_safety_gate(gate, response) for gate in safety_gates]
    failed_gates = [result for result in gate_results if not result.passed]
    critical_gate_failures = [
        result for result in failed_gates if result.severity == "critical"
    ]
    serialized = {result.id: result.__dict__ for result in gate_results}
    return {
        "status": "pass" if safety_gates and not failed_gates else "fail" if safety_gates else "not_configured",
        "passed": not failed_gates if safety_gates else None,
        "gates": [serialized[result.id] for result in gate_results],
        "failed_gates": [serialized[result.id] for result in failed_gates],
        "critical_gate_failures": [serialized[result.id] for result in critical_gate_failures],
    }


def score_response(
    case: dict[str, Any],
    response: str,
    judgment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    expected = case["expected"]
    required = expected.get("required_concepts", [])
    assertions = expected.get("required_assertions", [])
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

    assertion_results: list[AssertionResult] = []
    for assertion in assertions:
        result = _score_assertion(assertion, response)
        total_weight += result.weight
        if result.matched:
            earned_weight += result.weight
        for skill in result.skills:
            skill_totals[skill] = skill_totals.get(skill, 0.0) + result.weight
            if result.matched:
                skill_earned[skill] = skill_earned.get(skill, 0.0) + result.weight
        assertion_results.append(result)

    forbidden_hits: list[dict[str, Any]] = []
    penalty = 0.0
    for concept in forbidden:
        phrase = _find_unnegated_phrase(response, concept.get("phrases", []))
        if phrase:
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

    score = {
        "case_id": case["id"],
        "provider": case["provider"],
        "category": case["category"],
        "difficulty": case["difficulty"],
        "overall_score": round(overall, 1),
        "raw_required_score": round(raw_score, 1),
        "penalty": round(penalty, 1),
        "required": [result.__dict__ for result in concept_results],
        "assertions": [result.__dict__ for result in assertion_results],
        "forbidden_hits": forbidden_hits,
        "skill_scores": skill_scores,
    }
    score["decision_safety"] = evaluate_decision_safety(case, response)
    if judgment is not None:
        from mediabuyerbench.judge import score_judgment

        if judgment.get("case_id") != case["id"]:
            raise ValueError(f"Judgment case_id does not match case {case['id']}")
        judge_score = score_judgment(judgment)
        score["judge"] = judge_score
        # The hybrid is deliberately secondary until the paid-media reviewer has
        # calibrated judge behavior against enough reviewed answers. It gives
        # safety/calculation checks 35% and blind expert judgment 65%.
        score["hybrid_score"] = round(0.35 * score["overall_score"] + 0.65 * judge_score["judge_score"], 1)
    return score


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
    if score.get("assertions"):
        lines.extend(["", "Required assertions:"])
        for item in score["assertions"]:
            mark = "✓" if item["matched"] else "✗"
            value = f" ({item['matched_value']})" if item["matched_value"] is not None else ""
            lines.append(f"- {mark} {item['label']}{value}")
    if score["forbidden_hits"]:
        lines.extend(["", "Forbidden hits:"])
        for hit in score["forbidden_hits"]:
            lines.append(f"- {hit['label']} ({hit['matched_phrase']}) -{hit['penalty']}")
    if score["skill_scores"]:
        lines.extend(["", "Skill scores:"])
        for skill, value in score["skill_scores"].items():
            lines.append(f"- {skill}: {value}/100")
    safety = score["decision_safety"]
    lines.extend(["", f"Decision safety: {safety['status']}"])
    for gate in safety["gates"]:
        mark = "✓" if gate["passed"] else "✗"
        lines.append(f"- {mark} {gate['label']} ({gate['severity']})")
    if score.get("judge"):
        judge = score["judge"]
        lines.extend([
            "",
            f"Blind judge ({judge['rubric_id']}): {judge['judge_score']}/100",
            f"Methodology pass: {'yes' if judge['methodology_pass'] else 'no'}",
            f"Hybrid (not calibrated): {score['hybrid_score']}/100",
            f"Judge rationale: {judge['rationale']}",
        ])
    return "\n".join(lines)
