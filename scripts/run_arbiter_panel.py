#!/usr/bin/env python3
"""Run a blinded Google Search arbiter panel over manifest-listed candidate runs.

The runners deliberately see candidate labels only. They never receive source
model identities, canonical answers, deterministic checks, or other judges'
scores. Three independent judgments per run/case are then aggregated by the
benchmark's median/majority policy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mediabuyerbench.evaluator import evaluate_decision_safety, render_prompt
from mediabuyerbench.judge import (
    aggregate_judgments,
    aggregate_quality_contract_assessments,
    decision_record_check,
    load_rubric,
    render_arbiter,
    render_quality_contract,
    validate_judgment,
    validate_quality_contract_assessment,
)
from mediabuyerbench.reporting import is_serious_error
from mediabuyerbench.suites import load_declared_cases, validate_case_sources


DEFAULT_SUITE = ROOT / "suites" / "google_search_decision_safety_v1.json"
MANIFEST_NAME = "manifest.json"



@dataclass(frozen=True)
class JudgeConfig:
    runner: str
    model: str
    family: str


JUDGES: dict[str, JudgeConfig] = {
    "gpt-5.6-sol": JudgeConfig("codex", "gpt-5.6-sol", "openai"),
    "claude-opus-5": JudgeConfig("claude-sleeper", "claude-opus-5", "anthropic"),
    "gemini-3.1-pro-high": JudgeConfig("agy", "gemini-3.1-pro-high", "google"),
}


def load_suite(path: str | Path = DEFAULT_SUITE) -> dict[str, Any]:
    """Load and validate the suite contract used by the panel runner."""
    suite_path = Path(path)
    with suite_path.open("r", encoding="utf-8") as f:
        suite = json.load(f)
    if not isinstance(suite, dict):
        raise ValueError("Suite must be a JSON object")
    required = ("id", "case_ids", "candidate_protocol")
    missing = [field for field in required if field not in suite]
    if missing:
        raise ValueError(f"Suite missing fields: {', '.join(missing)}")
    if not isinstance(suite["id"], str) or not suite["id"]:
        raise ValueError("Suite id must be a non-empty string")
    validate_case_sources(suite)
    case_ids = suite["case_ids"]
    if not isinstance(case_ids, list) or not case_ids or not all(
        isinstance(case_id, str) and case_id for case_id in case_ids
    ):
        raise ValueError("Suite case_ids must be a non-empty array of strings")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("Suite case_ids must be unique")
    protocol = suite["candidate_protocol"]
    if not isinstance(protocol, dict):
        raise ValueError("Suite candidate_protocol must be an object")
    runs_per_model = protocol.get("runs_per_model_per_case")
    if (
        isinstance(runs_per_model, bool)
        or not isinstance(runs_per_model, int)
        or runs_per_model < 1
    ):
        raise ValueError(
            "Suite candidate_protocol.runs_per_model_per_case must be a positive integer"
        )
    return suite


def cohort_key(model_id: str, runtime: str) -> str:
    """Return the stable report label for one model/runtime cohort."""
    return f"{model_id} + {runtime}"


def _required_harness(suite: dict[str, Any]) -> dict[str, Any]:
    protocol = suite["candidate_protocol"]
    explicit = protocol.get("harness_requirements")
    if explicit is not None:
        if not isinstance(explicit, dict) or not explicit:
            raise ValueError(
                "Suite candidate_protocol.harness_requirements must be a non-empty object"
            )
        return dict(explicit)

    required: dict[str, Any] = {}
    for field in ("tools", "external_research"):
        if field in protocol:
            required[field] = protocol[field]
    if protocol.get("same_prompt_and_limits_required") is True:
        required["same_prompt_and_limits"] = True
    return required


def _validate_recorded_harness(
    suite: dict[str, Any], harness: Any
) -> dict[str, Any]:
    if not isinstance(harness, dict) or not harness:
        raise ValueError(
            f"Manifest harness must be a non-empty object for suite {suite['id']}"
        )
    expected = _required_harness(suite)
    for field, value in expected.items():
        recorded = harness.get(field)
        if recorded is None and field == "same_prompt_and_limits":
            # Accept the suite's longer field name in hand-authored manifests.
            recorded = harness.get("same_prompt_and_limits_required")
        if recorded != value:
            raise ValueError(
                f"Manifest harness.{field} must equal suite requirement {value!r}"
            )
    normalized = dict(harness)
    if "same_prompt_and_limits" not in normalized and "same_prompt_and_limits_required" in normalized:
        normalized["same_prompt_and_limits"] = normalized["same_prompt_and_limits_required"]
    if expected.get("same_prompt_and_limits") is True:
        prompt_sha256 = normalized.get("prompt_sha256")
        if not isinstance(prompt_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", prompt_sha256) is None:
            raise ValueError("Manifest harness.prompt_sha256 must be a lowercase SHA-256 digest")
        if not isinstance(normalized.get("limits"), dict) or not normalized["limits"]:
            raise ValueError("Manifest harness.limits must be a non-empty object")
        if not isinstance(normalized.get("retry_policy"), str) or not normalized["retry_policy"]:
            raise ValueError("Manifest harness.retry_policy must be a non-empty string")
    return normalized


def _response_case_ids(response: str) -> list[str]:
    return re.findall(r"^CASE (\S+)\s*$", response, re.MULTILINE)


def _resolve_response_path(candidate_dir: Path, response_file: Any) -> Path:
    if not isinstance(response_file, str) or not response_file:
        raise ValueError("Each manifest run needs a non-empty response_file")
    path = Path(response_file)
    if path.is_absolute():
        raise ValueError("Manifest response_file paths must be relative to candidate-dir")
    candidate_root = candidate_dir.resolve()
    resolved = (candidate_root / path).resolve()
    try:
        resolved.relative_to(candidate_root)
    except ValueError as exc:
        raise ValueError("Manifest response_file must stay inside candidate-dir") from exc
    if not resolved.is_file():
        raise ValueError(f"Manifest response_file does not exist: {response_file}")
    return resolved


def load_candidate_manifest(
    candidate_dir: str | Path, suite: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    """Load run entries and group them by model/runtime cohort.

    The manifest intentionally contains no candidate response text. Every run
    response file must contain exactly one section for every suite case.
    """
    candidate_root = Path(candidate_dir)
    manifest_path = candidate_root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise ValueError(f"Candidate directory must contain {MANIFEST_NAME}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid candidate manifest JSON: {manifest_path}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Candidate manifest must be an object")
    if manifest.get("suite_id") != suite["id"]:
        raise ValueError(
            f"Candidate manifest suite_id must equal {suite['id']!r}"
        )
    harness = _validate_recorded_harness(suite, manifest.get("harness"))
    runs = manifest.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("Candidate manifest runs must be a non-empty array")

    suite_case_ids = list(suite["case_ids"])
    required_runs = suite["candidate_protocol"]["runs_per_model_per_case"]
    seen_run_ids: set[str] = set()
    cohorts: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(runs):
        if not isinstance(entry, dict):
            raise ValueError(f"Manifest runs[{index}] must be an object")
        model_id = entry.get("model_id")
        runtime = entry.get("runtime")
        run_id = entry.get("run_id")
        if not isinstance(model_id, str) or not model_id:
            raise ValueError(f"Manifest runs[{index}].model_id must be a non-empty string")
        if not isinstance(runtime, str) or not runtime:
            raise ValueError(f"Manifest runs[{index}].runtime must be a non-empty string")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError(f"Manifest runs[{index}].run_id must be a non-empty string")
        if run_id in seen_run_ids:
            raise ValueError(f"Manifest repeats run_id {run_id}")
        seen_run_ids.add(run_id)
        response_file = entry.get("response_file", entry.get("response_path"))
        response_path = _resolve_response_path(candidate_root, response_file)
        response = response_path.read_text(encoding="utf-8")
        recorded_case_ids = _response_case_ids(response)
        if len(recorded_case_ids) != len(set(recorded_case_ids)):
            raise ValueError(f"Manifest run {run_id} repeats a CASE section")
        if set(recorded_case_ids) != set(suite_case_ids):
            raise ValueError(
                f"Manifest run {run_id} case coverage must exactly match suite case_ids"
            )
        key = cohort_key(model_id, runtime)
        cohorts.setdefault(
            key,
            {
                "model_id": model_id,
                "runtime": runtime,
                "run_ids": [],
                "runs": [],
            },
        )
        cohorts[key]["run_ids"].append(run_id)
        cohorts[key]["runs"].append(
            {
                "model_id": model_id,
                "runtime": runtime,
                "run_id": run_id,
                "response_file": str(response_file),
                "response_path": response_path,
                "response": response,
            }
        )

    for key, cohort in cohorts.items():
        count = len(cohort["runs"])
        if count != required_runs:
            raise ValueError(
                f"Cohort {key} has {count} runs; suite requires exactly "
                f"{required_runs} runs_per_model_per_case"
            )
        cohort["case_ids"] = suite_case_ids
        cohort["run_count"] = count

    manifest = dict(manifest)
    manifest["harness"] = harness
    return manifest, cohorts


def _protocol_verification(
    suite: dict[str, Any], manifest: dict[str, Any], cohorts: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    suite_case_ids = list(suite["case_ids"])
    required_runs = suite["candidate_protocol"]["runs_per_model_per_case"]
    return {
        "verified": True,
        "suite_id": suite["id"],
        "case_ids": suite_case_ids,
        "runs_per_model_per_case": required_runs,
        "required_harness": _required_harness(suite),
        "recorded_harness": manifest["harness"],
        "cohorts": {
            key: {
                "model_id": cohort["model_id"],
                "runtime": cohort["runtime"],
                "run_ids": list(cohort["run_ids"]),
                "run_count": cohort["run_count"],
                "case_ids": suite_case_ids,
                "results_per_case": {
                    case_id: required_runs for case_id in suite_case_ids
                },
            }
            for key, cohort in sorted(cohorts.items())
        },
    }


def extract_case_response(candidate: str, case_id: str) -> str:
    marker = re.compile(rf"^CASE {re.escape(case_id)}\s*$", re.MULTILINE)
    match = marker.search(candidate)
    if not match:
        raise ValueError(f"Candidate does not contain {case_id}")
    next_match = re.compile(r"^CASE \S+\s*$", re.MULTILINE).search(candidate, match.end())
    return candidate[match.end() : next_match.start() if next_match else None].strip()


def _candidate_label(index: int) -> str:
    """Return a short opaque label that remains readable beyond 26 runs."""
    letters: list[str] = []
    value = index
    while True:
        letters.append(chr(ord("a") + value % 26))
        value = value // 26 - 1
        if value < 0:
            break
    return "candidate-" + "".join(reversed(letters))


def anonymize_candidates(
    candidates: dict[str, str], case_id: str, judge_id: str
) -> tuple[dict[str, str], dict[str, str]]:
    """Replace source model ids with opaque labels and vary the mapping per judge."""
    ordered_source_ids = sorted(
        candidates,
        key=lambda source_id: hashlib.sha256(
            f"{case_id}\0{judge_id}\0{source_id}".encode("utf-8")
        ).digest(),
    )
    mapping = {
        _candidate_label(index): source_id
        for index, source_id in enumerate(ordered_source_ids)
    }
    anonymous = {label: candidates[source_id] for label, source_id in mapping.items()}
    return anonymous, mapping


def compute_judgment_cache_digest(
    prompt: str, judge_id: str, label_mapping: dict[str, str] | None = None
) -> str:
    """Bind a cached raw judgment to every input that can change its result."""
    if judge_id not in JUDGES:
        raise ValueError(f"Unknown judge {judge_id}")
    config = JUDGES[judge_id]
    material = {
        "prompt": prompt,
        "judge_id": judge_id,
        "judge_config": {
            "runner": config.runner,
            "model": config.model,
            "family": config.family,
        },
        "label_mapping": dict(label_mapping or {}),
    }
    encoded = json.dumps(
        material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def judgment_cache_digest_path(raw_path: str | Path) -> Path:
    """Return the sidecar path for a raw judge response cache."""
    return Path(raw_path).with_suffix(".sha256")


def load_cached_judgment(
    raw_path: str | Path,
    prompt: str,
    judge_id: str,
    label_mapping: dict[str, str] | None = None,
) -> str | None:
    """Return a cached raw response only when its input digest matches exactly."""
    raw_path = Path(raw_path)
    digest_path = judgment_cache_digest_path(raw_path)
    if not raw_path.is_file() or not digest_path.is_file():
        return None
    expected = compute_judgment_cache_digest(prompt, judge_id, label_mapping)
    if digest_path.read_text(encoding="utf-8").strip() != expected:
        return None
    raw = raw_path.read_text(encoding="utf-8")
    try:
        parse_json_output(raw)
    except ValueError:
        return None
    return raw


def save_judgment_cache(
    raw_path: str | Path,
    raw: str,
    prompt: str,
    judge_id: str,
    label_mapping: dict[str, str] | None = None,
) -> None:
    """Persist a raw response and the digest that authorizes future reuse."""
    raw_path = Path(raw_path)
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(raw, encoding="utf-8")
    judgment_cache_digest_path(raw_path).write_text(
        compute_judgment_cache_digest(prompt, judge_id, label_mapping) + "\n",
        encoding="utf-8",
    )


def run_or_reuse_judgment(
    judge_id: str,
    prompt: str,
    raw_path: str | Path,
    label_mapping: dict[str, str] | None = None,
    runner: Any | None = None,
) -> tuple[str, bool]:
    """Run a judge on a cache miss and report whether an exact cache was reused."""
    cached = load_cached_judgment(raw_path, prompt, judge_id, label_mapping)
    if cached is not None:
        return cached, True
    raw = (runner or run_judge)(judge_id, prompt)
    save_judgment_cache(raw_path, raw, prompt, judge_id, label_mapping)
    return raw, False


def combine_panel_and_safety(
    case: dict[str, Any], response: str, panel_score: dict[str, Any]
) -> dict[str, Any]:
    """Require both objective safety gates and the blind panel methodology gate."""
    combined = dict(panel_score)
    decision_safety = evaluate_decision_safety(case, response)
    combined["decision_safety"] = decision_safety
    contract = panel_score.get("case_specific_quality", {})
    contract_safe = (
        not contract.get("critical_failures")
        if contract.get("status") == "scored"
        else True
    )
    combined["safe_completion"] = (
        decision_safety["passed"] is True
        and bool(panel_score.get("methodology_pass"))
        and contract_safe
    )
    return combined


def build_panel_prompt(
    case: dict[str, Any], candidates: dict[str, str], rubric: dict[str, Any]
) -> str:
    judgment_schema = dict(rubric["output_schema"])
    contract = render_quality_contract(case)
    if contract:
        judgment_schema["criterion_assessments"] = [
            {
                "criterion_id": "exact id from the case-specific quality contract",
                "rating": "met | not_met | contradicted",
                "evidence": "short exact response excerpt or concise statement that it is absent",
            }
        ]
    judgment_schema["candidate_id"] = "string, must exactly equal one provided anonymous candidate label"
    schema = {"judgments": [judgment_schema]}
    candidate_sections = []
    for candidate_id, response in candidates.items():
        candidate_sections.extend(
            [
                f"## Anonymous candidate ({candidate_id})",
                f"{candidate_id} format check: {json.dumps(decision_record_check(response))}",
                response,
                "",
            ]
        )
    return "\n".join(
        [
            rubric["judge_instructions"],
            "",
            render_arbiter(rubric),
            "",
            "Scoring dimensions:",
            *(
                f"- {dimension['id']} ({int(dimension['weight'] * 100)}%): "
                f"{dimension['description']}"
                for dimension in rubric["dimensions"]
            ),
            "",
            f"Critical-error rule: {rubric['critical_error_rule']}",
            "",
            "Each candidate label is anonymous and has no model/provider identity. "
            "Score every response independently; do not normalize scores across candidates, assume facts, "
            "or infer a hidden answer from the other candidates.",
            "For every dimension, cite a short response excerpt, packet facts, and applicable "
            "operator-arbiter rule ids. A score of 4 requires every element in that dimension.",
            "Each candidate section includes its own deterministic response-format check. "
            "The check is structural, not a canonical answer.",
            "",
            "## Case packet",
            render_prompt(case).rstrip(),
            "",
            contract,
            "" if contract else "",
            *candidate_sections,
            "Return JSON only with this shape:",
            json.dumps(schema, indent=2),
            "",
            f"Every judgment must use this exact case_id: {case['id']}",
            "Use each provided candidate_id exactly once.",
        ]
    )


def parse_json_output(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Judge did not return valid JSON: {raw[-500:]}") from exc


def validate_judgment_case_ids(
    judgments: list[Any], case_id: str, judge_id: str
) -> None:
    """Ensure a judge cannot score a different case under the current case key."""
    for judgment in judgments:
        if not isinstance(judgment, dict):
            raise ValueError("Every judge judgment must be an object")
        if judgment.get("case_id") != case_id:
            raise ValueError(
                f"{judge_id} returned case_id {judgment.get('case_id')!r}; "
                f"expected {case_id!r}"
            )


def run_judge(judge_id: str, prompt: str) -> str:
    config = JUDGES[judge_id]
    runner = config.runner
    model = config.model
    if runner == "agy":
        proc = subprocess.run(
            [
                "agy",
                "--prompt",
                prompt,
                "--model",
                model,
                "--mode",
                "plan",
                "--sandbox",
                "--disable-slash-commands",
                "--print-timeout",
                "5m",
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=330,
        )
        if proc.returncode:
            raise RuntimeError(f"{judge_id} failed: {proc.stderr[-1000:]}")
        return proc.stdout

    if runner == "claude-sleeper":
        proc = subprocess.run(
            [
                "claude-sleeper",
                "--print",
                "--model",
                model,
                "--effort",
                "medium",
                "--safe-mode",
                "--no-session-persistence",
                "--permission-mode",
                "dontAsk",
                "--permission-prompts",
                "none",
                "--tools",
                "",
                "--output-format",
                "text",
                prompt,
            ],
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=330,
        )
        if proc.returncode:
            raise RuntimeError(f"{judge_id} failed: {(proc.stdout + proc.stderr)[-1000:]}")
        return proc.stdout

    with tempfile.NamedTemporaryFile(suffix=".txt") as output_file:
        proc = subprocess.run(
            [
                "codex",
                "exec",
                "--model",
                model,
                "--cd",
                str(ROOT),
                "--sandbox",
                "read-only",
                "--output-last-message",
                output_file.name,
                "-",
            ],
            cwd=ROOT,
            input=prompt,
            text=True,
            capture_output=True,
            timeout=330,
        )
        if proc.returncode:
            raise RuntimeError(f"{judge_id} failed: {(proc.stdout + proc.stderr)[-1000:]}")
        return Path(output_file.name).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-dir", type=Path, required=True)
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--case-dir", type=Path, help="Override the suite case directory")
    parser.add_argument(
        "--judge",
        action="append",
        choices=sorted(JUDGES),
        default=[],
        help="Repeat three times for the blind judge panel.",
    )
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)

    try:
        suite = load_suite(args.suite)
        manifest, cohorts = load_candidate_manifest(args.candidate_dir, suite)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc

    judges = args.judge or list(JUDGES)
    if len(judges) != 3 or len(set(judges)) != 3:
        raise SystemExit("Use exactly three distinct judges for the panel")
    families = {JUDGES[judge_id].family for judge_id in judges}
    if len(families) != 3:
        raise SystemExit("Use exactly three distinct model families for the panel")

    try:
        cases = load_declared_cases(suite, ROOT, args.case_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc)) from exc
    rubric = load_rubric()
    output_dir = args.output_dir or (
        ROOT / ".runs" / f"arbiter_panel_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    per_candidate: dict[str, list[dict[str, Any]]] = {
        key: [] for key in cohorts
    }
    raw_judgments: dict[str, Any] = {}
    anonymization_maps: dict[str, Any] = {}
    for case in cases:
        responses: dict[str, str] = {}
        run_metadata: dict[str, dict[str, Any]] = {}
        for key, cohort in cohorts.items():
            for run in cohort["runs"]:
                run_id = run["run_id"]
                responses[run_id] = extract_case_response(run["response"], case["id"])
                run_metadata[run_id] = {
                    "cohort_key": key,
                    "model_id": cohort["model_id"],
                    "runtime": cohort["runtime"],
                }
        grouped_judgments: dict[str, list[dict[str, Any]]] = {
            run_id: [] for run_id in responses
        }
        for judge_id in judges:
            anonymous_responses, label_map = anonymize_candidates(
                responses, case["id"], judge_id
            )
            prompt = build_panel_prompt(case, anonymous_responses, rubric)
            anonymization_maps.setdefault(case["id"], {})[judge_id] = label_map
            print(f"Judging {case['id']} with {judge_id}...", flush=True)
            raw_path = output_dir / "raw" / judge_id / f"{case['id']}.txt"
            try:
                raw, reused = run_or_reuse_judgment(
                    judge_id, prompt, raw_path, label_map
                )
                if reused:
                    print(f"Reusing {case['id']} with {judge_id}...", flush=True)
                else:
                    print(f"Ran {case['id']} with {judge_id}...", flush=True)
                result = parse_json_output(raw)
                judgments = result.get("judgments")
                if not isinstance(judgments, list):
                    raise ValueError("Judge result must contain a judgments array")
                validate_judgment_case_ids(judgments, case["id"], judge_id)
                returned_ids = {judgment.get("candidate_id") for judgment in judgments}
                if returned_ids != set(anonymous_responses) or len(judgments) != len(responses):
                    raise ValueError(f"{judge_id} did not return every candidate exactly once")
                for judgment in judgments:
                    judgment = dict(judgment)
                    anonymous_id = judgment.pop("candidate_id")
                    source_id = label_map[anonymous_id]
                    validate_judgment(judgment, rubric)
                    validate_quality_contract_assessment(judgment, case)
                    grouped_judgments[source_id].append(judgment)
                    raw_judgments.setdefault(case["id"], {}).setdefault(source_id, {})[
                        judge_id
                    ] = judgment
            except Exception as exc:
                error_path = raw_path.with_suffix(".error.txt")
                error_path.write_text(str(exc), encoding="utf-8")
                raise
        for run_id, judgments in grouped_judgments.items():
            panel_score = aggregate_judgments(judgments, rubric)
            panel_score["case_specific_quality"] = (
                aggregate_quality_contract_assessments(judgments, case)
            )
            result = combine_panel_and_safety(case, responses[run_id], panel_score)
            result.update({"run_id": run_id, **run_metadata[run_id]})
            per_candidate[run_metadata[run_id]["cohort_key"]].append(result)

    candidate_summaries = {}
    for candidate_id, scores in per_candidate.items():
        contract_scores = [
            score["case_specific_quality"]["score"]
            for score in scores
            if score.get("case_specific_quality", {}).get("score") is not None
        ]
        atomic_scores = [
            score["case_specific_quality"]["atomic_coverage_score"]
            for score in scores
            if score.get("case_specific_quality", {}).get("atomic_coverage_score")
            is not None
        ]
        weakest_pillar_scores = [
            score["case_specific_quality"]["weakest_pillar_score"]
            for score in scores
            if score.get("case_specific_quality", {}).get("weakest_pillar_score")
            is not None
        ]
        candidate_summaries[candidate_id] = {
            "average_judge_score": round(sum(score["judge_score"] for score in scores) / len(scores), 1),
            "overall_quality_score": (
                round(sum(contract_scores) / len(contract_scores), 1)
                if contract_scores
                else None
            ),
            "atomic_coverage_score": (
                round(sum(atomic_scores) / len(atomic_scores), 1)
                if atomic_scores
                else None
            ),
            "average_weakest_pillar_score": (
                round(
                    sum(weakest_pillar_scores) / len(weakest_pillar_scores), 1
                )
                if weakest_pillar_scores
                else None
            ),
            "safe_completion_rate": round(
                sum(bool(score["safe_completion"]) for score in scores) / len(scores), 3
            ),
            "methodology_pass_rate": round(
                sum(bool(score["methodology_pass"]) for score in scores) / len(scores), 3
            ),
            "critical_case_count": sum(bool(score["critical_errors"]) for score in scores),
            "serious_error_count": sum(is_serious_error(score) for score in scores),
        }

    payload = {
        "suite_id": suite["id"],
        "rubric_id": rubric["id"],
        "rubric_version": rubric["version"],
        "arbiter_id": rubric["arbiter"]["id"],
        "judges": judges,
        "judge_families": {judge_id: JUDGES[judge_id].family for judge_id in judges},
        "protocol_verification": _protocol_verification(suite, manifest, cohorts),
        "candidate_summaries": candidate_summaries,
        "case_panels": per_candidate,
        "raw_judgments": raw_judgments,
        "anonymization_maps": anonymization_maps,
    }
    (output_dir / "summary.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print("\nCandidate\tOverall quality\tSafe completion\tSerious errors\tMethod quality")
    for candidate_id, summary in sorted(candidate_summaries.items()):
        print(
            f"{candidate_id}\t{summary['overall_quality_score']}\t"
            f"{summary['safe_completion_rate']:.0%}\t"
            f"{summary['serious_error_count']}\t{summary['average_judge_score']}"
        )
    print(f"\nWrote {output_dir / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
