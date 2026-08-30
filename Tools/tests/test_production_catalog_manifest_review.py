from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from production_catalog_manifest_review import ManifestReviewError, canonical_json, finalize_manifest_review


class ProductionCatalogManifestReviewTests(unittest.TestCase):
    def quality(self, *, comparison: bool = True) -> dict:
        report = {
            "schemaVersion": 1,
            "policyVersion": "catalog-quality-v1",
            "sourceKey": "open-food-facts",
            "snapshotID": "off-2026-08-30",
            "evaluatedAt": "2026-08-30T18:00:00Z",
            "status": "pass",
            "quarantineRequired": False,
            "rollbackRequired": False,
            "releaseBlockingFindings": [],
            "warnings": [],
            "sourceRights": {
                "approved": True,
                "fixtureOnly": False,
                "licenseIdentifier": "ODbL-1.0",
                "attributionPresent": True,
                "termsReview": {"state": "current"},
            },
            "metrics": {
                "retailerEvidenceRecords": 12,
                "certificationRecords": 7,
                "formulationFreshness": {
                    "changed-unreviewed": 2,
                    "date-unknown": 3,
                    "fresh": 40,
                    "refresh-recommended": 4,
                    "stale": 5,
                },
                "retailerFreshness": {
                    "date-unknown": 1,
                    "fresh": 9,
                    "refresh-recommended": 1,
                    "stale": 1,
                },
                "assessmentStatus": {
                    "halal-certified": 4,
                    "halal-reviewed": 2,
                    "not-halal": 1,
                    "questionable": 3,
                    "unknown": 44,
                },
                "certificationState": {"current": 6, "stale-check": 1},
                "reviewState": {"approved": 8, "pending": 2},
                "assessmentValidityEvents": 6,
            },
            "changes": (
                {
                    "available": True,
                    "baseline": "accepted-2026-08-01",
                    "additions": 10,
                    "removals": 2,
                    "formulationChanges": 4,
                    "statusChanges": [{"gtin": "00000000000000"}],
                    "reviewQueueCount": 5,
                }
                if comparison
                else {"available": False}
            ),
            "auditSample": {
                "mandatoryReviewCount": 11,
                "mandatoryReviewTruncated": False,
            },
        }
        report["reportSha256"] = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
        return report

    def write_inputs(self, directory: str, *, comparison: bool = True) -> tuple[Path, Path]:
        root = Path(directory)
        quality_path = root / "quality-report.json"
        quality = self.quality(comparison=comparison)
        quality_path.write_text(json.dumps(quality, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifest = {
            "manifestSchemaVersion": 3,
            "catalogVersion": "1.0.0",
            "schemaVersion": 2,
            "methodologyVersion": "unreviewed",
            "recordCount": 54,
            "qualityGate": {
                "schemaVersion": 1,
                "policyVersion": quality["policyVersion"],
                "reportSha256": quality["reportSha256"],
                "reportFileSha256": hashlib.sha256(quality_path.read_bytes()).hexdigest(),
                "sourceKey": quality["sourceKey"],
                "snapshotID": quality["snapshotID"],
                "evaluatedAt": quality["evaluatedAt"],
            },
        }
        manifest_path = root / "catalog-manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return manifest_path, quality_path

    def test_finalizes_bounded_release_review_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, quality_path = self.write_inputs(directory)
            manifest = finalize_manifest_review(
                manifest_path=manifest_path,
                quality_report_path=quality_path,
            )
            review = manifest["releaseReview"]
            self.assertEqual(review["schemaVersion"], 1)
            self.assertEqual(review["changeComparison"]["additions"], 10)
            self.assertEqual(review["changeComparison"]["statusChangeCount"], 1)
            self.assertEqual(review["freshnessDistributions"]["formulation"]["stale"], 5)
            self.assertEqual(review["qualityDistributions"]["assessmentStatus"]["unknown"], 44)
            self.assertEqual(review["reviewQueue"]["mandatoryHighRiskReviewCount"], 11)
            self.assertEqual(review["invalidations"]["assessmentValidityEvents"], 6)
            self.assertEqual(review["invalidations"]["changedUnreviewedFormulations"], 2)
            self.assertFalse(review["retailerChangeComparison"]["available"])
            self.assertFalse(review["certificationChangeComparison"]["available"])
            self.assertEqual(review["sourceRightsReview"]["termsReviewState"], "current")

    def test_truthfully_marks_missing_comparison_baseline_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, quality_path = self.write_inputs(directory, comparison=False)
            review = finalize_manifest_review(
                manifest_path=manifest_path,
                quality_report_path=quality_path,
            )["releaseReview"]
            comparison = review["changeComparison"]
            self.assertFalse(comparison["available"])
            self.assertIsNone(comparison["additions"])
            self.assertIsNone(comparison["formulationChanges"])
            self.assertIsNone(review["reviewQueue"]["changeReviewQueueCount"])
            self.assertIn("no accepted comparison baseline", comparison["reason"])

    def test_rejects_quality_file_digest_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, quality_path = self.write_inputs(directory)
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            quality["warnings"] = [{"code": "tampered"}]
            quality_path.write_text(json.dumps(quality, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaises(ManifestReviewError):
                finalize_manifest_review(manifest_path=manifest_path, quality_report_path=quality_path)

    def test_rejects_nonpassing_or_self_digest_invalid_quality(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            manifest_path, quality_path = self.write_inputs(directory)
            quality = json.loads(quality_path.read_text(encoding="utf-8"))
            quality["status"] = "blocked"
            quality_path.write_text(json.dumps(quality, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["qualityGate"]["reportFileSha256"] = hashlib.sha256(quality_path.read_bytes()).hexdigest()
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaises(ManifestReviewError):
                finalize_manifest_review(manifest_path=manifest_path, quality_report_path=quality_path)

    def test_build_workflow_finalizes_manifest_before_handoff(self) -> None:
        workflow = (ROOT / ".github/workflows/build-catalog.yml").read_text(encoding="utf-8")
        finalizer = workflow.index("Tools/production_catalog_manifest_review.py")
        handoff = workflow.index("- name: Emit immutable database, manifest, and release-note handoffs")
        self.assertLess(finalizer, handoff)
        self.assertGreaterEqual(workflow.count("Tools/production_catalog.py validate"), 2)


if __name__ == "__main__":
    unittest.main()
