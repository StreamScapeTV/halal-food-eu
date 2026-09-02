import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("catalog_health_incident", ROOT / "Tools" / "catalog_health_incident.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class HealthIncidentTests(unittest.TestCase):
    def report(self, keys=None):
        return {
            "commitSha": "abcdef0123456789",
            "evaluatedAt": "2026-09-02T00:00:00Z",
            "assessments": {"invalidatedOrBlockingCodes": ["certification-invalid"]},
            "qualityGate": {
                "incident": {"action": "rollback-and-quarantine"},
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

    def test_invalid_key_shape_fails_closed(self):
        report = self.report([""])
        with self.assertRaises(MODULE.HealthIncidentError):
            MODULE.plan_incidents(report)


if __name__ == "__main__":
    unittest.main()
