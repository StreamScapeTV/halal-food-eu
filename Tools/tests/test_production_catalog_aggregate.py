from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "production_catalog_aggregate.py"
SPEC = importlib.util.spec_from_file_location("production_catalog_aggregate", MODULE_PATH)
assert SPEC and SPEC.loader
aggregate = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = aggregate
SPEC.loader.exec_module(aggregate)


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class FakeEvidenceModel:
    @staticmethod
    def validate_envelope(value: dict[str, object]) -> dict[str, object]:
        if value.get("schemaVersion") != 1:
            raise ValueError("invalid")
        return value

    @staticmethod
    def derive_id(collection: str, record: dict[str, object]) -> str:
        value = copy.deepcopy(record)
        value.pop("id", None)
        digest = hashlib.sha256(canonical(value).encode()).hexdigest()
        return f"hfeu:selection:sha256:{digest}"


class ProductionCatalogAggregateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.old_model = aggregate._evidence_model
        aggregate._evidence_model = lambda: FakeEvidenceModel

    def tearDown(self) -> None:
        aggregate._evidence_model = self.old_model

    def _empty_envelope(self, source_key: str, snapshot: str) -> dict[str, object]:
        return {
            "schemaVersion": 1,
            "sources": [{"sourceKey": source_key, "sourceSnapshotID": snapshot}],
            "assessments": [], "certifications": [], "currentSelections": [],
            "identities": [], "ingredients": [], "packageEvidence": [], "releases": [],
            "remoteImages": [], "retailerEvidence": [], "reviews": [], "validityEvents": [],
        }

    def _quality(self, source: str, snapshot: str, *, retailer_count: int = 0) -> dict[str, object]:
        value = {
            "schemaVersion": 1,
            "sourceKey": source,
            "snapshotID": snapshot,
            "policyVersion": "quality-v1",
            "evaluatedAt": "2026-08-31T20:00:00Z",
            "status": "pass",
            "releaseBlockingFindings": [],
            "quarantineRequired": False,
            "rollbackRequired": False,
            "warnings": [],
            "metrics": {"products": 1, "retailerEvidenceByKind": ({"retailer-observation": retailer_count} if retailer_count else {})},
            "sourceRights": {
                "approved": True,
                "fixtureOnly": False,
                "licenseIdentifier": "ODbL" if source == "open-prices" else "ODbL-1.0",
                "attributionPresent": True,
                "termsReview": {"state": "approved", "policyVersion": "source-review-v1", "sourceKey": source},
            },
        }
        value["reportSha256"] = hashlib.sha256(canonical(value).encode()).hexdigest()
        return value

    def test_merge_attaches_only_exact_existing_product_keys_and_rekeys_selection(self) -> None:
        primary = self._empty_envelope("open-food-facts", "off-1")
        primary["currentSelections"] = [{
            "id": "old", "gtin": "00000000000018", "market": "DE", "retailerEvidenceIDs": []
        }]
        retailer = self._empty_envelope("open-prices", "op-1")
        retailer["retailerEvidence"] = [
            {"id": "ret-1", "gtin": "00000000000018", "market": "DE", "kind": "retailer-observation", "sourceKey": "open-prices"},
            {"id": "ret-2", "gtin": "00000000000025", "market": "DE", "kind": "retailer-observation", "sourceKey": "open-prices"},
        ]
        merged, summary = aggregate.merge_evidence(
            primary=primary, retailer=retailer,
            primary_source_key="open-food-facts", primary_snapshot_id="off-1",
            retailer_source_key="open-prices", retailer_snapshot_id="op-1",
        )
        self.assertEqual([item["id"] for item in merged["retailerEvidence"]], ["ret-1"])
        self.assertEqual(merged["currentSelections"][0]["retailerEvidenceIDs"], ["ret-1"])
        self.assertNotEqual(merged["currentSelections"][0]["id"], "old")
        self.assertEqual(summary["matchedRetailerEvidence"], 1)
        self.assertEqual(summary["unmatchedRetailerEvidence"], 1)
        self.assertFalse(summary["retailerEvidenceCanChangeAssessment"])

    def test_retailer_component_cannot_smuggle_assessments_or_selections(self) -> None:
        primary = self._empty_envelope("open-food-facts", "off-1")
        retailer = self._empty_envelope("open-prices", "op-1")
        retailer["assessments"] = [{"id": "forbidden"}]
        with self.assertRaisesRegex(aggregate.AggregateError, "observational-only"):
            aggregate.merge_evidence(
                primary=primary, retailer=retailer,
                primary_source_key="open-food-facts", primary_snapshot_id="off-1",
                retailer_source_key="open-prices", retailer_snapshot_id="op-1",
            )

    def test_quality_merge_binds_both_passing_component_digests_and_rights(self) -> None:
        merged = self._empty_envelope("open-food-facts", "off-1")
        merged["sources"].append({"sourceKey": "open-prices", "sourceSnapshotID": "op-1"})
        merged["currentSelections"] = [{"id": "selection", "gtin": "00000000000018", "market": "DE", "retailerEvidenceIDs": ["ret-1"]}]
        merged["retailerEvidence"] = [{"id": "ret-1", "gtin": "00000000000018", "market": "DE", "kind": "retailer-observation", "sourceKey": "open-prices"}]
        retailer_evidence = self._empty_envelope("open-prices", "op-1")
        retailer_evidence["retailerEvidence"] = copy.deepcopy(merged["retailerEvidence"])
        primary = self._quality("open-food-facts", "off-1")
        retailer = self._quality("open-prices", "op-1", retailer_count=1)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            p_path, r_path = root / "primary.json", root / "retailer.json"
            p_path.write_text(json.dumps(primary) + "\n")
            r_path.write_text(json.dumps(retailer) + "\n")
            base = copy.deepcopy(primary)
            base["metrics"]["retailerEvidenceByKind"] = {"retailer-observation": 1}
            base.pop("reportSha256")
            base["reportSha256"] = hashlib.sha256(canonical(base).encode()).hexdigest()
            report = aggregate.merge_quality(
                base_report=base,
                primary_report=primary, primary_report_path=p_path,
                retailer_report=retailer, retailer_report_path=r_path,
                merged_evidence=merged, retailer_evidence=retailer_evidence,
                quality_policy={"policyVersion": "quality-v1"},
                primary_source_key="open-food-facts", primary_snapshot_id="off-1",
                retailer_source_key="open-prices", retailer_snapshot_id="op-1",
            )
        self.assertEqual([item["sourceKey"] for item in report["componentQualityReports"]], ["open-food-facts", "open-prices"])
        self.assertEqual(report["sourceRights"]["licenseIdentifier"], "multiple-reviewed-sources")
        self.assertEqual(report["aggregation"]["matchedRetailerEvidence"], 1)
        digest = report["reportSha256"]
        subject = copy.deepcopy(report)
        subject.pop("reportSha256")
        self.assertEqual(digest, hashlib.sha256(canonical(subject).encode()).hexdigest())

    def test_blocked_component_quality_fails_closed(self) -> None:
        retailer = self._quality("open-prices", "op-1")
        retailer["status"] = "blocked"
        retailer.pop("reportSha256")
        retailer["reportSha256"] = hashlib.sha256(canonical(retailer).encode()).hexdigest()
        with self.assertRaisesRegex(aggregate.AggregateError, "independently release-passing"):
            aggregate._verify_component_quality(
                retailer,
                source_key="open-prices", snapshot_id="op-1",
                policy_version="quality-v1", label="retailer",
            )


if __name__ == "__main__":
    unittest.main()
