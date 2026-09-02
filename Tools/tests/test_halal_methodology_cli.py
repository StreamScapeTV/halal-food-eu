import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "Tools" / "halal_methodology.py"
EVIDENCE = ROOT / "Data" / "evidence" / "sample-evidence-v1.json"


def digest(value):
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


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
            self.assertEqual(result["reviewArtifact"]["ingredientContentHash"], clean_report["ingredientContentHash"])
            self.assertEqual(
                [item["queueID"] for item in result["reviewArtifact"]["checklists"]],
                ["positive-ingredient-review"],
            )
            self.assertTrue(result["reviewArtifact"]["checklists"][0]["items"])

            forged_analysis = temp / "forged-analysis.json"
            forged_report = dict(clean_report)
            forged_report["sourceText"] = "Water only"
            forged_report.pop("analysisSha256")
            forged_report["analysisSha256"] = digest(forged_report)
            forged_analysis.write_text(json.dumps(forged_report, sort_keys=True) + "\n", encoding="utf-8")
            forged_output = temp / "forged-reviewed.json"
            forged_review = self.run_tool(
                "review",
                "--evidence", EVIDENCE,
                "--analysis", forged_analysis,
                "--review-input", review_input,
                "--output", forged_output,
            )
            self.assertNotEqual(forged_review.returncode, 0)
            self.assertIn("current exact evidence", forged_review.stderr + forged_review.stdout)
            self.assertFalse(forged_output.exists())

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
            self.assertIn("certificationStatus", migration_report)
            supplied_digest = migration_report.pop("migrationSha256")
            self.assertEqual(supplied_digest, digest(migration_report))


if __name__ == "__main__":
    unittest.main()
