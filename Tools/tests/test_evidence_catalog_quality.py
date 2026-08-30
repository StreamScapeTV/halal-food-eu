import copy
import importlib.util
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("catalog_quality_core", ROOT / "Tools" / "catalog_quality_core.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
POLICY = json.loads((ROOT / "Data" / "quality" / "catalog-quality-policy-v1.json").read_text(encoding="utf-8"))
AS_OF = datetime(2026, 8, 30, tzinfo=timezone.utc)


def base_envelope():
    identity = {
        "id": "identity-1", "gtin": "00000000000000", "market": "DE",
        "sourceKey": "synthetic-core", "name": "Fixture", "retrievedAt": "2026-08-30T00:00:00Z",
    }
    ingredient = {
        "id": "ingredient-1", "gtin": identity["gtin"], "market": "DE", "sourceKey": "synthetic-core",
        "sourceRecordID": "fixture", "contentHash": "a" * 64, "observedAt": "2026-08-30T00:00:00Z",
        "retrievedAt": "2026-08-30T00:00:00Z", "captureMethod": "source-text", "verificationState": "human-verified",
    }
    selection = {
        "id": "selection-1", "gtin": identity["gtin"], "market": "DE", "identityObservationID": identity["id"],
        "ingredientObservationID": ingredient["id"], "certificationIDs": [], "retailerEvidenceIDs": [],
        "remoteImageIDs": [], "conflictFlags": [],
    }
    return {
        "schemaVersion": 1,
        "sources": [{"sourceKey": "synthetic-core", "sourceClass": "synthetic", "retrievedAt": "2026-08-30T00:00:00Z"}],
        "identities": [identity], "ingredients": [ingredient], "retailerEvidence": [], "certifications": [],
        "reviews": [], "assessments": [], "validityEvents": [], "currentSelections": [selection],
    }


def change_report(**overrides):
    value = {
        "schemaVersion": 1, "sourceKey": "synthetic-fixture", "snapshotID": "fixture-1", "baseline": "none",
        "additions": 1, "unchanged": 0, "formulationChanges": 0, "removals": 0,
        "reviewQueue": [], "noCompletenessClaim": True,
    }
    value.update(overrides)
    return value


class PolicyTests(unittest.TestCase):
    def test_committed_policy_validates(self):
        MODULE.validate_policy(POLICY)

    def test_calendar_month_boundaries_are_exact(self):
        self.assertEqual(MODULE.freshness_state("2025-11-30T00:00:00Z", AS_OF, refresh_months=9, stale_months=12), "refresh-recommended")
        self.assertEqual(MODULE.freshness_state("2025-08-30T00:00:00Z", AS_OF, refresh_months=9, stale_months=12), "stale")
        self.assertEqual(MODULE.freshness_state(None, AS_OF, refresh_months=9, stale_months=12), "date-unknown")


class QualityGateTests(unittest.TestCase):
    def evaluate(self, envelope=None, change=None):
        return MODULE.evaluate_quality(
            policy=POLICY,
            envelope=envelope or base_envelope(),
            source_key="synthetic-fixture",
            snapshot_id="fixture-1",
            change_report=change or change_report(),
            as_of=AS_OF,
        )

    def test_fresh_fixture_passes_and_report_is_deterministic(self):
        first = self.evaluate()
        second = self.evaluate(envelope=copy.deepcopy(base_envelope()))
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "pass")
        self.assertEqual(first["metrics"]["formulationFreshness"]["fresh"], 1)

    def test_retrieval_date_cannot_refresh_unknown_formulation_date(self):
        envelope = base_envelope()
        envelope["ingredients"][0].pop("observedAt")
        report = self.evaluate(envelope=envelope)
        self.assertEqual(report["metrics"]["formulationFreshness"]["date-unknown"], 1)
        self.assertTrue(any(item["code"] == "formulation-date-unknown" for item in report["warnings"]))

    def test_changed_unreviewed_is_visible_and_positive_inheritance_blocks(self):
        envelope = base_envelope()
        ingredient = envelope["ingredients"][0]
        ingredient["supersedesID"] = "ingredient-old"
        assessment = {
            "id": "assessment-1", "gtin": ingredient["gtin"], "market": "DE", "status": "halal-reviewed",
            "ingredientObservationID": "ingredient-old",
        }
        envelope["assessments"] = [assessment]
        envelope["currentSelections"][0]["assessmentID"] = assessment["id"]
        report = self.evaluate(envelope=envelope)
        self.assertEqual(report["metrics"]["formulationFreshness"]["changed-unreviewed"], 1)
        self.assertTrue(any(item["code"] == "unsafe-positive-inheritance" for item in report["releaseBlockingFindings"]))

    def test_conflicting_active_formulations_block_positive_result(self):
        envelope = base_envelope()
        second = copy.deepcopy(envelope["ingredients"][0])
        second["id"] = "ingredient-2"
        second["contentHash"] = "b" * 64
        envelope["ingredients"].append(second)
        assessment = {
            "id": "assessment-1", "gtin": second["gtin"], "market": "DE", "status": "halal-reviewed",
            "ingredientObservationID": envelope["ingredients"][0]["id"],
        }
        envelope["assessments"] = [assessment]
        envelope["reviews"] = [
            {"targetID": "assessment-1", "state": "approved", "reviewerID": "reviewer-a"},
        ]
        envelope["currentSelections"][0]["assessmentID"] = assessment["id"]
        report = self.evaluate(envelope=envelope)
        self.assertEqual(report["metrics"]["formulationConflicts"], 1)
        self.assertTrue(any(item["code"] == "positive-with-formulation-conflict" for item in report["releaseBlockingFindings"]))

    def test_real_positive_requires_two_independent_reviewers(self):
        envelope = base_envelope()
        envelope["sources"][0]["sourceClass"] = "manufacturer"
        assessment = {
            "id": "assessment-1", "gtin": envelope["identities"][0]["gtin"], "market": "DE", "status": "halal-reviewed",
            "ingredientObservationID": envelope["ingredients"][0]["id"],
        }
        envelope["assessments"] = [assessment]
        envelope["reviews"] = [{"targetID": "assessment-1", "state": "approved", "reviewerID": "reviewer-a"}]
        envelope["currentSelections"][0]["assessmentID"] = assessment["id"]
        report = self.evaluate(envelope=envelope)
        self.assertTrue(any(item["code"] == "positive-second-review-missing" for item in report["releaseBlockingFindings"]))

    def test_certification_expiry_blocks_certified_assessment(self):
        envelope = base_envelope()
        cert = {
            "id": "cert-1", "gtin": envelope["identities"][0]["gtin"], "market": "DE",
            "lastCheckedAt": "2026-07-01T00:00:00Z", "effectiveAt": "2025-01-01T00:00:00Z", "expiryAt": "2026-08-01T00:00:00Z",
        }
        assessment = {
            "id": "assessment-1", "gtin": cert["gtin"], "market": "DE", "status": "halal-certified",
            "ingredientObservationID": envelope["ingredients"][0]["id"],
        }
        envelope["certifications"] = [cert]
        envelope["assessments"] = [assessment]
        envelope["reviews"] = [{"targetID": "assessment-1", "state": "approved", "reviewerID": "reviewer-a"}]
        envelope["currentSelections"][0]["assessmentID"] = assessment["id"]
        envelope["currentSelections"][0]["certificationIDs"] = [cert["id"]]
        report = self.evaluate(envelope=envelope)
        self.assertTrue(any(item["code"] == "certification-invalid" for item in report["releaseBlockingFindings"]))

    def test_retailer_freshness_is_separate_from_formulation_freshness(self):
        envelope = base_envelope()
        envelope["retailerEvidence"] = [{"id": "retail-1", "observedAt": "2026-01-01T00:00:00Z"}]
        envelope["currentSelections"][0]["retailerEvidenceIDs"] = ["retail-1"]
        report = self.evaluate(envelope=envelope)
        self.assertEqual(report["metrics"]["formulationFreshness"]["fresh"], 1)
        self.assertEqual(report["metrics"]["retailerFreshness"]["stale"], 1)

    def test_count_collapse_and_parser_errors_block_release(self):
        envelope = base_envelope()
        envelope["currentSelections"] = [copy.deepcopy(envelope["currentSelections"][0]) for _ in range(80)]
        for index, item in enumerate(envelope["currentSelections"]):
            item["gtin"] = f"{index:014d}"
            item["id"] = f"selection-{index}"
        change = change_report(
            baseline="provided-evidence", additions=0, unchanged=80, removals=40,
            parserQuality={"malformedRate": 0.01, "schemaErrors": 0},
        )
        report = self.evaluate(envelope=envelope, change=change)
        codes = {item["code"] for item in report["releaseBlockingFindings"]}
        self.assertIn("unexpected-count-decrease", codes)
        self.assertIn("parser-error-rate-exceeded", codes)

    def test_sampling_is_stable_under_selection_reordering(self):
        envelope = base_envelope()
        selections = []
        identities = []
        ingredients = []
        for index in range(40):
            gtin = f"{index:014d}"
            identity = copy.deepcopy(envelope["identities"][0]); identity["id"] = f"i-{index}"; identity["gtin"] = gtin
            ingredient = copy.deepcopy(envelope["ingredients"][0]); ingredient["id"] = f"g-{index}"; ingredient["gtin"] = gtin
            selection = copy.deepcopy(envelope["currentSelections"][0]); selection["id"] = f"s-{index}"; selection["gtin"] = gtin; selection["identityObservationID"] = identity["id"]; selection["ingredientObservationID"] = ingredient["id"]
            identities.append(identity); ingredients.append(ingredient); selections.append(selection)
        envelope["identities"] = identities; envelope["ingredients"] = ingredients; envelope["currentSelections"] = selections
        first = self.evaluate(envelope=envelope)["auditSample"]
        envelope["currentSelections"] = list(reversed(selections))
        second = self.evaluate(envelope=envelope)["auditSample"]
        self.assertEqual(first, second)

    def test_human_summary_contains_blockers_and_quarantine_state(self):
        envelope = base_envelope()
        envelope["ingredients"][0]["supersedesID"] = "old"
        assessment = {"id": "assessment-1", "gtin": envelope["identities"][0]["gtin"], "market": "DE", "status": "halal-reviewed", "ingredientObservationID": "old"}
        envelope["assessments"] = [assessment]
        envelope["currentSelections"][0]["assessmentID"] = assessment["id"]
        summary = MODULE.human_summary(self.evaluate(envelope=envelope))
        self.assertIn("unsafe-positive-inheritance", summary)
        self.assertIn("Quarantine required: true", summary)


if __name__ == "__main__":
    unittest.main()
