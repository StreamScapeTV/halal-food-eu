from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import basic_exclusions


class BasicExclusionsTests(unittest.TestCase):
    def report(self) -> dict:
        return {
            "schemaVersion": 1,
            "policyVersion": "1.0.0",
            "selected": [],
            "basicExclusions": [
                {
                    "gtin": "00000000000002",
                    "market": "DE",
                    "policyVersion": "1.0.0",
                    "reasonCode": "plain-basic-approved",
                },
                {
                    "gtin": "00000000000001",
                    "market": "DE",
                    "policyVersion": "1.0.0",
                    "reasonCode": "single-ingredient-basic",
                },
            ],
            "invalidExclusions": [],
            "report": {},
        }

    def test_projects_only_bounded_runtime_exclusions_in_deterministic_order(self):
        payload = basic_exclusions.project_selection_report(self.report())
        self.assertEqual(payload["schemaVersion"], 1)
        self.assertEqual(payload["selectionPolicyVersion"], "1.0.0")
        self.assertEqual(
            payload["records"],
            [
                {
                    "gtin": "00000000000001",
                    "market": "DE",
                    "reason": "single-ingredient-basic",
                },
                {
                    "gtin": "00000000000002",
                    "market": "DE",
                    "reason": "plain-basic-approved",
                },
            ],
        )
        self.assertNotIn("selected", payload)
        self.assertNotIn("invalidExclusions", payload)

    def test_rejects_policy_mismatch_inside_selection_decision(self):
        report = self.report()
        report["basicExclusions"][0]["policyVersion"] = "0.9.0"
        with self.assertRaisesRegex(basic_exclusions.BasicExclusionsError, "differs from selection report"):
            basic_exclusions.project_selection_report(report)

    def test_rejects_duplicate_gtin_market_rows(self):
        report = self.report()
        duplicate = dict(report["basicExclusions"][0])
        duplicate["reasonCode"] = "another-reason"
        report["basicExclusions"].append(duplicate)
        with self.assertRaisesRegex(basic_exclusions.BasicExclusionsError, "duplicate basic exclusion"):
            basic_exclusions.project_selection_report(report)

    def test_rejects_unknown_top_level_fields(self):
        report = self.report()
        report["downloadUrl"] = "https://example.invalid/raw"
        with self.assertRaisesRegex(basic_exclusions.BasicExclusionsError, "unexpected keys"):
            basic_exclusions.project_selection_report(report)

    def test_empty_payload_is_policy_bound(self):
        self.assertEqual(
            basic_exclusions.empty_payload("1.0.0"),
            {
                "schemaVersion": 1,
                "selectionPolicyVersion": "1.0.0",
                "records": [],
            },
        )


if __name__ == "__main__":
    unittest.main()
