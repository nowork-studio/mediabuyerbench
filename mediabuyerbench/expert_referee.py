from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mediabuyerbench.evaluator import render_prompt


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE_PACK = ROOT / "source_packs" / "google_search_operator_sources_v1.json"

DIMENSIONS = (
    "evidence_trace",
    "causal_discipline",
    "preconditions_and_sequence",
    "intervention_scope",
    "decision_rule",
    "alternatives_and_tradeoffs",
)
WEIGHTS = {
    "evidence_trace": 0.20,
    "causal_discipline": 0.15,
    "preconditions_and_sequence": 0.25,
    "intervention_scope": 0.15,
    "decision_rule": 0.15,
    "alternatives_and_tradeoffs": 0.10,
}


def load_source_pack(path: str | Path = DEFAULT_SOURCE_PACK) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        source_pack = json.load(f)
    validate_source_pack(source_pack)
    return source_pack


def validate_source_pack(source_pack: dict[str, Any]) -> None:
    required = ("id", "version", "as_of", "purpose", "source_policy", "sources")
    missing = [field for field in required if field not in source_pack]
    if missing:
        raise ValueError(f"Source pack missing fields: {', '.join(missing)}")
    source_ids: set[str] = set()
    for source in source_pack["sources"]:
        for field in ("id", "authority", "url", "scope", "operator_rule"):
            if not isinstance(source.get(field), str) or not source[field]:
                raise ValueError(f"Source pack source needs {field}")
        if source["id"] in source_ids:
            raise ValueError(f"Duplicate source id: {source['id']}")
        source_ids.add(source["id"])


def _source_ids(source_pack: dict[str, Any]) -> set[str]:
    return {str(source["id"]) for source in source_pack["sources"]}


def render_source_pack(source_pack: dict[str, Any]) -> str:
    lines = [
        f"# Source pack: {source_pack['id']} v{source_pack['version']} ({source_pack['as_of']})",
        source_pack["purpose"],
        "",
        "## Source policy",
        *(f"- {rule}" for rule in source_pack["source_policy"]),
        "",
        "## Sources and operating rules",
    ]
    for source in source_pack["sources"]:
        lines.extend(
            [
                f"### {source['id']} — {source['authority']}",
                f"URL: {source['url']}",
                f"Scope: {source['scope']}",
                f"Operator rule: {source['operator_rule']}",
            ]
        )
    return "\n".join(lines)


REFERENCE_SCHEMA = {
    "case_id": "string",
    "source_pack_id": "string",
    "source_pack_version": "integer",
    "reference_decision": {
        "decision": "string",
        "acceptable_alternatives": "array of strings",
        "decisive_packet_facts": "array of strings",
        "material_uncertainties": "array of strings",
        "prerequisite_gates": "array of strings",
        "smallest_safe_action": {"scope": "string", "action": "string"},
        "validation": {
            "outcome": "string",
            "denominator": "string",
            "window": "string",
            "comparison": "string",
            "go": "string",
            "no_go": "string"
        },
        "source_ids": "array of source ids from the source pack",
        "confidence": "high | medium | low"
    },
    "rationale": "string, 150 words maximum"
}


REVIEW_SCHEMA = {
    "case_id": "string",
    "source_pack_id": "string",
    "source_pack_version": "integer",
    "reference_decision_id": "string",
    "verdict": "pass | mixed | fail",
    **{dimension: "integer 0-4" for dimension in DIMENSIONS},
    "critical_errors": "array of strings",
    "methodology_failures": "array of strings",
    "evidence": {
        "<dimension_id>": {
            "candidate_excerpt": "short exact excerpt from candidate response",
            "packet_facts": "array of packet facts",
            "reference_alignment": "string",
            "source_ids": "array of source ids from the source pack"
        }
    },
    "rationale": "string, 150 words maximum"
}


