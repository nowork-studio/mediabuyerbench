import json
import tempfile
import unittest
from pathlib import Path

from mediabuyerbench.suites import (
    load_declared_cases,
    resolve_case_paths,
    validate_case_sources,
)


def _case(case_id: str) -> dict:
    return {
        "id": case_id,
        "title": case_id,
        "provider": "google_ads",
        "category": "test",
        "difficulty": "hard",
        "business": {},
        "user_prompt": "Decide.",
        "data": [],
        "expected": {
            "required_concepts": [
                {
                    "id": "decision",
                    "label": "Makes a decision",
                    "phrases": ["decision"],
                }
            ]
        },
    }


class SuiteCaseSourcesTest(unittest.TestCase):
    def test_accepts_exactly_one_case_source_mode(self):
        validate_case_sources({"case_split": "cases"})
        validate_case_sources({"case_files": ["cases/one.json"]})
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validate_case_sources({})
        with self.assertRaisesRegex(ValueError, "exactly one"):
            validate_case_sources(
                {"case_split": "cases", "case_files": ["cases/one.json"]}
            )

    def test_case_files_can_span_directories_and_keep_declared_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first_dir = root / "first"
            second_dir = root / "second"
            first_dir.mkdir()
            second_dir.mkdir()
            (first_dir / "one.json").write_text(
                json.dumps(_case("one")), encoding="utf-8"
            )
            (second_dir / "two.json").write_text(
                json.dumps(_case("two")), encoding="utf-8"
            )
            suite = {
                "case_files": ["first/one.json", "second/two.json"],
                "case_ids": ["two", "one"],
            }

            cases = load_declared_cases(suite, root)

            self.assertEqual([case["id"] for case in cases], ["two", "one"])

    def test_case_file_must_exist_inside_repository(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, "stay inside"):
                resolve_case_paths(
                    {"case_files": ["../outside.json"]}, root
                )
            with self.assertRaisesRegex(ValueError, "does not exist"):
                resolve_case_paths(
                    {"case_files": ["missing.json"]}, root
                )

    def test_declared_ids_must_exactly_match_loaded_cases(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "one.json").write_text(
                json.dumps(_case("one")), encoding="utf-8"
            )
            suite = {
                "case_files": ["one.json"],
                "case_ids": ["different"],
            }
            with self.assertRaisesRegex(ValueError, "exactly match"):
                load_declared_cases(suite, root)


if __name__ == "__main__":
    unittest.main()
