import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Tools"))

from catalog_refresh_artifact_locator import select_latest


def artifacts(source="open-food-facts", suffix="100"):
    return [
        {"name": f"normalized-{source}-snap-{suffix}", "expired": False},
        {"name": f"changes-{source}-snap-{suffix}", "expired": False},
        {"name": f"quality-{source}-snap-{suffix}", "expired": False},
        {"name": f"refresh-state-{source}-snap-{suffix}", "expired": False},
        {"name": f"refresh-report-{source}-snap-{suffix}", "expired": False},
        {"name": f"refresh-queue-{source}-snap-{suffix}", "expired": False},
    ]


class RefreshArtifactLocatorTests(unittest.TestCase):
    def test_selects_latest_successful_main_run_with_complete_source_set(self):
        runs = [
            {
                "id": 100,
                "head_branch": "main",
                "event": "schedule",
                "conclusion": "success",
                "created_at": "2026-09-02T03:17:00Z",
                "updated_at": "2026-09-02T03:30:00Z",
                "head_sha": "a" * 40,
            },
            {
                "id": 101,
                "head_branch": "main",
                "event": "schedule",
                "conclusion": "success",
                "created_at": "2026-09-04T03:41:00Z",
                "updated_at": "2026-09-04T04:00:00Z",
                "head_sha": "b" * 40,
            },
        ]
        result = select_latest(
            runs,
            {100: artifacts(), 101: artifacts("open-prices", "101")},
            "open-food-facts",
        )
        self.assertTrue(result["available"])
        self.assertEqual(result["runId"], "100")
        self.assertTrue(result["artifacts"]["refreshQueue"].startswith("refresh-queue-open-food-facts-"))

    def test_newest_complete_source_set_wins(self):
        runs = [
            {
                "id": 100,
                "head_branch": "main",
                "event": "schedule",
                "conclusion": "success",
                "created_at": "2026-09-02T03:17:00Z",
            },
            {
                "id": 102,
                "head_branch": "main",
                "event": "workflow_dispatch",
                "conclusion": "success",
                "created_at": "2026-09-03T12:00:00Z",
            },
        ]
        result = select_latest(
            runs,
            {100: artifacts(suffix="100"), 102: artifacts(suffix="102")},
            "open-food-facts",
        )
        self.assertEqual(result["runId"], "102")

    def test_failed_newer_run_does_not_replace_last_usable_artifacts(self):
        runs = [
            {
                "id": 100,
                "head_branch": "main",
                "event": "schedule",
                "conclusion": "success",
                "created_at": "2026-09-02T03:17:00Z",
            },
            {
                "id": 103,
                "head_branch": "main",
                "event": "schedule",
                "conclusion": "failure",
                "created_at": "2026-09-09T03:17:00Z",
            },
        ]
        result = select_latest(runs, {100: artifacts(suffix="100")}, "open-food-facts")
        self.assertEqual(result["runId"], "100")

    def test_incomplete_or_expired_artifact_set_is_rejected(self):
        incomplete = artifacts()
        incomplete.pop()
        expired = artifacts(suffix="200")
        expired[-1]["expired"] = True
        runs = [
            {"id": 100, "head_branch": "main", "event": "schedule", "conclusion": "success", "created_at": "2026-09-02T03:17:00Z"},
            {"id": 200, "head_branch": "main", "event": "schedule", "conclusion": "success", "created_at": "2026-09-09T03:17:00Z"},
        ]
        result = select_latest(runs, {100: incomplete, 200: expired}, "open-food-facts")
        self.assertFalse(result["available"])
        self.assertEqual(result["artifacts"], {})

    def test_aggregate_artifacts_do_not_create_ambiguous_primary_set(self):
        values = artifacts()
        values.extend(
            [
                {"name": "normalized-open-food-facts-snap-100-aggregate", "expired": False},
                {"name": "changes-open-food-facts-snap-100-aggregate", "expired": False},
                {"name": "quality-open-food-facts-snap-100-aggregate", "expired": False},
            ]
        )
        runs = [
            {"id": 100, "head_branch": "main", "event": "workflow_dispatch", "conclusion": "success", "created_at": "2026-09-02T03:17:00Z"}
        ]
        result = select_latest(runs, {100: values}, "open-food-facts")
        self.assertTrue(result["available"])
        self.assertFalse(result["artifacts"]["normalized"].endswith("-aggregate"))


if __name__ == "__main__":
    unittest.main()
