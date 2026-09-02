import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("catalog_health", ROOT / "Tools" / "catalog_health.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def envelope():
    return {
        "sources": [
            {"sourceKey": "open-food-facts", "sourceClass": "open-database", "retrievedAt": "2026-09-01T00:00:00Z"},
            {"sourceKey": "open-prices", "sourceClass": "retailer-observation", "retrievedAt": "2026-09-01T00:00:00Z"},
        ],
        "identities": [
            {"id": "i1", "gtin": "00000000000001", "market": "DE", "sourceKey": "open-food-facts", "brand": "Brand A", "categories": ["snacks"]},
            {"id": "i2", "gtin": "00000000000002", "market": "DE", "sourceKey": "open-food-facts", "brand": "Brand B", "categories": ["drinks"]},
        ],
        "ingredients": [
            {"id": "g1", "gtin": "00000000000001", "market": "DE", "sourceKey": "open-food-facts", "languageCode": "de"},
        ],
        "assessments": [
            {"id": "a1", "gtin": "00000000000001", "market": "DE", "status": "questionable", "methodologyVersion": "1.0"},
        ],
        "certifications": [],
        "retailerEvidence": [
            {"id": "r1", "gtin": "00000000000001", "market": "DE", "retailerKey": "lidl", "kind": "store-observation", "observedAt": "2026-08-30T00:00:00Z"},
        ],
        "currentSelections": [
            {"gtin": "00000000000001", "market": "DE", "identityObservationID": "i1", "ingredientObservationID": "g1", "assessmentID": "a1", "retailerEvidenceIDs": ["r1"], "certificationIDs": [], "conflictFlags": []},
            {"gtin": "00000000000002", "market": "DE", "identityObservationID": "i2", "ingredientObservationID": None, "assessmentID": None, "retailerEvidenceIDs": [], "certificationIDs": [], "conflictFlags": ["identity-conflict"]},
        ],
    }


def quality():
    return {
        "status": "pass",
        "metrics": {
            "formulationFreshness": {"fresh": 1, "date-unknown": 1},
            "retailerFreshness": {"fresh": 1},
            "certificationFreshness": {},
            "assessmentMethodologyVersions": {"1.0": 1},
            "retailerFreshnessByRetailerAndKind": {"lidl|store-observation": {"fresh": 1}},
        },
        "changes": {"previousSourceRecordCount": 80, "currentSourceRecordCount": 100},
        "releaseBlockingFindings": [],
        "warnings": [{"code": "formulation-date-unknown"}],
        "incident": {"action": "none", "deduplicationKeys": []},
        "deduplicationKeys": [],
    }


def passing_lidl_gate():
    return {
        "state": "pass",
        "claimState": "official-complete-snapshot",
        "denominatorReconciled": True,
        "denominator": 1,
        "snapshotID": "lidl-de-2026-09-01",
    }


class CatalogHealthTests(unittest.TestCase):
    def build(self, evidence=None, q=None, change=None, benchmark=None):
        return MODULE.build_health_report(
            envelope=evidence or envelope(),
            quality=q if q is not None else quality(),
            change=change,
            benchmark=benchmark,
            evaluated_at="2026-09-02T00:00:00Z",
            commit_sha="0123456789abcdef",
        )

    def test_product_ingredient_status_and_conflict_metrics_are_explicit(self):
        report = self.build()
        self.assertEqual(report["products"]["uniqueCurrentSelections"], 2)
        self.assertEqual(report["products"]["withCurrentIngredients"], 1)
        self.assertEqual(report["products"]["missingCurrentIngredients"], 1)
        self.assertEqual(report["products"]["conflictedSelections"], 1)
        self.assertEqual(report["assessments"]["currentStatusCounts"], {"questionable": 1, "unassessed": 1})
        self.assertEqual(report["freshness"]["formulation"]["date-unknown"], 1)

    def test_lidl_observation_is_partial_and_rewe_without_evidence_is_no_evidence(self):
        report = self.build()
        self.assertEqual(report["retailers"]["lidl"]["claimState"], "observational-partial")
        self.assertEqual(report["retailers"]["rewe"]["claimState"], "no-evidence")
        self.assertIsNone(report["retailers"]["lidl"]["denominator"])

    def test_official_evidence_alone_never_claims_complete_coverage(self):
        data = envelope()
        data["retailerEvidence"][0].update({
            "kind": "official-listing", "evidenceClass": "official",
            "coverageClaim": "official-complete-snapshot", "denominatorReconciled": True, "denominator": 1,
        })
        report = self.build(evidence=data)
        self.assertEqual(report["retailers"]["lidl"]["claimState"], "official-partial")
        self.assertFalse(report["retailers"]["lidl"]["coverageGatePresent"])
        self.assertIsNone(report["retailers"]["lidl"]["denominator"])

    def test_complete_claim_requires_separate_passing_reviewed_coverage_gate(self):
        data = envelope()
        data["retailerEvidence"][0].update({"kind": "official-listing", "evidenceClass": "official"})
        q = quality()
        q["retailerCoverageGates"] = {"lidl": passing_lidl_gate()}
        report = self.build(evidence=data, q=q)
        self.assertEqual(report["retailers"]["lidl"]["claimState"], "official-complete-snapshot")
        self.assertEqual(report["retailers"]["lidl"]["denominator"], 1)
        self.assertEqual(report["retailers"]["lidl"]["completeSnapshotID"], "lidl-de-2026-09-01")

    def test_incomplete_or_malformed_coverage_gate_fails_closed(self):
        data = envelope()
        data["retailerEvidence"][0].update({"kind": "official-listing", "evidenceClass": "official"})
        q = quality()
        gate = passing_lidl_gate()
        gate["denominatorReconciled"] = False
        q["retailerCoverageGates"] = {"lidl": gate}
        report = self.build(evidence=data, q=q)
        self.assertEqual(report["retailers"]["lidl"]["claimState"], "official-partial")
        self.assertFalse(report["retailers"]["lidl"]["coverageGatePresent"])

    def test_stale_official_evidence_degrades_instead_of_remaining_complete(self):
        data = envelope()
        data["retailerEvidence"][0].update({"kind": "official-listing", "evidenceClass": "official"})
        q = quality()
        q["retailerCoverageGates"] = {"lidl": passing_lidl_gate()}
        q["metrics"]["retailerFreshnessByRetailerAndKind"] = {"lidl|official-listing": {"stale": 1}}
        report = self.build(evidence=data, q=q)
        self.assertEqual(report["retailers"]["lidl"]["claimState"], "degraded")
        self.assertIsNone(report["retailers"]["lidl"]["denominator"])

    def test_before_after_source_counts_are_projected_from_quality_authority(self):
        report = self.build(change={"baseline": "fixture", "additions": 20, "removals": 0, "reviewQueue": [], "noCompletenessClaim": True})
        self.assertEqual(report["changes"]["previousSourceRecordCount"], 80)
        self.assertEqual(report["changes"]["currentSourceRecordCount"], 100)

    def test_certification_state_and_unmatched_counts_stay_separate(self):
        data = envelope()
        data["certifications"] = [
            {"id": "c1", "effectiveAt": "2026-01-01T00:00:00Z", "expiryAt": "2027-01-01T00:00:00Z", "lastCheckedAt": "2026-08-01T00:00:00Z"},
            {"id": "c2", "effectiveAt": "2026-01-01T00:00:00Z", "expiryAt": "2026-01-01T00:00:00Z", "lastCheckedAt": "2025-12-01T00:00:00Z"},
        ]
        data["currentSelections"][0]["certificationIDs"] = ["c1"]
        report = self.build(evidence=data)
        self.assertEqual(report["certifications"]["states"], {"active": 1})
        self.assertEqual(report["certifications"]["unmatchedStoredCertificateCount"], 1)

    def test_absent_runtime_metrics_are_unknown_not_guessed(self):
        report = self.build()
        self.assertFalse(report["buildRuntime"]["available"])
        self.assertIsNone(report["buildRuntime"]["sqliteBytes"])
        measured = self.build(benchmark={"sqliteBytes": 1234, "queryLatencyMs": {"p95": 4.5}})
        self.assertTrue(measured["buildRuntime"]["available"])
        self.assertEqual(measured["buildRuntime"]["sqliteBytes"], 1234)
        self.assertEqual(measured["buildRuntime"]["queryLatencyP95Ms"], 4.5)

    def test_report_is_deterministic_and_digest_validates(self):
        first = self.build()
        second = self.build(evidence=json.loads(json.dumps(envelope())))
        self.assertEqual(first, second)
        MODULE.validate_health_report(first)
        tampered = json.loads(json.dumps(first))
        tampered["products"]["uniqueCurrentSelections"] += 1
        with self.assertRaises(MODULE.CatalogHealthError):
            MODULE.validate_health_report(tampered)

    def test_human_summary_repeats_completeness_boundary(self):
        summary = MODULE.human_summary(self.build())
        self.assertIn("observational-partial", summary)
        self.assertIn("do not imply nationwide/current stock", summary)
        self.assertIn("separate reviewed official coverage gate", summary)


if __name__ == "__main__":
    unittest.main()
