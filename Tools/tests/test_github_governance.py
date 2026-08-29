import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "github_governance", ROOT / "Tools" / "github_governance.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class ManifestTests(unittest.TestCase):
    def test_repository_manifest_is_valid(self) -> None:
        labels = MODULE.load_manifest(ROOT / ".github" / "labels.json")
        names = {label["name"] for label in labels}
        self.assertIn("priority:P0", names)
        self.assertIn("status:ready", names)
        self.assertIn("status:done", names)
        self.assertTrue(
            all(
                len(label["description"]) <= MODULE.MAX_DESCRIPTION_LENGTH
                for label in labels
            )
        )

    def test_rejects_description_longer_than_github_limit(self) -> None:
        labels = json.loads((ROOT / ".github" / "labels.json").read_text(encoding="utf-8"))
        labels[0]["description"] = "x" * (MODULE.MAX_DESCRIPTION_LENGTH + 1)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "labels.json"
            path.write_text(json.dumps(labels), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "GitHub maximum"):
                MODULE.load_manifest(path)


class TaxonomyTests(unittest.TestCase):
    def test_accepts_one_priority_and_status(self) -> None:
        issues = [
            {
                "number": 12,
                "labels": [
                    {"name": "priority:P0"},
                    {"name": "status:ready"},
                    {"name": "area:catalog"},
                ],
            }
        ]
        self.assertEqual(MODULE.validate_taxonomy(issues), [])

    def test_rejects_missing_status(self) -> None:
        failures = MODULE.validate_taxonomy(
            [{"number": 7, "labels": [{"name": "priority:P0"}]}]
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("issue #7", failures[0])

    def test_rejects_two_in_progress_issues(self) -> None:
        issues = [
            {"number": 5, "labels": [{"name": "priority:P0"}, {"name": "status:in-progress"}]},
            {"number": 6, "labels": [{"name": "priority:P0"}, {"name": "status:in-progress"}]},
        ]
        failures = MODULE.validate_taxonomy(issues)
        self.assertTrue(any("more than one issue is in progress" in failure for failure in failures))

    def test_pull_requests_are_not_validated_as_issues(self) -> None:
        issues = [{"number": 99, "labels": [], "pull_request": {"url": "example"}}]
        self.assertEqual(MODULE.validate_taxonomy(issues), [])


if __name__ == "__main__":
    unittest.main()