def build_reference_prompt(case: dict[str, Any], source_pack: dict[str, Any] | None = None) -> str:
    source_pack = source_pack or load_source_pack()
    return "\n".join(
        [
            "You are the source-grounded Google Search certification referee.",
            "Create the reference decision for this case before any candidate response is shown. "
            "This is an audit record, not hidden chain-of-thought. State only the observable decision, evidence, gates, and decision rule.",
            "Use the case packet for advertiser facts. Use the source pack only for platform mechanics and general operating principles. "
            "Never turn a generic source rule into an unsupported business fact, numerical threshold, causal conclusion, or action.",
            "If the packet does not support a unique action, record materially safe alternatives and the missing evidence.",
            "Every reference decision must cite at least one source ID and every decisive fact must be traceable to the packet.",
            "",
            render_source_pack(source_pack),
            "",
            "## Case packet",
            render_prompt(case).rstrip(),
            "",
            "Return JSON only with this shape:",
            json.dumps(REFERENCE_SCHEMA, indent=2),
            "",
            f"Use this exact case_id: {case['id']}",
            f"Use this exact source_pack_id: {source_pack['id']}",
            f"Use this exact source_pack_version: {source_pack['version']}",
        ]
    )


def build_review_prompt(
    case: dict[str, Any],
    response: str,
    reference: dict[str, Any],
    source_pack: dict[str, Any] | None = None,
) -> str:
    source_pack = source_pack or load_source_pack()
    validate_reference(reference, case, source_pack)
    reference_id = f"{reference['case_id']}:{reference['source_pack_id']}:v{reference['source_pack_version']}"
    return "\n".join(
        [
            "You are the final source-grounded Google Search certification referee.",
            "Judge one anonymous candidate response against the case packet, source pack, and frozen reference decision. "
            "Do not reward prose, familiarity, or matching words. Evaluate whether the candidate reaches a supported decision by a safe, auditable method.",
            "The case packet overrides a generic source principle. The reference decision defines acceptable alternatives but cannot add facts absent from the packet.",
            "A critical error is an unsafe material action, an invented fact or numerical gate used to justify an action, an unsupported causal claim that drives the recommendation, or acting before a packet-stated prerequisite is satisfied. Any critical error caps the score at 49.",
            "For every dimension, cite a candidate excerpt, packet facts, reference alignment, and source IDs. Return only the auditable judgment, not private chain-of-thought.",
            "",
            render_source_pack(source_pack),
            "",
            "## Frozen expert reference decision",
            json.dumps(reference, indent=2),
            "",
            "## Case packet",
            render_prompt(case).rstrip(),
            "",
            "## Anonymous candidate response",
            response.strip(),
            "",
            "Return JSON only with this shape:",
            json.dumps(REVIEW_SCHEMA, indent=2),
            "",
            f"Use this exact case_id: {case['id']}",
            f"Use this exact source_pack_id: {source_pack['id']}",
            f"Use this exact source_pack_version: {source_pack['version']}",
            f"Use this exact reference_decision_id: {reference_id}",
        ]
    )


def validate_reference(
    reference: dict[str, Any], case: dict[str, Any], source_pack: dict[str, Any] | None = None
) -> None:
    source_pack = source_pack or load_source_pack()
    for field in ("case_id", "source_pack_id", "source_pack_version", "reference_decision", "rationale"):
        if field not in reference:
            raise ValueError(f"Reference decision missing {field}")
    if reference["case_id"] != case["id"]:
        raise ValueError("Reference case_id does not match case")
    if reference["source_pack_id"] != source_pack["id"]:
        raise ValueError("Reference source_pack_id does not match source pack")
    if reference["source_pack_version"] != source_pack["version"]:
        raise ValueError("Reference source_pack_version does not match source pack")
    decision = reference["reference_decision"]
    required = (
        "decision",
        "acceptable_alternatives",
        "decisive_packet_facts",
        "material_uncertainties",
        "prerequisite_gates",
        "smallest_safe_action",
        "validation",
        "source_ids",
        "confidence",
    )
    missing = [field for field in required if field not in decision]
    if missing:
        raise ValueError(f"Reference decision missing fields: {', '.join(missing)}")
    if not decision["source_ids"] or not set(decision["source_ids"]).issubset(_source_ids(source_pack)):
        raise ValueError("Reference must cite known source IDs")
    if decision["confidence"] not in {"high", "medium", "low"}:
        raise ValueError("Reference confidence must be high, medium, or low")
    validation = decision["validation"]
    if not isinstance(validation, dict) or any(
        not isinstance(validation.get(field), str)
        for field in ("outcome", "denominator", "window", "comparison", "go", "no_go")
    ):
        raise ValueError("Reference validation must define outcome, denominator, window, comparison, go, and no_go")


