import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for name in ("catalog_health", "catalog_refresh_health"):
    path = ROOT / "Tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

CATALOG_HEALTH = sys.modules["catalog_health"]
REFRESH_HEALTH = sys.modules["catalog_refresh_health"]


def base_health():
    envelope = json.loads((ROOT / "Data" / "evidence" / "sample-evidence-v1.json").read_text(encoding="utf-8"))
    return CATALOG_HEALTH.build_health_report(
        envelope=envelope,
        quality=None,
        change=None,
        benchmark=None,
        evaluated_at="2026-09-02T00:00:00Z",
        commit_sha="0123456789abcdef",
    )


def queue():
    value = {
        "schemaVersion": 1,
        "market": "DE",
        "evaluatedAt": "2026-09-02T00:00:00Z",
        "entries": [
            {
                "key": "stale:DE:12345678:ing1",
                "reason": "stale-ingredients",
                "priority": "high",
                "gtin": "12345678",
                "market": "DE",
                "evidenceID": "ing1",
                "detail": "stale",
            },
            {
                "key": "missing:DE:1234567890123:-",
                "reason": "missing-current-ingredients",
                "priority": "high",
                "gtin": "1234567890123",
                "market": "DE",
                "evidenceID": None,
                "detail": "missing",
            },
        ],
        "targetedExecution": {},
    }
    value["queueSha256"] = "a" * 64
    return value


def plan(due_reason="full-cadence-not-due"):
    return {
        "schemaVersion": 1,
        "policyVersion": "1.0.0",
        "sourceKey": "open-food-facts",
        "market": "DE",
        "lane": "targeted",
        "evaluatedAt": "2026-09-02T00:00:00Z",
        "requestedMode": "targeted",
        "fallbackReason": None,
        "fullDueAt": "2026-09-06T00:00:00Z",
        "fullDueReason": due_reason,
        "conditionalRequestHeaders": {},
        "deltaPredecessor": None,
        "targetedExecution": {
            "enabled": True,
            "endpointReference": "https://world.openfoodfacts.org/api/v3/product/{gtin}",
            "endpointHost": "world.openfoodfacts.org",
            "networkExecutionAllowed": False,
            "networkExecutionPerformed": False,
            "blockedReason": "target-endpoint-not-admitted-for-acquisition",
            "gtinCount": 2,
            "batches": [["12345678"], ["1234567890123"]],
            "maxGtinsPerRun": 400,
            "batchSize": 1,
            "maxRequestsPerMinute": 10,
            "minimumRequestIntervalSeconds": 7,
            "fields": ["code", "ingredients_text"],
        },
        "acceptedSnapshotID": "off-full-1",
        "acceptedContentSha256": "b" * 64,
        "planSha256": "c" * 64,
    }


class RefreshHealthTests(unittest.TestCase):
    def test_refresh_queue_is_visible_in_catalog_health(self):
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(),
            refresh_queue=queue(),
            refresh_plan=plan(),
        )
        self.assertEqual(report["schemaVersion"], 2)
        self.assertEqual(report["refresh"]["queue"]["entryCount"], 2)
        self.assertEqual(report["refresh"]["queue"]["reasonCounts"]["stale-ingredients"], 1)
        self.assertIn(
            "refresh:open-food-facts:queue:stale-ingredients",
            report["qualityGate"]["deduplicationKeys"],
        )
        REFRESH_HEALTH.validate_refresh_health(report)

    def test_nonincident_missing_ingredients_stays_visible_without_incident_spam(self):
        only_missing = queue()
        only_missing["entries"] = [only_missing["entries"][1]]
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(),
            refresh_queue=only_missing,
            refresh_plan=plan(),
        )
        self.assertEqual(report["refresh"]["queue"]["reasonCounts"], {"missing-current-ingredients": 1})
        self.assertEqual(report["refresh"]["deduplicationKeys"], [])

    def test_overdue_full_refresh_creates_stable_health_key(self):
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(),
            refresh_queue=queue(),
            refresh_plan=plan("full-cadence-due"),
        )
        self.assertIn("refresh:open-food-facts:full-overdue", report["refresh"]["deduplicationKeys"])

    def test_failed_scheduled_refresh_is_visible_through_health_incidents(self):
        workflow = {
            "conclusion": "failure",
            "runId": 12345,
            "event": "schedule",
            "updatedAt": "2026-09-02T03:30:00Z",
        }
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(),
            refresh_queue=queue(),
            refresh_plan=plan(),
            workflow_status=workflow,
        )
        key = "refresh:scheduled-catalog-refresh:failure"
        self.assertIn(key, report["refresh"]["deduplicationKeys"])
        self.assertIn(key, report["qualityGate"]["deduplicationKeys"])
        self.assertEqual(report["qualityGate"]["incident"]["action"], "investigate-refresh")

    def test_partial_or_blocked_attempt_is_health_incident_not_freshness(self):
        refresh_report = {
            "schemaVersion": 1,
            "sourceKey": "open-food-facts",
            "snapshotID": "off-partial",
            "mode": "sample",
            "evaluatedAt": "2026-09-02T00:00:00Z",
            "attemptStatus": "partial",
            "qualityStatus": "blocked",
            "candidateEligible": False,
            "candidateChangedFromAccepted": False,
            "acceptedSnapshotID": "off-full-1",
            "candidateSnapshotID": None,
            "queueCount": 2,
            "reportSha256": "d" * 64,
        }
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(),
            refresh_queue=queue(),
            refresh_plan=plan(),
            refresh_report=refresh_report,
        )
        self.assertEqual(report["refresh"]["acceptedSnapshotID"], "off-full-1")
        self.assertIn(
            "refresh:open-food-facts:attempt:partial:quality:blocked",
            report["refresh"]["deduplicationKeys"],
        )

    def test_target_endpoint_not_admitted_is_reported_but_not_incident_by_itself(self):
        only_missing = queue()
        only_missing["entries"] = [only_missing["entries"][1]]
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(),
            refresh_queue=only_missing,
            refresh_plan=plan(),
        )
        self.assertFalse(report["refresh"]["targeted"]["networkExecutionAllowed"])
        self.assertEqual(
            report["refresh"]["targeted"]["blockedReason"],
            "target-endpoint-not-admitted-for-acquisition",
        )
        self.assertEqual(report["refresh"]["deduplicationKeys"], [])

    def test_digest_detects_tampering(self):
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(),
            refresh_queue=queue(),
            refresh_plan=plan(),
        )
        report["refresh"]["queue"]["entryCount"] += 1
        with self.assertRaises(REFRESH_HEALTH.RefreshHealthError):
            REFRESH_HEALTH.validate_refresh_health(report)

    def test_human_summary_states_independent_refresh_clock(self):
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(),
            refresh_queue=queue(),
            refresh_plan=plan(),
        )
        summary = REFRESH_HEALTH.human_summary(report)
        self.assertIn("Refresh dates are independent", summary)
        self.assertIn("never freshens older evidence", summary)


if __name__ == "__main__":
    unittest.main()
