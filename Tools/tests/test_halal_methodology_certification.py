import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("halal_methodology_certification_test", TOOLS / "halal_methodology_core.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
METHODOLOGY = json.loads((ROOT / "Data" / "methodology" / "halal-methodology-v1.json").read_text(encoding="utf-8"))

GTIN = "04006381333931"
INGREDIENT_ID = "hfeu:ingredient:sha256:" + "1" * 64


def methodology_with_fixture_certifier():
    methodology = copy.deepcopy(METHODOLOGY)
    methodology["certificationPolicy"]["acceptedCertifiers"] = [{
        "certifier": "Fixture Certifier",
        "scheme": "fixture-scheme",
        "markets": ["DE"],
        "reviewedAt": "2026-01-01T00:00:00Z",
        "expiresAt": "2027-12-31T00:00:00Z",
    }]
    return methodology


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
        "limitations": "Synthetic certification-policy test.",
        "reason": "Exact fixture certification and ingredient observation were explicitly reviewed.",
        "resolvedQueues": {"positive-ingredient-review": [INGREDIENT_ID]},
        "additionalEvidenceIDs": [],
    }


def certification(**overrides):
    value = {
        "id": "hfeu:certification:sha256:" + "2" * 64,
        "gtin": GTIN,
        "market": "DE",
        "certifier": "Fixture Certifier",
        "scheme": "fixture-scheme",
        "effectiveAt": "2026-01-01T00:00:00Z",
        "expiryAt": "2027-01-01T00:00:00Z",
    }
    value.update(overrides)
    return value


class CertificationReviewTests(unittest.TestCase):
    def complete(self, cert):
        return MODULE.complete_review(
            report=analysis(),
            methodology=methodology_with_fixture_certifier(),
            review_input=review_input(),
            certifications=[cert],
        )

    def test_exact_current_accepted_certification_can_support_certified_status(self):
        result = self.complete(certification())
        self.assertEqual(result["assessment"]["status"], "halal-certified")
        self.assertEqual(result["assessment"]["certificationIDs"], [certification()["id"]])
        self.assertIn(certification()["id"], result["assessment"]["evidenceIDs"])

    def test_different_gtin_cannot_support_certified_status(self):
        with self.assertRaises(MODULE.MethodologyError):
            self.complete(certification(gtin="04006381333948"))

    def test_different_market_cannot_support_certified_status(self):
        with self.assertRaises(MODULE.MethodologyError):
            self.complete(certification(market="FR"))

    def test_expired_certification_cannot_support_certified_status(self):
        with self.assertRaises(MODULE.MethodologyError):
            self.complete(certification(expiryAt="2026-08-01T00:00:00Z"))

    def test_revoked_certification_cannot_support_certified_status(self):
        with self.assertRaises(MODULE.MethodologyError):
            self.complete(certification(revokedAt="2026-08-15T00:00:00Z"))

    def test_suspended_certification_cannot_support_certified_status(self):
        with self.assertRaises(MODULE.MethodologyError):
            self.complete(certification(suspendedAt="2026-08-15T00:00:00Z"))

    def test_unaccepted_scheme_cannot_support_certified_status(self):
        with self.assertRaises(MODULE.MethodologyError):
            self.complete(certification(scheme="unreviewed-scheme"))


if __name__ == "__main__":
    unittest.main()
