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


def job(name, *, conclusion="success", completed_at="2026-09-02T03:30:00Z"):
    return {
        "name": name,
        "status": "completed",
        "conclusion": conclusion,
        "completed_at": completed_at,
    }


def successful_refresh_jobs(*, marker=None, acquired_at="2026-09-02T03:20:00Z"):
    jobs = [
        job("policy / policy"),
        job("acquire / acquire", completed_at=acquired_at),
        job("normalize / normalize"),
        job("quality / quality"),
        job("refresh / refresh"),
    ]
    if marker is not None:
        jobs.append(job(marker))
    return {"jobs": jobs}


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

    def test_expired_artifact_historical_schedule_recovers_successful_full_lineage_from_jobs(self):
        status = MODULE.latest_relevant_run(
            {"workflow_runs": [run(34327267707, "open-food-facts", created_at="2026-09-09T08:05:57Z")]},
            "open-food-facts",
        )
        status = MODULE.apply_job_lineage(
            status,
            successful_refresh_jobs(acquired_at="2026-09-09T08:27:06Z"),
        )
        MODULE.validate_status(status)
        self.assertTrue(status["fullAcquisitionSucceeded"])
        self.assertEqual(status["mode"], "full")
        self.assertEqual(status["snapshotID"], "off-scheduled-34327267707")
        self.assertEqual(status["successfulFullAcquisitionAt"], "2026-09-09T08:27:06Z")
        self.assertEqual(status["completeness"], "complete")
        self.assertEqual(status["qualityStatus"], "pass")
        self.assertEqual(status["lineageSource"], "scheduled-job-graph")
        self.assertIsNone(status["sourceContentSha256"])

    def test_manual_full_recovery_requires_explicit_successful_lineage_marker(self):
        status = MODULE.latest_relevant_run(
            {"workflow_runs": [run(50, "open-food-facts", event="workflow_dispatch")]},
            "open-food-facts",
        )
        without_marker = MODULE.apply_job_lineage(status, successful_refresh_jobs())
        self.assertFalse(without_marker["fullAcquisitionSucceeded"])
        self.assertIsNone(without_marker["mode"])

        with_marker = MODULE.apply_job_lineage(
            status,
            successful_refresh_jobs(
                marker="refresh-lineage / open-food-facts / full / off-manual-50",
                acquired_at="2026-09-02T03:21:00Z",
            ),
        )
        MODULE.validate_status(with_marker)
        self.assertTrue(with_marker["fullAcquisitionSucceeded"])
        self.assertEqual(with_marker["snapshotID"], "off-manual-50")
        self.assertEqual(with_marker["lineageSource"], "job-marker")

    def test_fixture_marker_never_advances_full_acquisition_clock(self):
        status = MODULE.latest_relevant_run(
            {"workflow_runs": [run(60, "open-food-facts", event="workflow_dispatch")]},
            "open-food-facts",
        )
        status = MODULE.apply_job_lineage(
            status,
            successful_refresh_jobs(marker="refresh-lineage / open-food-facts / fixture / off-fixture-60"),
        )
        MODULE.validate_status(status)
        self.assertEqual(status["mode"], "fixture")
        self.assertFalse(status["fullAcquisitionSucceeded"])
        self.assertIsNone(status["successfulFullAcquisitionAt"])

    def test_failed_required_job_cannot_produce_successful_full_lineage(self):
        status = MODULE.latest_relevant_run(
            {"workflow_runs": [run(70, "open-food-facts")]},
            "open-food-facts",
        )
        jobs = successful_refresh_jobs()["jobs"]
        next(item for item in jobs if item["name"] == "quality / quality")["conclusion"] = "failure"
        status = MODULE.apply_job_lineage(status, {"jobs": jobs})
        MODULE.validate_status(status)
        self.assertFalse(status["fullAcquisitionSucceeded"])
        self.assertIsNone(status["completeness"])
        self.assertIsNone(status["qualityStatus"])

    def test_newer_failed_attempt_can_preserve_older_successful_full_lineage(self):
        successful = MODULE.latest_relevant_run(
            {"workflow_runs": [run(90, "open-food-facts", created_at="2026-09-09T03:17:00Z")]},
            "open-food-facts",
        )
        successful = MODULE.apply_job_lineage(
            successful,
            successful_refresh_jobs(acquired_at="2026-09-09T03:30:00Z"),
        )
        failed = MODULE.latest_relevant_run(
            {"workflow_runs": [run(91, "open-food-facts", conclusion="failure", created_at="2026-09-10T03:17:00Z")]},
            "open-food-facts",
        )
        jobs = successful_refresh_jobs()["jobs"]
        next(item for item in jobs if item["name"] == "refresh / refresh")["conclusion"] = "failure"
        failed = MODULE.apply_job_lineage(failed, {"jobs": jobs})
        combined = MODULE.apply_last_success(failed, successful)
        MODULE.validate_status(combined)
        self.assertFalse(combined["fullAcquisitionSucceeded"])
        self.assertEqual(combined["conclusion"], "failure")
        self.assertEqual(combined["lastSuccessfulFullSnapshotID"], "off-scheduled-90")
        self.assertEqual(combined["lastSuccessfulFullAcquisitionAt"], "2026-09-09T03:30:00Z")
        self.assertEqual(combined["lastSuccessfulFullRunId"], "90")

    def test_marker_source_mismatch_fails_closed(self):
        status = MODULE.latest_relevant_run(
            {"workflow_runs": [run(80, "open-food-facts", event="workflow_dispatch")]},
            "open-food-facts",
        )
        with self.assertRaisesRegex(MODULE.WorkflowStatusError, "source differs"):
            MODULE.apply_job_lineage(
                status,
                successful_refresh_jobs(marker="refresh-lineage / open-prices / full / off-manual-80"),
            )

    def test_scheduled_workflow_emits_metadata_only_lineage_marker(self):
        workflow = (ROOT / ".github/workflows/scheduled-catalog-refresh.yml").read_text(encoding="utf-8")
        self.assertIn("refresh-lineage:", workflow)
        self.assertIn("name: refresh-lineage / ${{ needs.trusted-default-branch.outputs.source_key }}", workflow)
        self.assertIn("needs: [trusted-default-branch, quality, refresh]", workflow)
        self.assertNotIn("upload-artifact", workflow[workflow.index("refresh-lineage:"):workflow.index("retailer-policy:")])


if __name__ == "__main__":
    unittest.main()
