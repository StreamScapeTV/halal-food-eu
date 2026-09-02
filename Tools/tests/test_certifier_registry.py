from __future__ import annotations

import copy
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import certifier_registry as REGISTRY
from halal_methodology_core import MethodologyError

METHODOLOGY = json.loads((ROOT / "Data/methodology/halal-methodology-v1.json").read_text(encoding="utf-8"))
PRODUCTION_REGISTRY = json.loads((ROOT / "Data/certifiers/certifier-registry-v1.json").read_text(encoding="utf-8"))
GTIN = "04006381333931"
INGREDIENT_ID = "hfeu:ingredient:sha256:" + "1" * 64
CERT_ID = "hfeu:certification:sha256:" + "2" * 64
ASSESSMENT_ID = "hfeu:assessment:sha256:" + "3" * 64


def accepted_registry(**entry_overrides):
    entry = {
        "certifierKey": "fixture-certifier",
        "schemeKey": "fixture-scheme",
        "certifier": "Fixture Certifier",
        "scheme": "fixture-scheme",
        "legalName": "Fixture Certifier e.V.",
        "displayName": "Fixture Certifier",
        "markets": ["DE"],
        "state": "accepted",
        "officialReference": "https://example.invalid/fixture-certifier",
        "standardReferences": ["fixture-standard-v1"],
        "recognitionReferences": ["fixture-qualified-review"],
        "allowedMatchKinds": ["exact-gtin", "explicit-product-list", "exact-batch"],
        "maxRecheckAgeDays": 180,
        "sourceApprovals": [{
            "sourceKey": "synthetic-certifier",
            "automated": False,
            "approvalReference": "fixture:synthetic-certifier",
            "credentialNames": [],
        }],
        "reviewerID": "reviewer:qualified-fixture",
        "reviewedAt": "2026-07-01T00:00:00Z",
        "nextReviewAt": "2027-07-01T00:00:00Z",
        "limitations": "Synthetic test registry entry; no real certifier is admitted.",
        "allowedAppWording": "Synthetic fixture certification only.",
    }
    entry.update(entry_overrides)
    return {
        "schemaVersion": 1,
        "registryVersion": "fixture-1.0.0" if False else "1.0.0",
        "defaultDecision": "review-required",
        "reviewedAt": "2026-08-01T00:00:00Z",
        "entries": [entry],
    }


def analysis():
    return {
        "schemaVersion": 1,
        "methodologyVersion": "1.0.0",
        "gtin": GTIN,
        "market": "DE",
        "ingredientObservationID": INGREDIENT_ID,
        "ingredientContentHash": "a" * 64,
        "sourceLanguage": "en",
        "sourceText": "Water, oats",
        "sourceTextSha256": "b" * 64,
        "freshnessState": "fresh",
        "conflictFlags": [],
        "parserStatus": "unknown",
        "candidateFindings": [],
        "reviewQueues": [{
            "id": "positive-ingredient-review",
            "reasons": ["no-parser-candidate-human-review-required"],
            "checklist": ["Explicit fixture checklist"],
            "ingredientObservationID": INGREDIENT_ID,
            "ingredientContentHash": "a" * 64,
        }],
        "safetyFlags": [],
        "analysisSha256": "c" * 64,
    }


def review_input():
    return {
        "decision": "halal-certified",
        "reviewerID": "reviewer:certification-fixture",
        "reviewedAt": "2026-08-30T12:00:00Z",
        "nextReviewAt": "2027-02-28T12:00:00Z",
        "limitations": "Synthetic registry-policy test.",
        "reason": "Exact fixture certification and current ingredient observation were explicitly reviewed.",
        "resolvedQueues": {"positive-ingredient-review": [INGREDIENT_ID]},
        "additionalEvidenceIDs": [],
    }


def certification(**overrides):
    value = {
        "id": CERT_ID,
        "gtin": GTIN,
        "market": "DE",
        "certifier": "Fixture Certifier",
        "scheme": "fixture-scheme",
        "certificateReference": "FIXTURE-CERT-1",
        "matchBasis": "exact-gtin",
        "scope": "Exact fixture product only",
        "sourceKey": "synthetic-certifier",
        "sourceRecordID": "fixture-cert-1",
        "retrievedAt": "2026-08-20T00:00:00Z",
        "lastCheckedAt": "2026-08-20T00:00:00Z",
        "effectiveAt": "2026-01-01T00:00:00Z",
        "expiryAt": "2027-01-01T00:00:00Z",
        "evidenceHash": "d" * 64,
    }
    value.update(overrides)
    return value


def binding(cert=None, ingredient_id=INGREDIENT_ID):
    cert = cert or certification()
    return {
        "certificationID": cert["id"],
        "ingredientObservationID": ingredient_id,
        "matchBasis": cert["matchBasis"],
    }


