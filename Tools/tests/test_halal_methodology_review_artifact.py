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
SPEC = importlib.util.spec_from_file_location("halal_methodology_review_artifact_test", TOOLS / "halal_methodology_review_artifact.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
METHODOLOGY = json.loads((ROOT / "Data" / "methodology" / "halal-methodology-v1.json").read_text(encoding="utf-8"))


class ReviewArtifactTests(unittest.TestCase):
    def result(self, decision="halal-reviewed"):
        return {
            "reviewArtifact": {
                "decision": decision,
                "reviewArtifactSha256": "0" * 64,
            }
        }

    def analysis(self, *, prohibited=False):
        findings = []
        if prohibited:
            findings = [{"outcome": "prohibited-candidate", "reasonCode": "explicit-pork-ingredient"}]
        analysis = {
            "ingredientContentHash": "a" * 64,
            "candidateFindings": findings,
            "reviewQueues": [{"id": "positive-ingredient-review"}],
        }
        analysis["analysisSha256"] = MODULE.digest(analysis)
        return analysis

    def test_checklist_snapshot_is_stable_and_rehashes_artifact(self):
        first = MODULE.attach_checklist_snapshot(self.result(), self.analysis(), METHODOLOGY)
        second = MODULE.attach_checklist_snapshot(self.result(), self.analysis(), copy.deepcopy(METHODOLOGY))
        self.assertEqual(first, second)
        artifact = first["reviewArtifact"]
        self.assertEqual([item["queueID"] for item in artifact["checklists"]], ["positive-ingredient-review"])
        self.assertTrue(artifact["checklists"][0]["items"])
        self.assertEqual(artifact["ingredientContentHash"], "a" * 64)
        self.assertNotEqual(artifact["reviewArtifactSha256"], "0" * 64)

    def test_tampered_analysis_is_rejected_before_artifact_materialization(self):
        analysis = self.analysis()
        analysis["candidateFindings"].append({"outcome": "informational", "reasonCode": "tampered"})
        with self.assertRaises(MODULE.MethodologyError):
            MODULE.attach_checklist_snapshot(self.result(), analysis, METHODOLOGY)

    def test_missing_analysis_digest_is_rejected(self):
        analysis = self.analysis()
        analysis.pop("analysisSha256")
        with self.assertRaises(MODULE.MethodologyError):
            MODULE.attach_checklist_snapshot(self.result(), analysis, METHODOLOGY)

    def test_positive_review_is_rejected_when_prohibited_candidate_survives(self):
        with self.assertRaises(MODULE.MethodologyError):
            MODULE.attach_checklist_snapshot(self.result("halal-reviewed"), self.analysis(prohibited=True), METHODOLOGY)
        with self.assertRaises(MODULE.MethodologyError):
            MODULE.attach_checklist_snapshot(self.result("halal-certified"), self.analysis(prohibited=True), METHODOLOGY)

    def test_non_positive_review_can_preserve_prohibited_candidate_for_human_decision(self):
        result = MODULE.attach_checklist_snapshot(self.result("questionable"), self.analysis(prohibited=True), METHODOLOGY)
        self.assertEqual(result["reviewArtifact"]["decision"], "questionable")


if __name__ == "__main__":
    unittest.main()
