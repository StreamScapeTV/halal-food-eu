import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "catalog_refresh_workflow_status",
    ROOT / "Tools" / "catalog_refresh_workflow_status.py",
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def run(run_id, source, *, event="schedule", conclusion="success", created_at="2026-09-02T03:17:00Z", branch="main"):
    return {
        "id": run_id,
        "head_branch": branch,
        "event": event,
        "status": "completed",
        "conclusion": conclusion,
        "display_title": f"Catalog refresh {source}",
        "created_at": created_at,
        "updated_at": created_at,
        "head_sha": f"{run_id % 10}" * 40,
    }


class RefreshWorkflowStatusTests(unittest.TestCase):
    def test_latest_main_run_is_selected_for_exact_source_only(self):
        payload = {
            "workflow_runs": [
                run(10, "open-food-facts", branch="feature", conclusion="failure"),
                run(11, "open-food-facts", conclusion="failure", created_at="2026-09-02T03:17:00Z"),
                run(12, "open-prices", conclusion="success", created_at="2026-09-02T04:00:00Z"),
                run(13, "open-food-facts", event="pull_request", conclusion="success", created_at="2026-09-02T05:00:00Z"),
            ]
        }
        status = MODULE.latest_relevant_run(payload, "open-food-facts")
        self.assertTrue(status["available"])
        self.assertEqual(status["sourceKey"], "open-food-facts")
        self.assertEqual(status["runId"], "11")
        self.assertEqual(status["conclusion"], "failure")
        self.assertEqual(status["event"], "schedule")

    def test_other_source_success_cannot_hide_failed_source_run(self):
        payload = {
            "workflow_runs": [
                run(20, "open-food-facts", conclusion="failure", created_at="2026-09-02T03:17:00Z"),
                run(21, "open-prices", conclusion="success", created_at="2026-09-02T05:00:00Z"),
            ]
        }
        off = MODULE.latest_relevant_run(payload, "open-food-facts")
        prices = MODULE.latest_relevant_run(payload, "open-prices")
        self.assertEqual(off["runId"], "20")
        self.assertEqual(off["conclusion"], "failure")
        self.assertEqual(prices["runId"], "21")
        self.assertEqual(prices["conclusion"], "success")

    def test_newer_manual_recovery_supersedes_failed_schedule_for_same_source(self):
        payload = {
            "workflow_runs": [
                run(30, "open-food-facts", conclusion="failure", created_at="2026-09-02T03:17:00Z"),
                run(
                    31,
                    "open-food-facts",
                    event="workflow_dispatch",
                    conclusion="success",
                    created_at="2026-09-02T05:00:00Z",
                ),
                run(32, "open-prices", conclusion="failure", created_at="2026-09-02T06:00:00Z"),
            ]
        }
        status = MODULE.latest_relevant_run(payload, "open-food-facts")
        self.assertEqual(status["runId"], "31")
        self.assertEqual(status["conclusion"], "success")
        self.assertEqual(status["event"], "workflow_dispatch")

    def test_no_trusted_main_run_for_source_is_explicitly_unavailable(self):
        status = MODULE.latest_relevant_run(
            {"workflow_runs": [run(40, "open-prices")]},
            "open-food-facts",
        )
        self.assertFalse(status["available"])
        self.assertEqual(status["sourceKey"], "open-food-facts")
        self.assertIsNone(status["runId"])
        self.assertIsNone(status["conclusion"])

    def test_malformed_payload_fails_closed(self):
        with self.assertRaises(MODULE.WorkflowStatusError):
            MODULE.latest_relevant_run({"workflow_runs": "not-an-array"}, "open-food-facts")

    def test_invalid_source_key_fails_closed(self):
        with self.assertRaises(MODULE.WorkflowStatusError):
            MODULE.latest_relevant_run({"workflow_runs": []}, "../open-food-facts")


if __name__ == "__main__":
    unittest.main()
