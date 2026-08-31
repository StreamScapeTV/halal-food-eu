from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import github_configuration_health as health


class GitHubConfigurationHealthTests(unittest.TestCase):
    def setUp(self):
        self.healthy = {
            "schemaVersion": 1,
            "status": "healthy",
            "ownerInputRequired": False,
            "deduplicationKey": "hfeu:configuration-health:owner-input:v1",
            "publicConfiguration": {},
            "sources": [],
            "blockers": [],
        }
        self.blocked = {
            "schemaVersion": 1,
            "status": "blocked",
            "ownerInputRequired": True,
            "deduplicationKey": "hfeu:configuration-health:owner-input:v1",
            "publicConfiguration": {},
            "sources": [
                {
                    "sourceKey": "example-retailer",
                    "state": "enabled",
                    "authenticationMode": "oauth-client",
                    "requiredSecrets": [
                        {"name": "EXAMPLE_CLIENT_ID", "configured": True},
                        {"name": "EXAMPLE_CLIENT_SECRET", "configured": False},
                    ],
                }
            ],
            "blockers": [
                {
                    "sourceKey": "example-retailer",
                    "code": "required-credentials-not-configured",
                    "requiredSecretNames": ["EXAMPLE_CLIENT_ID", "EXAMPLE_CLIENT_SECRET"],
                    "missingSecretNames": ["EXAMPLE_CLIENT_SECRET"],
                }
            ],
        }

    def test_issue_body_contains_names_and_boolean_state_but_no_secret_values(self):
        body = health._issue_body(self.blocked)
        self.assertIn("EXAMPLE_CLIENT_ID", body)
        self.assertIn("EXAMPLE_CLIENT_SECRET", body)
        self.assertIn("| yes |", body)
        self.assertIn("| no |", body)
        self.assertNotIn("secret-value", body)
        self.assertIn(health.MARKER, body)

    def test_blocked_health_creates_deduplicated_owner_input_issue(self):
        calls = []

        def fake(method, url, token, payload=None):
            calls.append((method, url, payload))
            if method == "GET":
                return []
            return {"number": 77}

        with mock.patch.object(health, "_request_json", side_effect=fake):
            result = health.reconcile(self.blocked, "StreamScapeTV/halal-food-eu", "token")
        self.assertEqual(result, {"action": "created", "issueNumber": 77})
        create = next(item for item in calls if item[0] == "POST")
        self.assertEqual(create[2]["labels"], health.LABELS)
        self.assertNotIn("token", str(create[2]).lower())

    def test_repeated_blocked_health_updates_existing_issue_instead_of_duplicating(self):
        existing = {"number": 77, "state": "open", "body": health.MARKER + "\nold"}
        calls = []

        def fake(method, url, token, payload=None):
            calls.append((method, url, payload))
            if method == "GET":
                return [existing]
            return {"number": 77}

        with mock.patch.object(health, "_request_json", side_effect=fake):
            result = health.reconcile(self.blocked, "StreamScapeTV/halal-food-eu", "token")
        self.assertEqual(result["action"], "updated")
        self.assertEqual(sum(1 for method, _, _ in calls if method == "POST"), 0)
        self.assertEqual(sum(1 for method, _, _ in calls if method == "PATCH"), 1)

    def test_healthy_report_closes_open_owner_input_issue(self):
        existing = {"number": 77, "state": "open", "body": health.MARKER + "\nold"}
        calls = []

        def fake(method, url, token, payload=None):
            calls.append((method, url, payload))
            if method == "GET":
                return [existing]
            return {"number": 77}

        with mock.patch.object(health, "_request_json", side_effect=fake):
            result = health.reconcile(self.healthy, "StreamScapeTV/halal-food-eu", "token")
        self.assertEqual(result, {"action": "closed", "issueNumber": 77})
        close = next(item for item in calls if item[0] == "PATCH")
        self.assertEqual(close[2], {"state": "closed", "state_reason": "completed"})

    def test_healthy_report_without_existing_issue_is_noop(self):
        with mock.patch.object(health, "_request_json", return_value=[]):
            result = health.reconcile(self.healthy, "StreamScapeTV/halal-food-eu", "token")
        self.assertEqual(result, {"action": "none", "issueNumber": None})


if __name__ == "__main__":
    unittest.main()