def certified_envelope(cert=None, *, selection_ingredient=INGREDIENT_ID, assessment_ingredient=INGREDIENT_ID, conflicts=None):
    cert = cert or certification()
    assessment = {
        "id": ASSESSMENT_ID,
        "gtin": GTIN,
        "market": "DE",
        "status": "halal-certified",
        "methodologyVersion": "1.0.0",
        "ingredientObservationID": assessment_ingredient,
        "certificationIDs": [cert["id"]],
    }
    return {
        "certifications": [cert],
        "assessments": [assessment],
        "currentSelections": [{
            "gtin": GTIN,
            "market": "DE",
            "ingredientObservationID": selection_ingredient,
            "assessmentID": ASSESSMENT_ID,
            "certificationIDs": [cert["id"]],
            "conflictFlags": conflicts or [],
        }],
    }


class RegistryValidationTests(unittest.TestCase):
    def test_committed_production_registry_admits_no_real_certifier(self):
        REGISTRY.validate_registry(PRODUCTION_REGISTRY, now=datetime(2026, 9, 2, tzinfo=timezone.utc))
        self.assertEqual(PRODUCTION_REGISTRY["entries"], [])
        self.assertEqual(PRODUCTION_REGISTRY["defaultDecision"], "review-required")

    def test_automated_source_requires_separate_source_policy_reference(self):
        registry = accepted_registry()
        registry["entries"][0]["sourceApprovals"][0].update({
            "automated": True,
            "approvalReference": "issue-only",
            "credentialNames": ["FIXTURE_API_KEY"],
        })
        with self.assertRaisesRegex(REGISTRY.RegistryError, "separate source-policy"):
            REGISTRY.validate_registry(registry, now=datetime(2026, 9, 2, tzinfo=timezone.utc))

    def test_duplicate_evidence_identifiers_fail_closed(self):
        registry = accepted_registry()
        registry["entries"].append(copy.deepcopy(registry["entries"][0]))
        registry["entries"][1]["certifierKey"] = "other-key"
        registry["entries"][1]["schemeKey"] = "other-scheme-key"
        with self.assertRaisesRegex(REGISTRY.RegistryError, "duplicates evidence"):
            REGISTRY.validate_registry(registry, now=datetime(2026, 9, 2, tzinfo=timezone.utc))


class CertificateEligibilityTests(unittest.TestCase):
    def result(self, cert=None, registry=None, ingredient_id=INGREDIENT_ID):
        cert = cert or certification()
        return REGISTRY.certificate_eligibility(
            cert,
            registry or accepted_registry(),
            gtin=GTIN,
            market="DE",
            ingredient_observation_id=ingredient_id,
            at=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
            binding=binding(cert, ingredient_id),
        )

    def test_exact_current_certificate_is_eligible_without_creating_a_ruling(self):
        result = self.result()
        self.assertTrue(result["eligible"])
        self.assertEqual(result["reasons"], [])
        self.assertEqual(result["derivedStatus"], "active")

    def test_unregistered_certifier_fails_closed(self):
        cert = certification(certifier="Unknown Body")
        result = self.result(cert)
        self.assertFalse(result["eligible"])
        self.assertIn("certifier-scheme-not-registered", result["reasons"])

    def test_name_brand_facility_and_logo_matches_never_qualify(self):
        for match_basis in ("name-only", "brand-only", "facility-only", "logo-only"):
            with self.subTest(match_basis=match_basis):
                cert = certification(matchBasis=match_basis)
                result = self.result(cert)
                self.assertFalse(result["eligible"])
                self.assertIn("certificate-match-not-exact", result["reasons"])

    def test_wrong_gtin_market_source_or_formulation_fail_closed(self):
        cases = [
            (certification(gtin="04006381333948"), INGREDIENT_ID, "certificate-gtin-mismatch"),
            (certification(market="FR"), INGREDIENT_ID, "certificate-market-mismatch"),
            (certification(sourceKey="other-source"), INGREDIENT_ID, "certificate-source-not-approved"),
        ]
        for cert, ingredient_id, reason in cases:
            with self.subTest(reason=reason):
                result = self.result(cert, ingredient_id=ingredient_id)
                self.assertFalse(result["eligible"])
                self.assertIn(reason, result["reasons"])
        cert = certification()
        result = REGISTRY.certificate_eligibility(
            cert,
            accepted_registry(),
            gtin=GTIN,
            market="DE",
            ingredient_observation_id="hfeu:ingredient:sha256:" + "9" * 64,
            at=datetime(2026, 8, 30, 12, tzinfo=timezone.utc),
            binding=binding(cert, INGREDIENT_ID),
        )
        self.assertIn("certificate-formulation-mismatch", result["reasons"])

    def test_expired_revoked_suspended_not_effective_and_stale_checks_fail(self):
        cases = [
            (certification(expiryAt="2026-08-01T00:00:00Z"), "certificate-expired"),
            (certification(revokedAt="2026-08-15T00:00:00Z"), "certificate-revoked"),
            (certification(suspendedAt="2026-08-15T00:00:00Z"), "certificate-suspended"),
            (certification(effectiveAt="2026-09-15T00:00:00Z"), "certificate-not-yet-effective"),
            (certification(lastCheckedAt="2026-01-01T00:00:00Z"), "certificate-recheck-stale"),
        ]
        for cert, reason in cases:
            with self.subTest(reason=reason):
                result = self.result(cert)
                self.assertFalse(result["eligible"])
                self.assertIn(reason, result["reasons"])

    def test_blocked_revoked_or_review_expired_registry_entry_fails(self):
        for state in ("review-required", "blocked", "revoked"):
            with self.subTest(state=state):
                result = self.result(registry=accepted_registry(state=state))
                self.assertIn(f"certifier-state-{state}", result["reasons"])
        result = self.result(registry=accepted_registry(nextReviewAt="2026-08-01T00:00:00Z"))
        self.assertIn("certifier-review-expired", result["reasons"])

    def test_missing_evidence_hash_is_not_independently_reviewable(self):
        cert = certification()
        cert.pop("evidenceHash")
        result = self.result(cert)
        self.assertIn("certificate-evidence-hash-missing", result["reasons"])


