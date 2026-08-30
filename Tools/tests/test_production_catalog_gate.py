from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import production_catalog_gate


def _envelope(*, source_class: str = "open-database", reviewer_count: int = 2, conflict: bool = False) -> dict:
    reviews = [
        {
            "targetType": "assessment",
            "targetID": "assessment-1",
            "state": "approved",
            "reviewerID": f"reviewer-{index}",
            "reviewedAt": f"2026-08-2{index}T12:00:00Z",
        }
        for index in range(1, reviewer_count + 1)
    ]
    return {
        "sources": [
            {
                "sourceKey": "source-1",
                "sourceClass": source_class,
                "sourceSnapshotID": "snapshot-1",
            }
        ],
        "identities": [
            {"id": "identity-1", "sourceKey": "source-1", "gtin": "00000000000000", "market": "DE"}
        ],
        "ingredients": [
            {
                "id": "ingredient-1",
                "gtin": "00000000000000",
                "market": "DE",
                "observedAt": "2026-08-20T00:00:00Z",
                "contentHash": "a" * 64,
            }
        ],
        "assessments": [
            {
                "id": "assessment-1",
                "gtin": "00000000000000",
                "market": "DE",
                "status": "halal-reviewed",
                "reasons": [{"severity": "positive"}],
            }
        ],
        "reviews": reviews,
        "currentSelections": [
            {
                "gtin": "00000000000000",
                "market": "DE",
                "identityObservationID": "identity-1",
                "ingredientObservationID": "ingredient-1",
                "assessmentID": "assessment-1",
                "conflictFlags": ["conflict"] if conflict else [],
            }
        ],
    }


def _policy() -> dict:
    return {
        "policyVersion": "1.0.0",
        "review": {
            "positiveStatusesRequiringIndependentSecondReview": ["halal-certified", "halal-reviewed"],
            "minimumIndependentReviewers": 2,
        },
        "freshness": {
            "formulation": {
                "anchorField": "observedAt",
                "refreshRecommendedMonths": 9,
                "staleMonths": 12,
            }
        },
    }


def _report(envelope: dict, *, status: str = "pass") -> dict:
    assessment_status = {name: 0 for name in ("halal-certified", "halal-reviewed", "not-halal", "questionable", "unknown")}
    assessment_status["halal-reviewed"] = 1
    report = {
        "schemaVersion": 1,
        "policyVersion": "1.0.0",
        "sourceKey": "source-1",
        "snapshotID": "snapshot-1",
        "evaluatedAt": "2026-08-30T12:00:00Z",
        "status": status,
        "quarantineRequired": False,
        "rollbackRequired": False,
        "releaseBlockingFindings": [],
        "warnings": [],
        "sourceRights": {"fixtureOnly": False},
        "metrics": {
            "products": 1,
            "formulationFreshness": {
                "changed-unreviewed": 0,
                "date-unknown": 0,
                "fresh": 1,
                "refresh-recommended": 0,
                "stale": 0,
            },
            "assessmentStatus": assessment_status,
            "missingIngredientSelections": 0,
            "formulationConflicts": 1 if envelope["currentSelections"][0]["conflictFlags"] else 0,
            "positiveSecondReviewDeficits": 0,
        },
    }
    report["reportSha256"] = hashlib.sha256(
        production_catalog_gate.canonical_json(report).encode("utf-8")
    ).hexdigest()
    return report


def _redigest(report: dict) -> None:
    report.pop("reportSha256", None)
    report["reportSha256"] = hashlib.sha256(
        production_catalog_gate.canonical_json(report).encode("utf-8")
    ).hexdigest()


class ProductionCatalogGateTests(unittest.TestCase):
    def test_accepts_exact_positive_with_independent_reviews(self):
        envelope = _envelope()
        result = production_catalog_gate.validate_release_gate(
            envelope=envelope,
            quality_report=_report(envelope),
            quality_policy=_policy(),
        )
        self.assertEqual(result["assessmentReviews"]["assessment-1"]["approvedReviewerCount"], 2)
        self.assertEqual(result["assessmentReviews"]["assessment-1"]["reviewedAt"], "2026-08-22T12:00:00Z")
        self.assertEqual(result["ingredientFreshness"]["ingredient-1"], "fresh")

    def test_accepts_current_selection_without_assessment(self):
        envelope = _envelope()
        envelope["currentSelections"][0]["assessmentID"] = None
        envelope["assessments"] = []
        envelope["reviews"] = []
        report = _report(envelope)
        report["metrics"]["assessmentStatus"]["halal-reviewed"] = 0
        _redigest(report)

        result = production_catalog_gate.validate_release_gate(
            envelope=envelope,
            quality_report=report,
            quality_policy=_policy(),
        )

        self.assertEqual(result["assessmentReviews"], {})
        self.assertEqual(result["ingredientFreshness"]["ingredient-1"], "fresh")

    def test_rejects_missing_second_independent_positive_review(self):
        envelope = _envelope(reviewer_count=1)
        report = _report(envelope)
        report["metrics"]["positiveSecondReviewDeficits"] = 1
        _redigest(report)
        with self.assertRaisesRegex(ValueError, "requires 2"):
            production_catalog_gate.validate_release_gate(
                envelope=envelope,
                quality_report=report,
                quality_policy=_policy(),
            )

    def test_rejects_positive_formulation_conflict_even_with_pass_report(self):
        envelope = _envelope(conflict=True)
        with self.assertRaisesRegex(ValueError, "formulation conflict"):
            production_catalog_gate.validate_release_gate(
                envelope=envelope,
                quality_report=_report(envelope),
                quality_policy=_policy(),
            )

    def test_rejects_report_digest_tampering(self):
        envelope = _envelope()
        report = _report(envelope)
        report["metrics"]["products"] = 2
        with self.assertRaisesRegex(ValueError, "self-digest mismatch"):
            production_catalog_gate.validate_release_gate(
                envelope=envelope,
                quality_report=report,
                quality_policy=_policy(),
            )

    def test_synthetic_positive_requires_one_approved_reviewer_but_stays_fixture_only(self):
        envelope = _envelope(source_class="synthetic", reviewer_count=1)
        report = _report(envelope)
        report["sourceKey"] = "synthetic-fixture"
        report["snapshotID"] = "fixture-snapshot"
        report["sourceRights"] = {"fixtureOnly": True}
        _redigest(report)
        result = production_catalog_gate.validate_release_gate(
            envelope=envelope,
            quality_report=report,
            quality_policy=_policy(),
        )
        self.assertEqual(result["assessmentReviews"]["assessment-1"]["approvedReviewerCount"], 1)

    def test_not_halal_requires_prohibitive_reason(self):
        envelope = _envelope()
        envelope["assessments"][0]["status"] = "not-halal"
        envelope["assessments"][0]["reasons"] = [{"severity": "caution"}]
        report = _report(envelope)
        report["metrics"]["assessmentStatus"]["halal-reviewed"] = 0
        report["metrics"]["assessmentStatus"]["not-halal"] = 1
        _redigest(report)
        with self.assertRaisesRegex(ValueError, "prohibitive"):
            production_catalog_gate.validate_release_gate(
                envelope=envelope,
                quality_report=report,
                quality_policy=_policy(),
            )


if __name__ == "__main__":
    unittest.main()
