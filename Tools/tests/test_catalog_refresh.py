import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog_refresh import RefreshError, digest_without, evaluate, validate_policy

POLICY = {
    "schemaVersion": 1,
    "policyVersion": "1.0.0",
    "market": "DE",
    "queue": {"maxEntries": 5000, "certificationDueDays": 30, "assessmentDueDays": 30},
    "sources": {
        "open-food-facts": {
            "adapterVersion": "1.0.0",
            "fullCadenceDays": 7,
            "targetedCadenceHours": 24,
            "supportedAcquisitionModes": ["full"],
            "conditionalMetadata": ["etag", "last-modified"],
            "targetedQueue": {
                "enabled": True,
                "endpointReference": "https://world.openfoodfacts.org/api/v2/search",
                "maxGtinsPerRun": 400,
                "batchSize": 40,
                "maxRequestsPerMinute": 10,
                "minimumRequestIntervalSeconds": 7,
                "fields": ["code", "ingredients_text"],
            },
        },
        "open-prices": {
            "adapterVersion": "1.0.0",
            "fullCadenceDays": 7,
            "targetedCadenceHours": None,
            "supportedAcquisitionModes": ["full"],
            "conditionalMetadata": ["etag", "lastModified"],
            "targetedQueue": {
                "enabled": False,
                "endpointReference": None,
                "maxGtinsPerRun": 0,
                "batchSize": 0,
                "maxRequestsPerMinute": 0,
                "minimumRequestIntervalSeconds": 0,
                "fields": [],
            },
        },
    },
}
QUALITY_POLICY = {"freshness": {"formulation": {"refreshRecommendedMonths": 9, "staleMonths": 12}}}
SOURCE_POLICY = {"schemaVersion": 1, "sourceKey": "open-food-facts"}
META = {
    "sourceKey": "open-food-facts",
    "snapshotID": "off-full-1",
    "mode": "full",
    "retrievedAt": "2026-09-02T00:00:00Z",
    "downloadComplete": True,
    "recordsEmitted": 2,
    "transportSha256": "a" * 64,
    "httpMetadata": {"etag": "e1", "last-modified": "Mon, 01 Sep 2026 00:00:00 GMT"},
}
QUALITY = {"status": "pass", "releaseBlockingFindings": []}
EVIDENCE = {
    "currentSelections": [
        {
            "id": "sel1",
            "gtin": "12345678",
            "market": "DE",
            "ingredientObservationID": "ing1",
            "assessmentID": "a1",
            "certificationIDs": [],
            "conflictFlags": [],
        },
        {
            "id": "sel2",
            "gtin": "1234567890123",
            "market": "DE",
            "ingredientObservationID": None,
            "assessmentID": None,
            "certificationIDs": [],
            "conflictFlags": ["identity-conflict"],
        },
    ],
    "ingredients": [{"id": "ing1", "observedAt": None}],
    "assessments": [{"id": "a1", "status": "questionable", "recheckAt": "2026-09-10T00:00:00Z"}],
    "certifications": [],
}


