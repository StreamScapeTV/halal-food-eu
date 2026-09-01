from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONFIG_CI = ROOT / ".github/workflows/configuration-ci.yml"
HEALTH = ROOT / ".github/workflows/configuration-health.yml"
ACQUIRE = ROOT / ".github/workflows/acquire-catalog.yml"
POLICY = ROOT / "Tools/catalog_workflow_policy.py"


class ProjectConfigurationWorkflowTests(unittest.TestCase):
    def test_configuration_ci_is_pull_request_safe_and_secret_free(self):
        text = CONFIG_CI.read_text(encoding="utf-8")
        self.assertIn("pull_request:", text)
        self.assertIn("contents: read", text)
        self.assertNotIn("issues: write", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("vars.", text)
        self.assertIn("Tools/project_configuration.py validate", text)
        self.assertIn("test_*configuration*.py", text)

    def test_configuration_health_is_main_only_and_uses_only_automatic_github_token(self):
        text = HEALTH.read_text(encoding="utf-8")
        self.assertIn("schedule:", text)
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("pull_request:", text)
        self.assertIn("EXPECTED_REF: refs/heads/main", text)
        self.assertIn('test "$GITHUB_REF" = "$EXPECTED_REF"', text)
        self.assertIn("issues: write", text)
        self.assertIn("GITHUB_TOKEN: ${{ github.token }}", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("vars.", text)
        self.assertIn("Tools/github_configuration_health.py", text)

    def test_acquisition_uses_committed_open_food_facts_user_agent_instead_of_repository_variables(self):
        text = ACQUIRE.read_text(encoding="utf-8")
        self.assertNotIn("vars.OPEN_FOOD_FACTS_USER_AGENT", text)
        self.assertNotIn("vars.OPEN_FOOD_FACTS_CONTACT_EMAIL", text)
        self.assertIn("Tools/project_configuration.py get", text)
        self.assertIn("OPEN_FOOD_FACTS_USER_AGENT", text)

    def test_generic_workflow_policy_allows_issue_write_only_for_trusted_configuration_health(self):
        text = POLICY.read_text(encoding="utf-8")
        self.assertIn('"configuration-health.yml": {"issues"}', text)
        self.assertGreaterEqual(text.count('"configuration-health.yml"'), 3)


if __name__ == "__main__":
    unittest.main()
