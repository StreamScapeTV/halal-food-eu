import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Tools"))

from catalog_refresh_submission_targets import derive, merge_queue


class SubmissionTargetTests(unittest.TestCase):
    def test_missing_admitted_directory_is_empty_not_error(self):
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(derive(Path(temporary) / "missing"), [])

    def test_owner_admitted_evidence_yields_only_gtin_market_queue_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proposal = {
                "schemaVersion": 1,
                "submissionID": "submission-123",
                "outputEvidence": {
                    "identities": [{"gtin": "04006381333931", "market": "DE", "name": "Example"}],
                    "ingredients": [{"gtin": "04006381333931", "market": "DE", "ingredientsText": "Water"}],
                    "certifications": [],
                },
                "admission": {"reviewerID": "must-not-leak"},
            }
            (root / "submission-123.json").write_text(json.dumps(proposal), encoding="utf-8")
            targets = derive(root)
            self.assertEqual(len(targets), 1)
            target = targets[0]
            self.assertEqual(target["reason"], "admitted-submission")
            self.assertEqual(target["gtin"], "04006381333931")
            self.assertEqual(target["market"], "DE")
            text = json.dumps(targets)
            self.assertNotIn("reviewerID", text)
            self.assertNotIn("submission-123", text)
            self.assertNotIn("Example", text)

    def test_invalid_or_non_gtin_observations_do_not_become_targets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            proposal = {
                "schemaVersion": 1,
                "submissionID": "submission-1",
                "outputEvidence": {
                    "identities": [
                        {"gtin": "not-a-gtin", "market": "DE"},
                        {"gtin": "04006381333931", "market": "de"},
                    ],
                    "ingredients": [],
                    "certifications": [],
                },
            }
            (root / "bad.json").write_text(json.dumps(proposal), encoding="utf-8")
            self.assertEqual(derive(root), [])

    def test_merge_is_deterministic_deduplicated_and_bounded(self):
        queue = {
            "schemaVersion": 1,
            "market": "DE",
            "evaluatedAt": "2026-09-02T00:00:00Z",
            "entries": [
                {"key": "stale:DE:00000000000001:x", "reason": "stale-ingredients", "priority": "high", "gtin": "00000000000001", "market": "DE", "evidenceID": "x", "detail": "stale"}
            ],
            "targetedExecution": {},
            "queueSha256": "0" * 64,
        }
        targets = [
            {"key": "admitted-submission:DE:04006381333931:-", "reason": "admitted-submission", "priority": "high", "gtin": "04006381333931", "market": "DE", "evidenceID": None, "detail": "admitted"},
            {"key": "admitted-submission:DE:04006381333931:-", "reason": "admitted-submission", "priority": "high", "gtin": "04006381333931", "market": "DE", "evidenceID": None, "detail": "admitted"},
        ]
        first = merge_queue(queue, targets, 2)
        second = merge_queue(queue, list(reversed(targets)), 2)
        self.assertEqual(first, second)
        self.assertEqual(len(first["entries"]), 2)
        self.assertEqual(len({item["key"] for item in first["entries"]}), 2)
        self.assertEqual(len(first["queueSha256"]), 64)


if __name__ == "__main__":
    unittest.main()
