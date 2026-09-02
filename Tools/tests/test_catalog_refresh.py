import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from catalog_refresh import (
    RefreshError,
    digest_without,
    evaluate,
    promote_state,
    validate_policy,
)

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
        return promote_state(copy.deepcopy(POLICY), state)

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
        self.assertEqual("2026-09-02T00:00:00Z", state["nextFullDueAt"])

    def test_candidate_does_not_advance_accepted_freshness_clock(self):
        prior = self.accepted()
        metadata = copy.deepcopy(META)
        metadata.update(
            snapshotID="off-full-2",
            retrievedAt="2026-09-05T00:00:00Z",
            transportSha256="b" * 64,
        )
        state, report, _ = evaluate(
            policy=copy.deepcopy(POLICY),
            quality_policy=QUALITY_POLICY,
            source_policy=SOURCE_POLICY,
            acquisition=metadata,
            evidence=copy.deepcopy(EVIDENCE),
            quality=copy.deepcopy(QUALITY),
            change=None,
            previous=copy.deepcopy(prior),
            evaluated_at="2026-09-05T00:00:00Z",
        )
        self.assertEqual("off-full-1", state["acceptedComplete"]["snapshotID"])
        self.assertEqual("off-full-2", state["candidateComplete"]["snapshotID"])
        self.assertEqual("2026-09-09T00:00:00Z", state["nextFullDueAt"])
        self.assertEqual("2026-09-09T00:00:00Z", report["nextFullDueAt"])

    def test_protected_promotion_advances_full_cadence_from_candidate(self):
        prior = self.accepted()
        metadata = copy.deepcopy(META)
        metadata.update(
            snapshotID="off-full-2",
            retrievedAt="2026-09-05T00:00:00Z",
            transportSha256="b" * 64,
        )
        state, _, _ = evaluate(
            policy=copy.deepcopy(POLICY),
            quality_policy=QUALITY_POLICY,
            source_policy=SOURCE_POLICY,
            acquisition=metadata,
            evidence=copy.deepcopy(EVIDENCE),
            quality=copy.deepcopy(QUALITY),
            change=None,
            previous=copy.deepcopy(prior),
            evaluated_at="2026-09-05T00:00:00Z",
        )
        promoted = promote_state(copy.deepcopy(POLICY), state)
        self.assertEqual("off-full-2", promoted["acceptedComplete"]["snapshotID"])
        self.assertIsNone(promoted["candidateComplete"])
        self.assertFalse(promoted["candidateEligible"])
        self.assertEqual("2026-09-12T00:00:00Z", promoted["nextFullDueAt"])
        self.assertEqual(promoted["stateSha256"], digest_without(promoted, "stateSha256"))

    def test_same_content_new_snapshot_identifier_is_logical_noop(self):
        prior = self.accepted()
        metadata = copy.deepcopy(META)
        metadata.update(
            snapshotID="off-weekly-new-run-id",
            retrievedAt="2026-09-09T00:00:00Z",
        )
        state, _, _ = evaluate(
            policy=copy.deepcopy(POLICY),
            quality_policy=QUALITY_POLICY,
            source_policy=SOURCE_POLICY,
            acquisition=metadata,
            evidence=copy.deepcopy(EVIDENCE),
            quality=copy.deepcopy(QUALITY),
            change=None,
            previous=copy.deepcopy(prior),
            evaluated_at="2026-09-09T00:00:00Z",
        )
        self.assertFalse(state["candidateChangedFromAccepted"])
        self.assertFalse(state["candidateEligible"])
        self.assertIsNone(state["candidateComplete"])
        self.assertEqual("off-full-1", state["acceptedComplete"]["snapshotID"])
        self.assertEqual("2026-09-09T00:00:00Z", state["nextFullDueAt"])

    def test_partial_never_replaces_previous_complete_or_allows_deletion(self):
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
        self.assertFalse(report["deletionReconciliationAllowed"])
        self.assertTrue(report["noFalseFreshness"])

    def test_blocked_quality_never_replaces_previous_complete(self):
        prior = self.accepted()
        metadata = copy.deepcopy(META)
        metadata.update(snapshotID="off-full-2", transportSha256="b" * 64)
        quality = {
            "status": "blocked",
            "releaseBlockingFindings": [{"code": "SOURCE-TERMS"}],
        }
        state, report, queue = self.call(metadata, quality, prior)
        self.assertEqual("off-full-1", state["acceptedComplete"]["snapshotID"])
        self.assertIsNone(state["candidateComplete"])
        self.assertFalse(report["deletionReconciliationAllowed"])
        self.assertTrue(any(item["reason"] == "source-or-quality-blocker" for item in queue["entries"]))

    def test_same_snapshot_is_idempotent(self):
        prior = self.accepted()
        state, _, _ = self.call(previous=prior)
        self.assertFalse(state["candidateChangedFromAccepted"])
        self.assertEqual(prior["acceptedComplete"], state["acceptedComplete"])

    def test_delta_candidate_and_promotion_preserve_full_refresh_due(self):
        policy = copy.deepcopy(POLICY)
        policy["sources"]["open-food-facts"]["supportedAcquisitionModes"] = ["full", "delta"]
        initial, _, _ = evaluate(
            policy=copy.deepcopy(policy),
            quality_policy=QUALITY_POLICY,
            source_policy=SOURCE_POLICY,
            acquisition=copy.deepcopy(META),
            evidence=copy.deepcopy(EVIDENCE),
            quality=copy.deepcopy(QUALITY),
            change=None,
            previous=None,
            evaluated_at="2026-09-02T00:00:00Z",
        )
        prior = promote_state(copy.deepcopy(policy), initial)
        delta = copy.deepcopy(META)
        delta.update(
            snapshotID="off-delta-2",
            mode="delta",
            retrievedAt="2026-09-04T00:00:00Z",
            transportSha256="b" * 64,
            cursor="cursor-2",
            cursorExpiresAt="2026-09-20T00:00:00Z",
            predecessorSnapshotID="off-full-1",
        )
        state, _, _ = evaluate(
            policy=copy.deepcopy(policy),
            quality_policy=QUALITY_POLICY,
            source_policy=SOURCE_POLICY,
            acquisition=delta,
            evidence=copy.deepcopy(EVIDENCE),
            quality=copy.deepcopy(QUALITY),
            change=None,
            previous=copy.deepcopy(prior),
            evaluated_at="2026-09-04T00:00:00Z",
        )
        self.assertEqual("2026-09-09T00:00:00Z", state["nextFullDueAt"])
        promoted = promote_state(copy.deepcopy(policy), state)
        self.assertEqual("delta", promoted["acceptedComplete"]["mode"])
        self.assertEqual("2026-09-09T00:00:00Z", promoted["nextFullDueAt"])

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

    def test_changed_formulation_adds_mandatory_review_queue(self):
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

    def test_revoked_certificate_routes_dependent_product_for_review(self):
        evidence = copy.deepcopy(EVIDENCE)
        evidence["currentSelections"][0]["certificationIDs"] = ["cert1"]
        evidence["certifications"] = [
            {
                "id": "cert1",
                "effectiveAt": "2026-01-01T00:00:00Z",
                "expiryAt": "2027-01-01T00:00:00Z",
                "revokedAt": "2026-09-01T00:00:00Z",
                "lastCheckedAt": "2026-08-15T00:00:00Z",
            }
        ]
        _, _, queue = evaluate(
            policy=copy.deepcopy(POLICY),
            quality_policy=QUALITY_POLICY,
            source_policy=SOURCE_POLICY,
            acquisition=copy.deepcopy(META),
            evidence=evidence,
            quality=copy.deepcopy(QUALITY),
            change=None,
            previous=None,
            evaluated_at="2026-09-02T00:00:00Z",
        )
        self.assertTrue(
            any(
                item["reason"] == "certification-invalidated"
                and item["gtin"] == "12345678"
                for item in queue["entries"]
            )
        )

    def test_source_policy_identity_mismatch_fails_closed(self):
        with self.assertRaisesRegex(RefreshError, "source policy identity mismatch"):
            evaluate(
                policy=copy.deepcopy(POLICY),
                quality_policy=QUALITY_POLICY,
                source_policy={"schemaVersion": 1, "sourceKey": "open-prices"},
                acquisition=copy.deepcopy(META),
                evidence=copy.deepcopy(EVIDENCE),
                quality=copy.deepcopy(QUALITY),
                change=None,
                previous=None,
                evaluated_at="2026-09-02T00:00:00Z",
            )

    def test_state_digest_is_canonical(self):
        state, _, _ = self.call()
        self.assertEqual(state["stateSha256"], digest_without(state, "stateSha256"))


if __name__ == "__main__":
    unittest.main()
