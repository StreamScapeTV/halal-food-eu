import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("halal_methodology_batch_test", TOOLS / "halal_methodology_batch.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
METHODOLOGY = json.loads((ROOT / "Data" / "methodology" / "halal-methodology-v1.json").read_text(encoding="utf-8"))
EVIDENCE = json.loads((ROOT / "Data" / "evidence" / "sample-evidence-v1.json").read_text(encoding="utf-8"))


class BatchMethodologyTests(unittest.TestCase):
    def test_sample_envelope_produces_deterministic_product_reports(self):
        first = MODULE.analyze_envelope(envelope=EVIDENCE, methodology=METHODOLOGY)
        second = MODULE.analyze_envelope(envelope=EVIDENCE, methodology=METHODOLOGY)
        self.assertEqual(first, second)
        self.assertEqual(first["counts"]["products"], 2)
        self.assertEqual(len(first["products"]), 2)
        by_gtin = {item["gtin"]: item for item in first["products"]}
        dessert = by_gtin["00200000000028"]
        self.assertIn("formulation-changed", dessert["safetyFlags"])
        self.assertIn("new-changed-formulation", {item["id"] for item in dessert["reviewQueues"]})
        oat = by_gtin["00200000000004"]
        self.assertEqual(oat["parserStatus"], "unknown")
        self.assertIn("positive-ingredient-review", {item["id"] for item in oat["reviewQueues"]})

    def test_quality_freshness_warnings_route_exact_gtin_market(self):
        quality = {
            "reportSha256": "a" * 64,
            "metrics": {"products": 2},
            "warnings": [
                {"code": "formulation-stale", "gtin": "00200000000004", "market": "DE"},
                {"code": "formulation-refresh-recommended", "gtin": "00200000000028", "market": "DE"},
            ],
        }
        report = MODULE.analyze_envelope(envelope=EVIDENCE, methodology=METHODOLOGY, quality_report=quality)
        by_gtin = {item["gtin"]: item for item in report["products"]}
        self.assertEqual(by_gtin["00200000000004"]["freshnessState"], "stale")
        self.assertEqual(by_gtin["00200000000004"]["parserStatus"], "unknown")
        self.assertIn("formulation-stale", by_gtin["00200000000004"]["safetyFlags"])
        self.assertEqual(by_gtin["00200000000028"]["freshnessState"], "refresh-recommended")
        self.assertEqual(report["qualityReportSha256"], "a" * 64)

    def test_quality_selection_count_mismatch_fails_closed(self):
        with self.assertRaises(MODULE.MethodologyError):
            MODULE.analyze_envelope(
                envelope=EVIDENCE,
                methodology=METHODOLOGY,
                quality_report={"metrics": {"products": 3}, "warnings": []},
            )


if __name__ == "__main__":
    unittest.main()
