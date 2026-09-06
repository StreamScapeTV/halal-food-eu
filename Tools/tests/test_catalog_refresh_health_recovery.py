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
        self.assertTrue(recovery["workflowDispatch"])
        self.assertEqual(recovery["sources"]["open-food-facts"]["nextScheduledAt"], "2026-09-09T03:17:00Z")
        self.assertEqual(recovery["sources"]["open-prices"]["nextScheduledAt"], "2026-09-11T03:41:00Z")
        for source in recovery["sources"].values():
            self.assertEqual(source["mode"], "full")
            self.assertTrue(source["snapshotIDRequired"])
            self.assertTrue(source["catalogVersionMustBeEmpty"])
            self.assertEqual(source["fullCadenceDays"], 7)
        self.assertIn("refresh:open-food-facts:no-successful-full-acquisition", report["refresh"]["deduplicationKeys"])

    def test_recovery_fails_closed_when_schedule_drifts(self):
        changed = workflow_text().replace('cron: "17 3 * * 3"', 'cron: "18 3 * * 3"')
        with self.assertRaisesRegex(REFRESH_HEALTH.RefreshHealthError, "cron set differs"):
            REFRESH_HEALTH.enrich_health(
                base_health=base_health(), refresh_queue=queue(), refresh_plan=plan(),
                refresh_policy=policy(), scheduled_workflow_text=changed,
            )

    def test_recovery_fails_closed_when_source_mapping_drifts(self):
        changed = workflow_text().replace("SOURCE_KEY=open-prices", "SOURCE_KEY=open-food-facts", 1)
        with self.assertRaisesRegex(REFRESH_HEALTH.RefreshHealthError, "source-to-cron mapping differs"):
            REFRESH_HEALTH.enrich_health(
                base_health=base_health(), refresh_queue=queue(), refresh_plan=plan(),
                refresh_policy=policy(), scheduled_workflow_text=changed,
            )

    def test_recovery_fails_closed_when_policy_cadence_drifts(self):
        changed = policy()
        changed["sources"]["open-food-facts"]["fullCadenceDays"] = 14
        with self.assertRaisesRegex(REFRESH_HEALTH.RefreshHealthError, "weekly full cadence"):
            REFRESH_HEALTH.enrich_health(
                base_health=base_health(), refresh_queue=queue(), refresh_plan=plan(),
                refresh_policy=changed, scheduled_workflow_text=workflow_text(),
            )

    def test_summary_is_actionable_without_implying_success(self):
        report = REFRESH_HEALTH.enrich_health(
            base_health=base_health(), refresh_queue=queue(), refresh_plan=plan()
        )
        summary = REFRESH_HEALTH.human_summary(report)
        self.assertIn("Manual recovery workflow: `scheduled-catalog-refresh.yml`", summary)
        self.assertIn("`source_key=open-food-facts`", summary)
        self.assertIn("leave `catalog_version` empty", summary)
        self.assertIn("`2026-09-09T03:17:00Z`", summary)
        self.assertIn("no-successful-full-acquisition", summary)

    def test_v2_schema_requires_operator_recovery(self):
        schema = json.loads((ROOT / "Data/health/catalog-health-v2.schema.json").read_text(encoding="utf-8"))
        refresh = schema["properties"]["refresh"]
        self.assertIn("operatorRecovery", refresh["required"])
        source = refresh["properties"]["operatorRecovery"]["properties"]["sources"]["additionalProperties"]
        self.assertEqual(
            set(source["required"]),
            {"sourceKey", "mode", "snapshotIDRequired", "catalogVersionMustBeEmpty", "scheduleCronUTC", "fullCadenceDays", "nextScheduledAt"},
        )


if __name__ == "__main__":
    unittest.main()
