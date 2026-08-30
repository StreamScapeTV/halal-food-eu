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

METHODOLOGY_SPEC = importlib.util.spec_from_file_location("halal_methodology_integration", TOOLS / "halal_methodology_core.py")
assert METHODOLOGY_SPEC and METHODOLOGY_SPEC.loader
METHODOLOGY_MODULE = importlib.util.module_from_spec(METHODOLOGY_SPEC)
sys.modules[METHODOLOGY_SPEC.name] = METHODOLOGY_MODULE
METHODOLOGY_SPEC.loader.exec_module(METHODOLOGY_MODULE)

EVIDENCE_SPEC = importlib.util.spec_from_file_location("evidence_model_integration", TOOLS / "evidence_model.py")
assert EVIDENCE_SPEC and EVIDENCE_SPEC.loader
EVIDENCE_MODULE = importlib.util.module_from_spec(EVIDENCE_SPEC)
sys.modules[EVIDENCE_SPEC.name] = EVIDENCE_MODULE
EVIDENCE_SPEC.loader.exec_module(EVIDENCE_MODULE)

METHODOLOGY = json.loads((ROOT / "Data" / "methodology" / "halal-methodology-v1.json").read_text(encoding="utf-8"))
SAMPLE = json.loads((ROOT / "Data" / "evidence" / "sample-evidence-v1.json").read_text(encoding="utf-8"))


class MethodologyEvidenceIntegrationTests(unittest.TestCase):
    def test_explicit_review_records_validate_and_project_through_canonical_evidence_model(self):
        envelope = copy.deepcopy(SAMPLE)
        selection = next(item for item in envelope["currentSelections"] if item["gtin"] == "00200000000004")
        ingredient = next(item for item in envelope["ingredients"] if item["id"] == selection["ingredientObservationID"])
        analysis = METHODOLOGY_MODULE.analyze_ingredient(
            ingredient,
            METHODOLOGY,
            gtin=selection["gtin"],
            market=selection["market"],
            freshness_state="fresh",
            conflict_flags=[],
        )
        self.assertEqual({item["id"] for item in analysis["reviewQueues"]}, {"positive-ingredient-review"})
        result = METHODOLOGY_MODULE.complete_review(
            report=analysis,
            methodology=METHODOLOGY,
            review_input={
                "decision": "halal-reviewed",
                "reviewerID": "reviewer:integration-fixture",
                "reviewedAt": "2026-08-30T12:00:00Z",
                "nextReviewAt": "2027-02-28T12:00:00Z",
                "limitations": "Synthetic integration review only; not certification.",
                "reason": "Exact current synthetic ingredient observation explicitly reviewed for evidence-model compatibility.",
                "resolvedQueues": {"positive-ingredient-review": [ingredient["id"]]},
                "additionalEvidenceIDs": [],
            },
        )
        envelope["assessments"].append(result["assessment"])
        envelope["reviews"].append(result["review"])
        selection["assessmentID"] = result["assessment"]["id"]

        EVIDENCE_MODULE.validate_envelope(envelope)
        runtime = EVIDENCE_MODULE.runtime_projection(envelope)
        product = next(item for item in runtime["products"] if item["gtin"] == selection["gtin"])
        self.assertEqual(product["assessment"]["status"], "halal-reviewed")
        self.assertEqual(product["assessment"]["methodologyVersion"], "1.0.0")
        self.assertEqual(product["assessment"]["certificationIDs"], [])
        self.assertEqual(product["assessment"]["reasons"][0]["code"], "completed-methodology-review")
        self.assertIn(ingredient["id"], product["assessment"]["evidenceIDs"])


if __name__ == "__main__":
    unittest.main()
