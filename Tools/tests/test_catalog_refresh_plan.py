import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("catalog_refresh_plan", ROOT / "Tools" / "catalog_refresh_plan.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

POLICY = json.loads((ROOT / "Data" / "refresh" / "catalog-refresh-policy-v1.json").read_text(encoding="utf-8"))
SOURCE_POLICY = json.loads((ROOT / "Data" / "sources" / "open-food-facts" / "source-policy-v1.json").read_text(encoding="utf-8"))


def previous_state():
    accepted = {
        "snapshotID": "off-full-1",
        "mode": "full",
        "status": "complete",
        "retrievedAt": "2026-08-30T00:00:00Z",
        "contentSha256": "a" * 64,
        "recordCount": 123,
        "upstream": {
            "etag": '"off-etag-1"',
            "lastModified": "Sun, 30 Aug 2026 00:00:00 GMT",
        },
        "adapterVersion": "1.0.0",
        "sourcePolicySha256": "b" * 64,
        "qualityStatus": "pass",
    }
    return {
        "schemaVersion": 1,
        "sourceKey": "open-food-facts",
        "market": "DE",
        "policyVersion": "1.0.0",
        "evaluatedAt": "2026-08-30T00:00:00Z",
        "acceptedComplete": accepted,
        "candidateComplete": None,
        "lastAttempt": accepted,
        "nextFullDueAt": "2026-09-06T00:00:00Z",
        "candidateEligible": False,
        "candidateChangedFromAccepted": False,
        "stateSha256": "c" * 64,
    }


def refresh_queue():
    return {
        "schemaVersion": 1,
        "sourceKey": "open-food-facts",
        "market": "DE",
        "entries": [
            {"key": "k2", "reason": "stale-ingredients", "priority": "high", "gtin": "1234567890123", "market": "DE"},
            {"key": "k1", "reason": "missing-current-ingredients", "priority": "high", "gtin": "12345678", "market": "DE"},
            {"key": "ignored", "reason": "source-or-quality-blocker", "priority": "high", "gtin": None, "market": None},
        ],
        "userEmail": "must-not-affect-target-plan@example.invalid",
    }


class RefreshPlanTests(unittest.TestCase):
    def build(self, *, policy=None, source_policy=None, lane="full", previous=None, queue=None, evaluated_at="2026-09-02T00:00:00Z"):
        return MODULE.build_plan(
            policy=copy.deepcopy(policy or POLICY),
            source_policy=copy.deepcopy(source_policy or SOURCE_POLICY),
            source_key="open-food-facts",
            lane=lane,
            evaluated_at=evaluated_at,
            previous=copy.deepcopy(previous),
            refresh_queue=copy.deepcopy(queue),
        )

    def test_schedule_and_manual_equivalent_inputs_produce_same_plan(self):
        state = previous_state()
        scheduled = self.build(previous=state)
        manual = self.build(previous=state)
        self.assertEqual(scheduled, manual)
        MODULE.validate_plan(scheduled)

    def test_supported_conditional_metadata_projects_exact_headers(self):
        plan = self.build(previous=previous_state())
        self.assertEqual(
            plan["conditionalRequestHeaders"],
            {
                "If-None-Match": '"off-etag-1"',
                "If-Modified-Since": "Sun, 30 Aug 2026 00:00:00 GMT",
            },
        )

    def test_unsupported_conditional_metadata_is_omitted(self):
        policy = copy.deepcopy(POLICY)
        policy["sources"]["open-food-facts"]["conditionalMetadata"] = []
        plan = self.build(policy=policy, previous=previous_state())
        self.assertEqual(plan["conditionalRequestHeaders"], {})

    def test_valid_reviewed_delta_cursor_selects_delta(self):
        policy = copy.deepcopy(POLICY)
        policy["sources"]["open-food-facts"]["supportedAcquisitionModes"] = ["full", "delta"]
        state = previous_state()
        state["acceptedComplete"]["cursor"] = "cursor-2"
        state["acceptedComplete"]["cursorExpiresAt"] = "2026-09-10T00:00:00Z"
        plan = self.build(policy=policy, lane="auto", previous=state)
        self.assertEqual(plan["requestedMode"], "delta")
        self.assertIsNone(plan["fallbackReason"])
        self.assertEqual(plan["deltaPredecessor"]["cursor"], "cursor-2")
        self.assertEqual(plan["deltaPredecessor"]["snapshotID"], "off-full-1")

    def test_expired_delta_cursor_falls_back_to_full(self):
        policy = copy.deepcopy(POLICY)
        policy["sources"]["open-food-facts"]["supportedAcquisitionModes"] = ["full", "delta"]
        state = previous_state()
        state["acceptedComplete"]["cursor"] = "cursor-2"
        state["acceptedComplete"]["cursorExpiresAt"] = "2026-09-01T00:00:00Z"
        plan = self.build(policy=policy, lane="auto", previous=state)
        self.assertEqual(plan["requestedMode"], "full")
        self.assertEqual(plan["fallbackReason"], "delta-cursor-expired")
        self.assertIsNone(plan["deltaPredecessor"])

    def test_missing_delta_cursor_falls_back_to_full(self):
        policy = copy.deepcopy(POLICY)
        policy["sources"]["open-food-facts"]["supportedAcquisitionModes"] = ["full", "delta"]
        plan = self.build(policy=policy, lane="auto", previous=previous_state())
        self.assertEqual(plan["requestedMode"], "full")
        self.assertEqual(plan["fallbackReason"], "delta-missing-cursor")

    def test_targeted_plan_is_bounded_deterministic_and_not_network_authority(self):
        first = self.build(lane="targeted", queue=refresh_queue())
        second = self.build(lane="targeted", queue=refresh_queue())
        self.assertEqual(first, second)
        target = first["targetedExecution"]
        self.assertTrue(target["enabled"])
        self.assertFalse(target["networkExecutionAllowed"])
        self.assertFalse(target["networkExecutionPerformed"])
        self.assertEqual(target["blockedReason"], "target-endpoint-not-admitted-for-acquisition")
        self.assertEqual(
            sorted(gtin for batch in target["batches"] for gtin in batch),
            ["12345678", "1234567890123"],
        )
        self.assertLessEqual(60 / target["minimumRequestIntervalSeconds"], target["maxRequestsPerMinute"])
        self.assertNotIn("userEmail", json.dumps(first))

    def test_targeted_network_permission_requires_explicit_acquisition_host(self):
        source_policy = copy.deepcopy(SOURCE_POLICY)
        source_policy["allowedAcquisitionHosts"].append("world.openfoodfacts.org")
        target = self.build(source_policy=source_policy, lane="targeted", queue=refresh_queue())["targetedExecution"]
        self.assertTrue(target["networkExecutionAllowed"])
        self.assertIsNone(target["blockedReason"])
        self.assertFalse(target["networkExecutionPerformed"])

    def test_invalid_target_rate_plan_fails_closed(self):
        policy = copy.deepcopy(POLICY)
        policy["sources"]["open-food-facts"]["targetedQueue"]["minimumRequestIntervalSeconds"] = 5
        with self.assertRaises(MODULE.RefreshPlanError):
            self.build(policy=policy, lane="targeted", queue=refresh_queue())

    def test_full_due_is_anchored_to_accepted_complete_not_evaluation_time(self):
        plan = self.build(previous=previous_state(), evaluated_at="2026-09-02T12:00:00Z")
        self.assertEqual(plan["fullDueAt"], "2026-09-06T00:00:00Z")
        self.assertEqual(plan["fullDueReason"], "full-cadence-not-due")
        later = self.build(previous=previous_state(), evaluated_at="2026-09-07T00:00:00Z")
        self.assertEqual(later["fullDueReason"], "full-cadence-due")


if __name__ == "__main__":
    unittest.main()