class RefreshTests(unittest.TestCase):
    def call(self, meta=META, quality=QUALITY, previous=None):
        return evaluate(
            policy=copy.deepcopy(POLICY),
            quality_policy=QUALITY_POLICY,
            source_policy=SOURCE_POLICY,
            acquisition=copy.deepcopy(meta),
            evidence=copy.deepcopy(EVIDENCE),
            quality=copy.deepcopy(quality),
            change=None,
            previous=copy.deepcopy(previous),
            evaluated_at="2026-09-02T00:00:00Z",
        )

    def accepted(self):
        state, _, _ = self.call()
        promoted = copy.deepcopy(state)
        promoted["acceptedComplete"] = copy.deepcopy(state["candidateComplete"])
        promoted["candidateComplete"] = None
        promoted["candidateEligible"] = False
        promoted["candidateChangedFromAccepted"] = False
        promoted["stateSha256"] = digest_without(promoted, "stateSha256")
        return promoted

    def test_policy_rejects_rate_limit_violation(self):
        policy = copy.deepcopy(POLICY)
        policy["sources"]["open-food-facts"]["targetedQueue"]["minimumRequestIntervalSeconds"] = 5
        with self.assertRaises(RefreshError):
            validate_policy(policy)

    def test_complete_passing_snapshot_is_only_candidate(self):
        state, report, queue = self.call()
        self.assertTrue(state["candidateChangedFromAccepted"])
        self.assertIsNone(state["acceptedComplete"])
        self.assertEqual("off-full-1", state["candidateComplete"]["snapshotID"])
        self.assertEqual("complete", report["attemptStatus"])
        self.assertGreaterEqual(len(queue["entries"]), 4)

    def test_partial_never_replaces_previous_complete(self):
        prior = self.accepted()
        metadata = copy.deepcopy(META)
        metadata.update(
            snapshotID="off-partial-2",
            downloadComplete=False,
            mode="sample",
            transportSha256="b" * 64,
        )
        state, report, _ = self.call(metadata, previous=prior)
        self.assertFalse(state["candidateChangedFromAccepted"])
        self.assertEqual("off-full-1", state["acceptedComplete"]["snapshotID"])
        self.assertIsNone(state["candidateComplete"])
        self.assertEqual("partial", report["attemptStatus"])

    def test_blocked_quality_never_replaces_previous_complete(self):
        prior = self.accepted()
        metadata = copy.deepcopy(META)
        metadata.update(snapshotID="off-full-2", transportSha256="b" * 64)
        quality = {
            "status": "blocked",
            "releaseBlockingFindings": [{"code": "SOURCE-TERMS"}],
        }
        state, _, queue = self.call(metadata, quality, prior)
        self.assertEqual("off-full-1", state["acceptedComplete"]["snapshotID"])
        self.assertIsNone(state["candidateComplete"])
        self.assertTrue(any(item["reason"] == "source-or-quality-blocker" for item in queue["entries"]))

    def test_same_snapshot_is_idempotent(self):
        prior = self.accepted()
        state, _, _ = self.call(previous=prior)
        self.assertFalse(state["candidateChangedFromAccepted"])
        self.assertEqual(prior["acceptedComplete"], state["acceptedComplete"])

    def test_queue_is_deterministic_and_bounded_rate_plan(self):
        _, _, first = self.call()
        _, _, second = self.call()
        self.assertEqual(first, second)
        plan = first["targetedExecution"]
        self.assertLessEqual(60 / plan["minimumRequestIntervalSeconds"], plan["maxRequestsPerMinute"])
        self.assertEqual(
            sorted({"12345678", "1234567890123"}),
            sorted(gtin for batch in plan["batches"] for gtin in batch),
        )
        self.assertFalse(plan["networkExecutionPerformed"])

    def test_queue_does_not_contain_user_identity(self):
        _, _, queue = self.call()
        text = json.dumps(queue)
        self.assertNotIn("email", text.lower())
        self.assertNotIn("userId", text)

    def test_change_report_adds_changed_unreviewed_queue(self):
        _, _, queue = evaluate(
            policy=copy.deepcopy(POLICY),
            quality_policy=QUALITY_POLICY,
            source_policy=SOURCE_POLICY,
            acquisition=copy.deepcopy(META),
            evidence=copy.deepcopy(EVIDENCE),
            quality=copy.deepcopy(QUALITY),
            change={
                "formulationChanges": 1,
                "reviewQueue": [
                    {
                        "gtin": "12345678",
                        "market": "DE",
                        "id": "change-1",
                        "reason": "formulation changed",
                    }
                ],
            },
            previous=None,
            evaluated_at="2026-09-02T00:00:00Z",
        )
        self.assertTrue(
            any(
                item["reason"] == "changed-unreviewed" and item["gtin"] == "12345678"
                for item in queue["entries"]
            )
        )

    def test_state_digest_is_canonical(self):
        state, _, _ = self.call()
        self.assertEqual(state["stateSha256"], digest_without(state, "stateSha256"))


if __name__ == "__main__":
    unittest.main()
