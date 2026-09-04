import importlib.util
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("catalog_health_incident", ROOT / "Tools" / "catalog_health_incident.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class HealthIncidentTests(unittest.TestCase):
    def report(self, keys=None, action="rollback-and-quarantine"):
        return {
            "commitSha": "abcdef0123456789",
            "evaluatedAt": "2026-09-02T00:00:00Z",
            "assessments": {"invalidatedOrBlockingCodes": ["certification-invalid"]},
            "qualityGate": {
                "incident": {"action": action},
                "deduplicationKeys": keys or [],
            },
        }

    def test_no_blocker_keys_produces_no_incidents(self):
        self.assertEqual(MODULE.plan_incidents(self.report()), [])

    def test_keys_are_deduplicated_and_sorted(self):
        incidents = MODULE.plan_incidents(self.report(["health:z", "health:a", "health:a"]))
        self.assertEqual([item.key for item in incidents], ["health:a", "health:z"])
        self.assertIn("catalog-health-key:health:a", incidents[0].body)
        self.assertIn("certification-invalid", incidents[0].body)
        self.assertNotIn("ingredientsText", incidents[0].body)

    def test_correctness_incident_is_p0_and_blocked(self):
        incident = MODULE.plan_incidents(self.report(["quality:unsafe"]))[0]
        self.assertIn("priority:P0", incident.labels)
        self.assertIn("status:blocked", incident.labels)
        self.assertIn("type:data-quality", incident.labels)
        self.assertIn("area:observability", incident.labels)

    def test_refresh_incident_is_p1_and_source_classified(self):
        incident = MODULE.plan_incidents(
            self.report(["refresh:open-food-facts:no-successful-full-acquisition"], action="investigate-refresh")
        )[0]
        self.assertIn("priority:P1", incident.labels)
        self.assertIn("status:blocked", incident.labels)
        self.assertIn("area:sources", incident.labels)
        self.assertNotIn("status:in-progress", incident.labels)

    def test_invalid_key_shape_fails_closed(self):
        report = self.report([""])
        with self.assertRaises(MODULE.HealthIncidentError):
            MODULE.plan_incidents(report)

    def test_sync_create_applies_taxonomy_labels(self):
        calls = []

        def fake_request(method, url, token, payload=None):
            calls.append((method, url, payload))
            return {}

        with mock.patch.object(MODULE, "_existing_health_issues", return_value={}), mock.patch.object(
            MODULE, "_request", side_effect=fake_request
        ):
            result = MODULE.synchronize(
                self.report(
                    ["refresh:open-food-facts:no-successful-full-acquisition"],
                    action="investigate-refresh",
                ),
                "StreamScapeTV/halal-food-eu",
                "token",
            )

        self.assertEqual(result, {"created": 1, "updated": 0, "closed": 0})
        payload = calls[0][2]
        self.assertEqual(payload["labels"].count("priority:P1"), 1)
        self.assertEqual(payload["labels"].count("status:blocked"), 1)

    def test_sync_update_repairs_taxonomy_and_preserves_unmanaged_labels(self):
        key = "refresh:open-food-facts:no-successful-full-acquisition"
        existing = {
            key: {
                "number": 75,
                "state": "open",
                "body": f"<!-- catalog-health-key:{key} -->",
                "labels": [
                    {"name": "priority:P0"},
                    {"name": "status:done"},
                    {"name": "needs:owner-action"},
                ],
            }
        }
        calls = []

        def fake_request(method, url, token, payload=None):
            calls.append((method, url, payload))
            return {}

        with mock.patch.object(MODULE, "_existing_health_issues", return_value=existing), mock.patch.object(
            MODULE, "_request", side_effect=fake_request
        ):
            result = MODULE.synchronize(
                self.report([key], action="investigate-refresh"),
                "StreamScapeTV/halal-food-eu",
                "token",
            )

        self.assertEqual(result, {"created": 0, "updated": 1, "closed": 0})
        labels = calls[0][2]["labels"]
        self.assertIn("priority:P1", labels)
        self.assertIn("status:blocked", labels)
        self.assertIn("needs:owner-action", labels)
        self.assertNotIn("priority:P0", labels)
        self.assertNotIn("status:done", labels)

    def test_sync_resolution_sets_done_and_preserves_priority(self):
        key = "quality:unsafe"
        existing = {
            key: {
                "number": 99,
                "state": "open",
                "body": f"<!-- catalog-health-key:{key} -->",
                "labels": [
                    {"name": "priority:P0"},
                    {"name": "status:blocked"},
                    {"name": "area:security"},
                ],
            }
        }
        calls = []

        def fake_request(method, url, token, payload=None):
            calls.append((method, url, payload))
            return {}

        with mock.patch.object(MODULE, "_existing_health_issues", return_value=existing), mock.patch.object(
            MODULE, "_request", side_effect=fake_request
        ):
            result = MODULE.synchronize(
                self.report([], action="rollback-and-quarantine"),
                "StreamScapeTV/halal-food-eu",
                "token",
            )

        self.assertEqual(result, {"created": 0, "updated": 0, "closed": 1})
        payload = calls[0][2]
        self.assertEqual(payload["state"], "closed")
        self.assertEqual(payload["state_reason"], "completed")
        self.assertIn("priority:P0", payload["labels"])
        self.assertIn("status:done", payload["labels"])
        self.assertIn("area:security", payload["labels"])
        self.assertNotIn("status:blocked", payload["labels"])


if __name__ == "__main__":
    unittest.main()
