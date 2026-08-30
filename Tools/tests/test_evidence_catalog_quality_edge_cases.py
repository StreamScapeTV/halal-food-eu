import copy
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
QUALITY_SPEC = importlib.util.spec_from_file_location("catalog_quality_core_edges", TOOLS / "catalog_quality_core.py")
assert QUALITY_SPEC and QUALITY_SPEC.loader
QUALITY = importlib.util.module_from_spec(QUALITY_SPEC)
sys.modules[QUALITY_SPEC.name] = QUALITY
QUALITY_SPEC.loader.exec_module(QUALITY)
BASE_SPEC = importlib.util.spec_from_file_location("catalog_quality_base_tests", Path(__file__).with_name("test_evidence_catalog_quality.py"))
assert BASE_SPEC and BASE_SPEC.loader
BASE = importlib.util.module_from_spec(BASE_SPEC)
sys.modules[BASE_SPEC.name] = BASE
BASE_SPEC.loader.exec_module(BASE)


class CatalogQualityEdgeCaseTests(unittest.TestCase):
    def evaluate(self, envelope):
        return QUALITY.evaluate_quality(
            policy=BASE.POLICY,
            envelope=envelope,
            source_key="synthetic-fixture",
            snapshot_id="fixture-1",
            change_report=BASE.change_report(),
            as_of=BASE.AS_OF,
        )

    def test_same_gtin_different_markets_keep_independent_formulation_clocks(self):
        envelope = BASE.base_envelope()
        fr_identity = copy.deepcopy(envelope["identities"][0])
        fr_identity["id"] = "identity-fr"
        fr_identity["market"] = "FR"
        fr_ingredient = copy.deepcopy(envelope["ingredients"][0])
        fr_ingredient["id"] = "ingredient-fr"
        fr_ingredient["market"] = "FR"
        fr_ingredient["observedAt"] = "2025-01-01T00:00:00Z"
        fr_selection = copy.deepcopy(envelope["currentSelections"][0])
        fr_selection["id"] = "selection-fr"
        fr_selection["market"] = "FR"
        fr_selection["identityObservationID"] = fr_identity["id"]
        fr_selection["ingredientObservationID"] = fr_ingredient["id"]
        envelope["identities"].append(fr_identity)
        envelope["ingredients"].append(fr_ingredient)
        envelope["currentSelections"].append(fr_selection)

        report = self.evaluate(envelope)
        self.assertEqual(report["metrics"]["products"], 2)
        self.assertEqual(report["metrics"]["formulationFreshness"]["fresh"], 1)
        self.assertEqual(report["metrics"]["formulationFreshness"]["stale"], 1)

    def test_revoked_certification_cannot_support_certified_status(self):
        envelope = BASE.base_envelope()
        gtin = envelope["identities"][0]["gtin"]
        cert = {
            "id": "cert-revoked",
            "gtin": gtin,
            "market": "DE",
            "lastCheckedAt": "2026-08-01T00:00:00Z",
            "effectiveAt": "2026-01-01T00:00:00Z",
            "expiryAt": "2027-01-01T00:00:00Z",
            "revokedAt": "2026-08-15T00:00:00Z",
        }
        assessment = {
            "id": "assessment-certified",
            "gtin": gtin,
            "market": "DE",
            "status": "halal-certified",
            "ingredientObservationID": envelope["ingredients"][0]["id"],
        }
        envelope["certifications"] = [cert]
        envelope["assessments"] = [assessment]
        envelope["reviews"] = [{"targetID": assessment["id"], "state": "approved", "reviewerID": "reviewer-a"}]
        envelope["currentSelections"][0]["assessmentID"] = assessment["id"]
        envelope["currentSelections"][0]["certificationIDs"] = [cert["id"]]

        report = self.evaluate(envelope)
        self.assertEqual(report["metrics"]["certificationState"]["revoked"], 1)
        self.assertTrue(any(item["code"] == "certification-invalid" for item in report["releaseBlockingFindings"]))

    def test_high_sample_defect_rate_widens_deterministic_audit_sample(self):
        envelope = BASE.base_envelope()
        identities = []
        ingredients = []
        selections = []
        for index in range(30):
            gtin = f"{index:014d}"
            identity = copy.deepcopy(envelope["identities"][0])
            identity["id"] = f"identity-{index}"
            identity["gtin"] = gtin
            ingredient = copy.deepcopy(envelope["ingredients"][0])
            ingredient["id"] = f"ingredient-{index}"
            ingredient["gtin"] = gtin
            ingredient.pop("observedAt", None)
            selection = copy.deepcopy(envelope["currentSelections"][0])
            selection["id"] = f"selection-{index}"
            selection["gtin"] = gtin
            selection["identityObservationID"] = identity["id"]
            selection["ingredientObservationID"] = ingredient["id"]
            identities.append(identity)
            ingredients.append(ingredient)
            selections.append(selection)
        envelope["identities"] = identities
        envelope["ingredients"] = ingredients
        envelope["currentSelections"] = selections

        report = self.evaluate(envelope)
        self.assertTrue(report["auditSample"]["escalated"])
        self.assertEqual(report["auditSample"]["defectRate"], 1.0)
        self.assertEqual(len(report["auditSample"]["base"]), 25)
        self.assertEqual(len(report["auditSample"]["selected"]), 30)


if __name__ == "__main__":
    unittest.main()