def validate_expert_review(
    review: dict[str, Any], case: dict[str, Any], source_pack: dict[str, Any] | None = None
) -> None:
    source_pack = source_pack or load_source_pack()
    for field in (
        "case_id",
        "source_pack_id",
        "source_pack_version",
        "reference_decision_id",
        "verdict",
        *DIMENSIONS,
        "critical_errors",
        "methodology_failures",
        "evidence",
        "rationale",
    ):
        if field not in review:
            raise ValueError(f"Expert review missing {field}")
    if review["case_id"] != case["id"]:
        raise ValueError("Expert review case_id does not match case")
    if review["source_pack_id"] != source_pack["id"]:
        raise ValueError("Expert review source_pack_id does not match source pack")
    if review["source_pack_version"] != source_pack["version"]:
        raise ValueError("Expert review source_pack_version does not match source pack")
    if review["verdict"] not in {"pass", "mixed", "fail"}:
        raise ValueError("Expert review verdict must be pass, mixed, or fail")
    if not isinstance(review["critical_errors"], list) or not isinstance(review["methodology_failures"], list):
        raise ValueError("Expert review error fields must be arrays")
    known_source_ids = _source_ids(source_pack)
    for dimension in DIMENSIONS:
        if not isinstance(review[dimension], int) or not 0 <= review[dimension] <= 4:
            raise ValueError(f"Expert review {dimension} must be an integer from 0 through 4")
        evidence = review["evidence"].get(dimension)
        if not isinstance(evidence, dict):
            raise ValueError(f"Expert review evidence needs {dimension}")
        if not all(isinstance(evidence.get(field), str) for field in ("candidate_excerpt", "reference_alignment")):
            raise ValueError(f"Expert review evidence {dimension} needs excerpts and alignment")
        if not isinstance(evidence.get("packet_facts"), list) or not all(
            isinstance(fact, str) for fact in evidence["packet_facts"]
        ):
            raise ValueError(f"Expert review evidence {dimension} needs packet_facts")
        source_ids = evidence.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids or not set(source_ids).issubset(known_source_ids):
            raise ValueError(f"Expert review evidence {dimension} needs known source_ids")


def score_expert_review(
    review: dict[str, Any], case: dict[str, Any], source_pack: dict[str, Any] | None = None
) -> dict[str, Any]:
    validate_expert_review(review, case, source_pack)
    raw = sum(100 * WEIGHTS[dimension] * review[dimension] / 4 for dimension in DIMENSIONS)
    critical_errors = list(review["critical_errors"])
    score = min(raw, 49.0) if critical_errors else raw
    methodology_pass = not critical_errors and all(review[dimension] >= 3 for dimension in DIMENSIONS[:-1])
    return {
        "case_id": review["case_id"],
        "source_pack_id": review["source_pack_id"],
        "source_pack_version": review["source_pack_version"],
        "expert_score": round(score, 1),
        "raw_expert_score": round(raw, 1),
        "methodology_pass": methodology_pass,
        "critical_errors": critical_errors,
        "dimensions": {dimension: review[dimension] for dimension in DIMENSIONS},
        "verdict": review["verdict"],
        "rationale": review["rationale"],
    }
