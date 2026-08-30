import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


class CatalogQualityCLITests(unittest.TestCase):
    def test_sample_evidence_produces_machine_and_human_reports(self):
        changes = {
            "schemaVersion": 1,
            "sourceKey": "synthetic-fixture",
            "snapshotID": "quality-cli-fixture",
            "baseline": "none",
            "additions": 2,
            "unchanged": 0,
            "formulationChanges": 0,
            "removals": 0,
            "removedSelections": [],
            "addedSelections": [
                {"gtin": "00200000000004", "market": "DE"},
                {"gtin": "00200000000028", "market": "DE"},
            ],
            "reviewQueue": [],
            "noCompletenessClaim": True,
        }
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            change_path = temp / "change.json"
            output = temp / "quality.json"
            summary = temp / "quality.md"
            change_path.write_text(json.dumps(changes, sort_keys=True) + "\n", encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "Tools" / "catalog_quality.py"),
                    "evaluate",
                    "--evidence", str(ROOT / "Data" / "evidence" / "sample-evidence-v1.json"),
                    "--change-report", str(change_path),
                    "--source-key", "synthetic-fixture",
                    "--snapshot-id", "quality-cli-fixture",
                    "--as-of", "2026-08-30T12:00:00Z",
                    "--output", str(output),
                    "--summary-output", str(summary),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)
            report = json.loads(output.read_text(encoding="utf-8"))
            human = summary.read_text(encoding="utf-8")

        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["sourceRights"]["termsReview"]["state"], "fixture-only")
        self.assertEqual(report["metrics"]["products"], 2)
        self.assertEqual(report["metrics"]["productsWithCurrentIngredients"], 2)
        self.assertTrue(report["auditSample"]["stratified"])
        self.assertIn("Evidence coverage", human)
        self.assertIn("Review sampling", human)
        self.assertIn("Incident identity", human)
        self.assertIn("Status: **pass**", human)


if __name__ == "__main__":
    unittest.main()
