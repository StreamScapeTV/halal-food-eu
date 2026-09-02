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


class RefreshWorkflowStatusTests(unittest.TestCase):
    def test_latest_main_schedule_or_manual_run_is_selected(self):
        payload = {
            "workflow_runs": [
                {
                    "id": 10,
                    "head_branch": "feature",
                    "event": "workflow_dispatch",
                    "status": "completed",
                    "conclusion": "failure",
                    "created_at": "2026-09-02T02:00:00Z",
                    "updated_at": "2026-09-02T02:10:00Z",
                    "head_sha": "a" * 40,
                },
                {
                    "id": 11,
                    "head_branch": "main",
                    "event": "schedule",
                    "status": "completed",
                    "conclusion": "success",
                    "created_at": "2026-09-02T03:17:00Z",
                    "updated_at": "2026-09-02T03:30:00Z",
                    "head_sha": "b" * 40,
                },
                {
                    "id": 12,
                    "head_branch": "main",
                    "event": "pull_request",
                    "status": "completed",
                    "conclusion": "failure",
                    "created_at": "2026-09-02T04:00:00Z",
                    "updated_at": "2026-09-02T04:01:00Z",
                    "head_sha": "c" * 40,
                },
            ]
        }
        status = MODULE.latest_relevant_run(payload)
        self.assertTrue(status["available"])
        self.assertEqual(status["runId"], "11")
        self.assertEqual(status["conclusion"], "success")
        self.assertEqual(status["event"], "schedule")

    def test_newer_manual_recovery_supersedes_failed_schedule(self):
        payload = {
            "workflow_runs": [
                {
                    "id": 20,
                    "head_branch": "main",
                    "event": "schedule",
                    "status": "completed",
                    "conclusion": "failure",
                    "created_at": "2026-09-02T03:17:00Z",
                    "updated_at": "2026-09-02T03:20:00Z",
                    "head_sha": "a" * 40,
                },
                {
                    "id": 21,
                    "head_branch": "main",
                    "event": "workflow_dispatch",
                    "status": "completed",
                    "conclusion": "success",
                    "created_at": "2026-09-02T05:00:00Z",
                    "updated_at": "2026-09-02T05:15:00Z",
                    "head_sha": "b" * 40,
                },
            ]
        }
        status = MODULE.latest_relevant_run(payload)
        self.assertEqual(status["runId"], "21")
        self.assertEqual(status["conclusion"], "success")
        self.assertEqual(status["event"], "workflow_dispatch")

    def test_no_trusted_main_run_is_explicitly_unavailable(self):
        status = MODULE.latest_relevant_run({"workflow_runs": []})
        self.assertFalse(status["available"])
        self.assertIsNone(status["runId"])
        self.assertIsNone(status["conclusion"])

    def test_malformed_payload_fails_closed(self):
        with self.assertRaises(MODULE.WorkflowStatusError):
            MODULE.latest_relevant_run({"workflow_runs": "not-an-array"})


if __name__ == "__main__":
    unittest.main()