class CertifiedReviewTests(unittest.TestCase):
    def test_production_empty_registry_cannot_create_certified_assessment(self):
        with self.assertRaises(MethodologyError):
            REGISTRY.complete_review_with_registry(
                report=analysis(), methodology=METHODOLOGY, review_input=review_input(),
                certifications=[certification()], registry=PRODUCTION_REGISTRY,
            )

    def test_synthetic_accepted_registry_creates_explicit_formulation_binding(self):
        result = REGISTRY.complete_review_with_registry(
            report=analysis(), methodology=METHODOLOGY, review_input=review_input(),
            certifications=[certification()], registry=accepted_registry(),
        )
        self.assertEqual(result["assessment"]["status"], "halal-certified")
        self.assertEqual(result["assessment"]["certificationIDs"], [CERT_ID])
        artifact = result["reviewArtifact"]
        self.assertEqual(artifact["certifierRegistryVersion"], "1.0.0")
        self.assertEqual(artifact["certificationBindings"], [binding()])
        self.assertEqual(len(artifact["certifierRegistrySha256"]), 64)

    def test_non_certified_review_does_not_require_registry_admission(self):
        review = review_input()
        review["decision"] = "unknown"
        result = REGISTRY.complete_review_with_registry(
            report=analysis(), methodology=METHODOLOGY, review_input=review,
            certifications=[], registry=PRODUCTION_REGISTRY,
        )
        self.assertEqual(result["assessment"]["status"], "unknown")


class CertificationInvalidationTests(unittest.TestCase):
    def test_formulation_change_invalidates_prior_certified_binding(self):
        envelope = certified_envelope(selection_ingredient="hfeu:ingredient:sha256:" + "9" * 64)
        report = REGISTRY.certification_status_report(
            envelope=envelope, registry=accepted_registry(), evaluated_at="2026-08-30T12:00:00Z"
        )
        self.assertEqual(report["invalidated"], 1)
        self.assertIn("certificate-formulation-mismatch", report["decisions"][0]["reasons"])

    def test_revocation_and_registry_state_changes_invalidate_deterministically(self):
        for cert, registry, reason in (
            (certification(revokedAt="2026-08-29T00:00:00Z"), accepted_registry(), "certificate-revoked"),
            (certification(), accepted_registry(state="revoked"), "certifier-state-revoked"),
        ):
            with self.subTest(reason=reason):
                first = REGISTRY.certification_status_report(
                    envelope=certified_envelope(cert), registry=registry, evaluated_at="2026-08-30T12:00:00Z"
                )
                second = REGISTRY.certification_status_report(
                    envelope=certified_envelope(cert), registry=registry, evaluated_at="2026-08-30T12:00:00Z"
                )
                self.assertEqual(first, second)
                self.assertIn(reason, first["decisions"][0]["reasons"])

    def test_validity_event_is_immutable_and_reasoned(self):
        report = REGISTRY.certification_status_report(
            envelope=certified_envelope(certification(expiryAt="2026-08-01T00:00:00Z")),
            registry=accepted_registry(), evaluated_at="2026-08-30T12:00:00Z",
        )
        events = REGISTRY.validity_events_from_status_report(report)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "invalidated")
        self.assertIn("certificate-expired", events[0]["reason"])
        self.assertTrue(events[0]["id"].startswith("hfeu:validity-event:sha256:"))

    def test_certification_invalidation_merges_with_methodology_migration(self):
        migration = {
            "schemaVersion": 1,
            "methodologyVersion": "1.0.0",
            "decisions": [{
                "gtin": GTIN, "market": "DE", "assessmentID": ASSESSMENT_ID,
                "action": "carry-forward", "reasons": [],
            }],
            "invalidated": 0,
            "carriedForward": 1,
            "migrationSha256": "x" * 64,
        }
        status = REGISTRY.certification_status_report(
            envelope=certified_envelope(certification(expiryAt="2026-08-01T00:00:00Z")),
            registry=accepted_registry(), evaluated_at="2026-08-30T12:00:00Z",
        )
        merged = REGISTRY.merge_status_into_migration(migration, status)
        self.assertEqual(merged["invalidated"], 1)
        self.assertEqual(merged["carriedForward"], 0)
        self.assertEqual(merged["decisions"][0]["action"], "invalidate")
        self.assertIn("certificate-expired", merged["decisions"][0]["reasons"])


if __name__ == "__main__":
    unittest.main()
