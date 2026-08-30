import copy
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("catalog_quality", TOOLS / "catalog_quality.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class IncidentIdentityTests(unittest.TestCase):
    def test_blockers_get_stable_health_keys_and_quarantine_action(self):
        report = {
            "releaseBlockingFindings": [
                {"code": "unsafe-positive-inheritance"},
                {"code": "unsafe-positive-inheritance"},
                {"code": "parser-error-rate-exceeded"},
            ],
            "quarantineRequired": True,
            "rollbackRequired": True,
            "reportSha256": "old",
        }
        first = MODULE.decorate_incident(copy.deepcopy(report), "open-food-facts")
        second = MODULE.decorate_incident(copy.deepcopy(report), "open-food-facts")
        self.assertEqual(first, second)
        self.assertEqual(first["incident"]["action"], "rollback-and-quarantine")
        self.assertEqual(len(first["deduplicationKeys"]), 2)
        self.assertTrue(all(key.startswith("catalog-health-") for key in first["deduplicationKeys"]))
        self.assertEqual(first["incident"]["deduplicationKeys"], first["deduplicationKeys"])
        self.assertNotEqual(first["reportSha256"], "old")

    def test_clean_report_has_no_incident_identity(self):
        report = MODULE.decorate_incident({
            "releaseBlockingFindings": [],
            "quarantineRequired": False,
            "rollbackRequired": False,
        }, "open-prices")
        self.assertEqual(report["deduplicationKeys"], [])
        self.assertEqual(report["incident"]["action"], "none")


if __name__ == "__main__":
    unittest.main()
