import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("catalog_quality_source_review", ROOT / "Tools" / "catalog_quality_source_review.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
POLICY = json.loads((ROOT / "Data" / "quality" / "source-review-policy-v1.json").read_text(encoding="utf-8"))


def report(evaluated_at="2026-08-30T12:00:00Z"):
    return {
        "evaluatedAt": evaluated_at,
        "status": "pass",
        "releaseBlockingFindings": [],
        "sourceRights": {"licenseIdentifier": "ODbL"},
    }


class SourceReviewTests(unittest.TestCase):
    def test_committed_source_review_policy_is_valid(self):
        MODULE.validate_source_reviews(POLICY)

    def test_current_approved_review_passes(self):
        result = MODULE.enforce_source_review(report(), copy.deepcopy(POLICY), "open-food-facts")
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["sourceRights"]["termsReview"]["state"], "approved")

    def test_expired_review_blocks_release(self):
        result = MODULE.enforce_source_review(report("2027-08-30T00:00:00Z"), copy.deepcopy(POLICY), "open-food-facts")
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any(item["code"] == "source-terms-review-invalid" for item in result["releaseBlockingFindings"]))

    def test_revoked_review_blocks_release(self):
        policy = copy.deepcopy(POLICY)
        policy["sources"]["open-prices"]["state"] = "revoked"
        result = MODULE.enforce_source_review(report(), policy, "open-prices")
        self.assertEqual(result["status"], "blocked")

    def test_active_license_must_be_covered(self):
        policy = copy.deepcopy(POLICY)
        policy["sources"]["open-food-facts"]["licenseIdentifiers"] = ["different-license"]
        result = MODULE.enforce_source_review(report(), policy, "open-food-facts")
        self.assertEqual(result["status"], "blocked")


if __name__ == "__main__":
    unittest.main()
