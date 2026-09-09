from __future__ import annotations

from pathlib import Path
from typing import Any

from mediabuyerbench.evaluator import load_case


def validate_case_sources(suite: dict[str, Any]) -> None:
    """Require one unambiguous source for the cases declared by a suite."""
    has_split = "case_split" in suite
    has_files = "case_files" in suite
    if has_split == has_files:
        raise ValueError("Suite must define exactly one of case_split or case_files")

    if has_split:
        case_split = suite["case_split"]
        if not isinstance(case_split, str) or not case_split:
            raise ValueError("Suite case_split must be a non-empty string")
        return

    case_files = suite["case_files"]
    if (
        not isinstance(case_files, list)
        or not case_files
        or not all(isinstance(path, str) and path for path in case_files)
    ):
        raise ValueError("Suite case_files must be a non-empty array of strings")
    if len(set(case_files)) != len(case_files):
        raise ValueError("Suite case_files must be unique")


def resolve_case_paths(
    suite: dict[str, Any], root: str | Path, case_dir: str | Path | None = None
) -> list[Path]:
    """Resolve a suite's case paths without allowing paths outside the repository."""
    root_path = Path(root).resolve()
    if case_dir is not None:
        directory = Path(case_dir)
        if not directory.is_absolute():
            directory = root_path / directory
        return sorted(directory.glob("*.json"))

    validate_case_sources(suite)
    if "case_split" in suite:
        directory = (root_path / suite["case_split"]).resolve()
        try:
            directory.relative_to(root_path)
        except ValueError as exc:
            raise ValueError("Suite case_split must stay inside the repository") from exc
        return sorted(directory.glob("*.json"))

    paths: list[Path] = []
    for configured_path in suite["case_files"]:
        relative_path = Path(configured_path)
        if relative_path.is_absolute():
            raise ValueError("Suite case_files paths must be relative")
        resolved = (root_path / relative_path).resolve()
        try:
            resolved.relative_to(root_path)
        except ValueError as exc:
            raise ValueError("Suite case_files paths must stay inside the repository") from exc
        if not resolved.is_file():
            raise ValueError(f"Suite case file does not exist: {configured_path}")
        paths.append(resolved)
    return paths


def load_declared_cases(
    suite: dict[str, Any], root: str | Path, case_dir: str | Path | None = None
) -> list[dict[str, Any]]:
    """Load exactly the cases declared by the suite, in declared order."""
    case_ids = suite.get("case_ids")
    if (
        not isinstance(case_ids, list)
        or not case_ids
        or not all(isinstance(case_id, str) and case_id for case_id in case_ids)
    ):
        raise ValueError("Suite case_ids must be a non-empty array of strings")
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("Suite case_ids must be unique")

    cases_by_id: dict[str, dict[str, Any]] = {}
    for path in resolve_case_paths(suite, root, case_dir):
        case = load_case(path)
        case_id = case["id"]
        if case_id in cases_by_id:
            raise ValueError(f"Suite case sources repeat case id {case_id}")
        cases_by_id[case_id] = case

    if set(cases_by_id) != set(case_ids):
        raise ValueError(
            "Suite case files do not exactly match its declared case_ids: "
            f"declared={case_ids}, found={sorted(cases_by_id)}"
        )
    return [cases_by_id[case_id] for case_id in case_ids]
