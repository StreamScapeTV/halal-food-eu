from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/catalog-health.yml"


class CatalogRefreshHealthWorkflowTests(unittest.TestCase):
    def test_base_catalog_health_uses_committed_accepted_fixture_not_latest_refresh_candidate(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('BASE_EVIDENCE="Data/evidence/sample-evidence-v1.json"', text)
        self.assertIn('REFRESH_EVIDENCE="$RUNNER_TEMP/normalized/payload/evidence.json"', text)
        health_build = text.index("python3 Tools/catalog_health.py build")
        refresh_queue = text.index("python3 Tools/catalog_refresh.py queues")
        self.assertIn('--evidence "$BASE_EVIDENCE"', text[health_build:])
        self.assertIn('--evidence "$REFRESH_EVIDENCE"', text[refresh_queue:health_build])

    def test_refresh_plan_merges_protected_acceptance_with_latest_operational_artifact(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Tools/catalog_refresh_operational_state.py merge-previous", text)
        self.assertIn("--accepted-state Data/refresh/accepted-open-food-facts-v1.json", text)
        self.assertIn('--operational-state "$RUNNER_TEMP/refresh-state/payload/source-refresh-state.json"', text)
        self.assertIn('--previous-state "$RUNNER_TEMP/health/refresh-plan-state.json"', text)
        self.assertNotIn('--previous-state "$RUNNER_TEMP/refresh-state/payload/source-refresh-state.json"', text)

    def test_latest_refresh_artifacts_are_used_only_for_refresh_projection(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("HF-HEALTH-003/017", text)
        self.assertIn("latest source refresh is projected only into", text)
        self.assertIn('--refresh-report "$REFRESH_REPORT"', text)
        self.assertIn('--workflow-status "$RUNNER_TEMP/health/scheduled-off-status.json"', text)
        self.assertIn('--workflow-status "$RUNNER_TEMP/health/scheduled-open-prices-status.json"', text)


if __name__ == "__main__":
    unittest.main()
