import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = json.loads((ROOT / "Data" / "methodology" / "additive-identities-v1.json").read_text(encoding="utf-8"))
SOURCE_POLICY = json.loads((ROOT / "Data" / "sources" / "eu-additives" / "source-policy-v1.json").read_text(encoding="utf-8"))
SOURCE_REVIEWS = json.loads((ROOT / "Data" / "quality" / "source-review-policy-v1.json").read_text(encoding="utf-8"))


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise AssertionError(f"timestamp lacks timezone: {value}")
    return parsed.astimezone(timezone.utc)


class EUAdditiveReviewTimestampTests(unittest.TestCase):
    def test_review_metadata_is_effective_and_not_future_dated(self):
        now = datetime.now(timezone.utc)
        dataset_reviewed = timestamp(DATA["reviewedAt"])
        source_reviewed = timestamp(SOURCE_POLICY["updateDetection"]["lastReviewedAt"])
        terms_reviewed = timestamp(SOURCE_REVIEWS["sources"]["eu-additives"]["reviewedAt"])

        self.assertLessEqual(dataset_reviewed, now)
        self.assertLessEqual(source_reviewed, now)
        self.assertLessEqual(terms_reviewed, now)
        self.assertEqual(dataset_reviewed, source_reviewed)
        self.assertEqual(dataset_reviewed, terms_reviewed)

        self.assertGreater(timestamp(DATA["nextReviewAt"]), dataset_reviewed)
        self.assertGreater(timestamp(SOURCE_POLICY["updateDetection"]["nextReviewAt"]), source_reviewed)
        self.assertGreater(timestamp(SOURCE_REVIEWS["sources"]["eu-additives"]["expiresAt"]), terms_reviewed)

        for entry in DATA["entries"]:
            self.assertLessEqual(timestamp(entry["reviewedAt"]), dataset_reviewed, entry["id"])


if __name__ == "__main__":
    unittest.main()
