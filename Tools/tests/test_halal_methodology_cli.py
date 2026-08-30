import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "Tools" / "halal_methodology.py"
EVIDENCE = ROOT / "Data" / "evidence" / "sample-evidence-v1.json"


class HalalMethodologyCLITests(unittest.TestCase):
    def run_tool(self, *arguments):
        return subprocess.run(
            [sys.executable, str(TOOL), *map(str, arguments)],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )

    def test_validate_analyze_review_and_migrate_end_to_end(self):
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            analysis = temp / "analysis.json"
            reviewed = temp / "reviewed.json"
            review_input = temp / "review-input.json"
            migration = temp / "migration.json"

            validation = self.run_tool("validate")
            self.assertEqual(validation.returncode, 0, msg=validation.stderr + validation.stdout)

            candidate = self.run_tool(
                "analyze",
                "--evidence", EVIDENCE,
                "--gtin", "00200000000028",
                "--market", "DE",
                "--freshness-state", "changed-unreviewed",
                "--output", analysis,
            )
            self.assertEqual(candidate.returncode, 0, msg=candidate.stderr + candidate.stdout)
            report = json.loads(analysis.read_text(encoding="utf-8"))
            codes = {item["reasonCode"] for item in report["candidateFindings"]}
            self.assertIn("emulsifier-origin-required", codes)
            self.assertIn("flavouring-origin-carrier-required", codes)
            self.assertEqual(report["parserStatus"], "questionable")
            self.assertNotIn(report["parserStatus"], {"halal-certified", "halal-reviewed", "not-halal"})

            clean_analysis = temp / "clean-analysis.json"
            clean = self.run_tool(
                "analyze",
                "--evidence", EVIDENCE,
                "--gtin", "00200000000004",
                "--market", "DE",
                "--freshness-state", "fresh",
                "--output", clean_analysis,
            )
            self.assertEqual(clean.returncode, 0, msg=clean.stderr + clean.stdout)
            clean_report = json.loads(clean_analysis.read_text(encoding="utf-8"))
            ingredient_id = clean_report["ingredientObservationID"]
            self.assertEqual(clean_report["parserStatus"], "unknown")
            self.assertEqual({item["id"] for item in clean_report["reviewQueues"]}, {"positive-ingredient-review"})

            review_input.write_text(json.dumps({
                "decision": "halal-reviewed",
                "reviewerID": "reviewer:synthetic-cli",
                "reviewedAt": "2026-08-30T12:00:00Z",
                "nextReviewAt": "2027-02-28T12:00:00Z",
                "limitations": "Synthetic fixture review; this is not certification.",
                "reason": "Synthetic fixture was explicitly reviewed against the exact current ingredient observation.",
                "resolvedQueues": {"positive-ingredient-review": [ingredient_id]},
                "additionalEvidenceIDs": [],
            }, sort_keys=True) + "\n", encoding="utf-8")
            reviewed_result = self.run_tool(
                "review",
                "--evidence", EVIDENCE,
                "--analysis", clean_analysis,
                "--review-input", review_input,
                "--output", reviewed,
            )
            self.assertEqual(reviewed_result.returncode, 0, msg=reviewed_result.stderr + reviewed_result.stdout)
            result = json.loads(reviewed.read_text(encoding="utf-8"))
            self.assertEqual(result["assessment"]["status"], "halal-reviewed")
            self.assertEqual(result["assessment"]["certificationIDs"], [])
            self.assertEqual(result["review"]["methodologyVersion"], "1.0.0")
            self.assertIn("not certification", result["reviewArtifact"]["limitations"])
            self.assertEqual(
                [item["queueID"] for item in result["reviewArtifact"]["checklists"]],
                ["positive-ingredient-review"],
            )
            self.assertTrue(result["reviewArtifact"]["checklists"][0]["items"])

            migrated = self.run_tool(
                "migrate",
                "--evidence", EVIDENCE,
                "--occurred-at", "2026-08-30T12:00:00Z",
                "--output", migration,
            )
            self.assertEqual(migrated.returncode, 0, msg=migrated.stderr + migrated.stdout)
            migration_report = json.loads(migration.read_text(encoding="utf-8"))
            self.assertGreaterEqual(migration_report["invalidated"], 1)
            self.assertTrue(migration_report["validityEvents"])


if __name__ == "__main__":
    unittest.main()
