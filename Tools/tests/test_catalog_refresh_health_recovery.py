from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for name in ("catalog_health", "catalog_refresh_health"):
    if name in sys.modules:
        continue
    path = ROOT / "Tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)

CATALOG_HEALTH = sys.modules["catalog_health"]
REFRESH_HEALTH = sys.modules["catalog_refresh_health"]


def base_health(evaluated_at: str = "2026-09-06T08:42:28Z"):
    envelope = json.loads((ROOT / "Data/evidence/sample-evidence-v1.json").read_text(encoding="utf-8"))
    return CATALOG_HEALTH.build_health_report(
        envelope=envelope,
        quality=None,
        change=None,
        benchmark=None,
        evaluated_at=evaluated_at,
        commit_sha="0123456789abcdef",
    )


def queue():
    return {
        "schemaVersion": 1,
        "market": "DE",
        "evaluatedAt": "2026-09-06T08:42:28Z",
        "entries": [],
        "targetedExecution": {},
        "queueSha256": "a" * 64,
    }


def plan(reason: str = "no-successful-full-acquisition"):
    return {
        "schemaVersion": 1,
        "sourceKey": "open-food-facts",
        "requestedMode": "full",
        "fullDueAt": None,
        "fullDueReason": reason,
        "fallbackReason": None,
        "acceptedSnapshotID": None,
        "acceptedContentSha256": None,
        "targetedExecution": None,
    }


def policy():
    return json.loads((ROOT / "Data/refresh/catalog-refresh-policy-v1.json").read_text(encoding="utf-8"))


def workflow_text():
    return (ROOT / ".github/workflows/scheduled-catalog-refresh.yml").read_text(encoding="utf-8")


class RefreshRecoveryTests(unittest.TestCase):
    def test_recovery_projects_manual_workflow_and_next_source_cadence(self):
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(), refresh_queue=queue(), refresh_plan=plan()
        )
        recovery = report["refresh"]["operatorRecovery"]
        self.assertEqual(recovery["workflow"], "scheduled-catalog-refresh.yml")
        self.assertEqual(recovery["ref"], "main")
        self.assertTrue(recovery["workflowDispatch"])
        self.assertEqual(recovery["sources"]["open-food-facts"]["nextScheduledAt"], "2026-09-09T03:17:00Z")
        self.assertEqual(recovery["sources"]["open-prices"]["nextScheduledAt"], "2026-09-11T03:41:00Z")
        for source in recovery["sources"].values():
            self.assertEqual(source["mode"], "full")
            self.assertTrue(source["snapshotIDRequired"])
            self.assertTrue(source["catalogVersionMustBeEmpty"])
            self.assertEqual(source["fullCadenceDays"], 7)
        self.assertIn("refresh:open-food-facts:no-successful-full-acquisition", report["refresh"]["deduplicationKeys"])

    def test_recovery_derives_schedule_from_reviewed_workflow(self):
        changed = workflow_text().replace('cron: "17 3 * * 3"', 'cron: "18 3 * * 3"')
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(), refresh_queue=queue(), refresh_plan=plan(),
            refresh_policy=policy(), scheduled_workflow_text=changed,
        )
        off = report["refresh"]["operatorRecovery"]["sources"]["open-food-facts"]
        self.assertEqual(off["scheduleCronUTC"], "18 3 * * 3")
        self.assertEqual(off["nextScheduledAt"], "2026-09-09T03:18:00Z")

    def test_recovery_derives_explicit_source_schedule_when_workflow_changes_consistently(self):
        changed = workflow_text().replace("41 3 * * 5", "42 3 * * 5")
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(), refresh_queue=queue(), refresh_plan=plan(),
            refresh_policy=policy(), scheduled_workflow_text=changed,
        )
        prices = report["refresh"]["operatorRecovery"]["sources"]["open-prices"]
        self.assertEqual(prices["scheduleCronUTC"], "42 3 * * 5")
        self.assertEqual(prices["nextScheduledAt"], "2026-09-11T03:42:00Z")

    def test_recovery_fails_closed_when_source_mapping_drifts(self):
        changed = workflow_text().replace("SOURCE_KEY=open-prices", "SOURCE_KEY=open-food-facts", 1)
        with self.assertRaisesRegex(REFRESH_HEALTH.RefreshHealthError, "multiple cron entries"):
            REFRESH_HEALTH.enrich_health(
                base_health=base_health(), refresh_queue=queue(), refresh_plan=plan(),
                refresh_policy=policy(), scheduled_workflow_text=changed,
            )

    def test_recovery_fails_closed_when_policy_cadence_conflicts_with_weekly_schedule(self):
        changed = policy()
        changed["sources"]["open-food-facts"]["fullCadenceDays"] = 14
        with self.assertRaisesRegex(REFRESH_HEALTH.RefreshHealthError, "conflicts with policy cadence"):
            REFRESH_HEALTH.enrich_health(
                base_health=base_health(), refresh_queue=queue(), refresh_plan=plan(),
                refresh_policy=changed, scheduled_workflow_text=workflow_text(),
            )

    def test_unavailable_workflow_status_keeps_recovery_actionable_without_false_success(self):
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(),
            refresh_queue=queue(),
            refresh_plan=plan(),
            workflow_statuses=[{"schemaVersion": 1, "sourceKey": "open-food-facts", "available": False}],
        )
        self.assertFalse(report["refresh"]["scheduledWorkflows"]["open-food-facts"]["available"])
        self.assertEqual(report["refresh"]["operatorRecovery"]["workflow"], "scheduled-catalog-refresh.yml")
        self.assertIn("refresh:open-food-facts:no-successful-full-acquisition", report["refresh"]["deduplicationKeys"])
        self.assertFalse(any("scheduled-workflow:success" in key for key in report["refresh"]["deduplicationKeys"]))

    def test_summary_is_actionable_without_implying_success(self):
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(), refresh_queue=queue(), refresh_plan=plan()
        )
        summary = REFRESH_HEALTH.human_summary(report)
        self.assertIn("Manual recovery workflow: `scheduled-catalog-refresh.yml` from `main`", summary)
        self.assertIn("`source_key=open-food-facts`", summary)
        self.assertIn("leave `catalog_version` empty", summary)
        self.assertIn("`2026-09-09T03:17:00Z`", summary)
        self.assertIn("no-successful-full-acquisition", summary)

    def test_operator_recovery_is_covered_by_report_digest(self):
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(), refresh_queue=queue(), refresh_plan=plan()
        )
        report["refresh"]["operatorRecovery"]["sources"]["open-food-facts"]["nextScheduledAt"] = "2026-09-10T03:17:00Z"
        with self.assertRaisesRegex(REFRESH_HEALTH.RefreshHealthError, "digest mismatch"):
            REFRESH_HEALTH.validate_refresh_health(report)

    def test_v2_schema_requires_operator_recovery(self):
        schema = json.loads((ROOT / "Data/health/catalog-health-v2.schema.json").read_text(encoding="utf-8"))
        refresh = schema["properties"]["refresh"]
        operator = refresh["properties"]["operatorRecovery"]
        self.assertEqual(set(operator["required"]), {"workflow", "ref", "workflowDispatch", "sources"})
        source = operator["properties"]["sources"]["additionalProperties"]
        self.assertEqual(
            set(source["required"]),
            {"sourceKey", "mode", "snapshotIDRequired", "catalogVersionMustBeEmpty", "scheduleCronUTC", "fullCadenceDays", "nextScheduledAt"},
        )


if __name__ == "__main__":
    unittest.main()
