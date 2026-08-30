import copy
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("catalog_quality_reporting", TOOLS / "catalog_quality_reporting.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

POLICY = {
    "freshness": {"retailer": {"anchorFields": ["observedAt", "snapshotAt"], "refreshRecommendedMonths": 1, "staleMonths": 3}},
    "sampling": {"seed": "quality-test", "baseSize": 25},
}


def envelope():
    return {
        "sources": [{"sourceKey": "open-food-facts", "reference": "https://world.openfoodfacts.org", "sourceClass": "open-database"}],
        "identities": [
            {"id": "i1", "gtin": "04006381333931", "market": "DE", "confidence": "high", "categories": ["snacks"], "sourceKey": "open-food-facts", "sourceRecordID": "r1"},
            {"id": "i2", "gtin": "04006381333948", "market": "DE", "confidence": "medium", "categories": ["drinks"], "sourceKey": "open-food-facts", "sourceRecordID": "r2"},
        ],
        "ingredients": [
            {"id": "g1", "sourceKey": "open-food-facts", "sourceRecordID": "r1", "sourceRevision": "1", "languageCode": "de", "verificationState": "human-verified", "captureMethod": "source-text", "observedAt": "2026-08-20T00:00:00Z"},
            {"id": "g2", "sourceKey": "open-food-facts", "sourceRecordID": "r2", "languageCode": "en", "verificationState": "unverified", "captureMethod": "ocr"},
        ],
        "assessments": [{"id": "a1", "status": "halal-reviewed", "methodologyVersion": "m1"}],
        "retailerEvidence": [{"id": "rt1", "kind": "retailer-observation", "retailerKey": "rewe", "observedAt": "2026-05-01T00:00:00Z"}],
        "currentSelections": [
            {"gtin": "04006381333931", "market": "DE", "identityObservationID": "i1", "ingredientObservationID": "g1", "assessmentID": "a1", "conflictFlags": [], "certificationIDs": []},
            {"gtin": "04006381333948", "market": "DE", "identityObservationID": "i2", "ingredientObservationID": "g2", "conflictFlags": ["source-conflict"], "certificationIDs": []},
        ],
    }


class ReportingTests(unittest.TestCase):
    def test_dimensions_and_strata_are_deterministic(self):
        base = {"evaluatedAt": "2026-08-30T12:00:00Z", "metrics": {}, "changes": {}, "auditSample": {}}
        change = {"addedSelections": [{"gtin": "04006381333948", "market": "DE"}], "reviewQueue": [{"gtin": "04006381333931", "market": "DE", "reason": "formulation-changed"}]}
        first = MODULE.augment_quality_report(copy.deepcopy(base), envelope(), change, POLICY)
        second = MODULE.augment_quality_report(copy.deepcopy(base), envelope(), change, POLICY)
        self.assertEqual(first, second)
        self.assertEqual(first["metrics"]["productsWithCurrentIngredients"], 2)
        self.assertEqual(first["metrics"]["identityConfidence"], {"high": 1, "medium": 1})
        self.assertEqual(first["metrics"]["retailerFreshnessByRetailerAndKind"]["rewe|retailer-observation"]["stale"], 1)
        self.assertIn("source:open-food-facts", first["auditSample"]["stratified"])
        self.assertIn("category:snacks", first["auditSample"]["stratified"])
        self.assertIn("status:halal-reviewed", first["auditSample"]["stratified"])
        self.assertIn("change:new", first["auditSample"]["stratified"])
        self.assertIn("change:changed", first["auditSample"]["stratified"])
        self.assertEqual(first["auditSample"]["mandatoryReviewCount"], 2)
        sample = first["auditSample"]["stratified"]["source:open-food-facts"][0]
        self.assertEqual(sample["recordReference"].split("/")[-1], sample["gtin"])

    def test_unknown_observation_date_remains_visible_in_dimensions(self):
        result = MODULE.augment_quality_report({"evaluatedAt": "2026-08-30T12:00:00Z", "metrics": {}, "changes": {}, "auditSample": {}}, envelope(), None, POLICY)
        self.assertEqual(result["metrics"]["currentIngredientsWithObservedAt"], 1)
        self.assertEqual(result["metrics"]["ingredientVerificationState"]["unverified"], 1)


if __name__ == "__main__":
    unittest.main()
