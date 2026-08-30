from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import production_catalog


def _projection() -> dict:
    return {
        "schemaVersion": 1,
        "evidenceSchemaVersion": 1,
        "sources": [
            {"sourceKey": "open-food-facts", "operator": "Open Food Facts", "sourceClass": "open-database", "reference": "https://world.openfoodfacts.org/", "retrievedAt": "2026-08-30T04:36:51Z"},
            {"sourceKey": "open-prices", "operator": "Open Food Facts", "sourceClass": "community-observation", "reference": "https://prices.openfoodfacts.org/", "retrievedAt": "2026-08-30T04:57:07Z"},
        ],
        "products": [
            {
                "gtin": "00200000000004", "market": "DE", "selectionID": "sel-1",
                "identity": {"id": "identity-1", "name": "Reviewed Oat Drink", "brand": "Demo", "brandOwner": None, "quantity": "1 L", "sourceKey": "open-food-facts", "sourceRecordID": "off-1", "retrievedAt": "2026-08-30T04:36:51Z"},
                "ingredients": {"id": "ingredient-1", "text": "Water, oats.", "languageCode": "en", "allergensText": "Oats.", "tracesText": None, "observedAt": "2026-08-29T00:00:00Z", "retrievedAt": "2026-08-30T04:36:51Z", "contentHash": "a" * 64, "sourceKey": "open-food-facts", "sourceRecordID": "off-1", "verificationState": "human-verified"},
                "assessment": {"id": "assessment-1", "status": "halal-certified", "methodologyVersion": "1.0.0", "assessedAt": "2026-08-30T07:00:00Z", "recheckAt": "2027-08-01T00:00:00Z", "reasons": [{"code": "CERT-MATCH", "title": "Certificate matches exact product", "detail": "Current scope-matched evidence exists.", "ingredient": None, "severity": "positive", "evidenceIDs": ["cert-1"]}]},
                "certifications": [{"id": "cert-1", "certifier": "Test Certifier", "scheme": "test", "certificateReference": "CERT-1", "scope": "Exact GTIN", "effectiveAt": "2026-08-01T00:00:00Z", "expiryAt": "2027-08-01T00:00:00Z", "lastCheckedAt": "2026-08-30T07:00:00Z", "sourceKey": "open-food-facts"}],
                "retailerEvidence": [{"id": "retailer-1", "kind": "retailer-observation", "retailerKey": "rewe", "observedAt": "2026-08-28T00:00:00Z", "snapshotAt": None, "scope": None, "locationID": "store-1", "limitations": "Dated observation only; not current stock.", "sourceKey": "open-prices"}],
                "remoteImages": [{"id": "image-1", "purpose": "front", "url": "https://images.openfoodfacts.org/product.jpg", "sourceKey": "open-food-facts", "imageID": "front-1", "revision": "1"}],
                "conflictFlags": [],
            },
            {
                "gtin": "00200000000028", "market": "DE", "selectionID": "sel-2",
                "identity": {"id": "identity-2", "name": "Unknown Dessert", "brand": None, "brandOwner": None, "quantity": None, "sourceKey": "open-food-facts", "sourceRecordID": "off-2", "retrievedAt": "2026-08-30T04:36:51Z"},
                "ingredients": None,
                "assessment": {"id": "assessment-2", "status": "unknown", "methodologyVersion": "1.0.0", "assessedAt": "2026-08-30T07:00:00Z", "recheckAt": None, "reasons": [{"code": "INGREDIENTS-MISSING", "title": "Ingredient evidence is missing", "detail": "No current ingredient observation is available.", "ingredient": None, "severity": "caution", "evidenceIDs": []}]},
                "certifications": [], "retailerEvidence": [], "remoteImages": [], "conflictFlags": ["missing-ingredients"],
            },
        ],
    }


class ProductionCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.evidence = self.root / "evidence.json"
        self.database = self.root / "catalog.sqlite3"
        self.manifest = self.root / "catalog-manifest.json"
        projection = _projection()
        self.evidence.write_text(json.dumps({
            "schemaVersion": 1,
            "sources": [
                {"sourceKey": "open-food-facts", "sourceSnapshotID": "off-snapshot"},
                {"sourceKey": "open-prices", "sourceSnapshotID": "op-snapshot"},
            ],
            "projection": projection,
        }), encoding="utf-8")
        self.policies = []
        for source_key in ("open-food-facts", "open-prices"):
            path = self.root / f"{source_key}.json"
            path.write_text(json.dumps({
                "schemaVersion": 1, "sourceKey": source_key,
                "databaseLicense": {"identifier": "ODbL"},
                "attribution": f"Test attribution for {source_key}.",
            }), encoding="utf-8")
            self.policies.append(path)
        self.exclusions = self.root / "exclusions.json"
        self.exclusions.write_text(json.dumps({
            "schemaVersion": 1, "selectionPolicyVersion": "1.0.0",
            "records": [{"gtin": "00200000000011", "market": "DE", "reason": "plain-basic-approved"}],
        }), encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self, **overrides):
        args = dict(
            evidence_path=self.evidence, database_path=self.database, manifest_path=self.manifest,
            policy_paths=self.policies, basic_exclusions_path=self.exclusions,
            catalog_version="1.0.0", selection_policy_version="1.0.0",
            generated_at="2026-08-30T08:00:00Z", source_commit="2e7b7d30a6e777dc89c081e6d2846324fec16118",
            workflow_run="unit-test", logical_dump_path=self.root / "logical.json",
            release_notes_path=self.root / "release.md",
        )
        args.update(overrides)
        return production_catalog.build_catalog(**args)

    def test_build_preserves_missing_ingredients_as_null_unknown(self):
        manifest = self.build()
        production_catalog.validate_catalog(self.database, self.manifest)
        self.assertEqual(manifest["schemaVersion"], 2)
        self.assertEqual(manifest["counts"]["products"], 2)
        self.assertEqual(manifest["counts"]["ingredientObservations"], 1)
        with sqlite3.connect(self.database) as db:
            row = db.execute("SELECT p.current_observation_id,a.status FROM products p JOIN product_assessments a ON a.id=p.current_assessment_id WHERE p.gtin=?", ("00200000000028",)).fetchone()
            self.assertEqual(row, (None, "unknown"))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM basic_exclusions").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM remote_image_references").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM retailer_evidence").fetchone()[0], 1)

    def test_rejects_non_unknown_without_ingredient_evidence(self):
        projection = _projection()
        projection["products"][1]["assessment"]["status"] = "halal-reviewed"
        original = production_catalog.evidence_model.runtime_projection
        production_catalog.evidence_model.runtime_projection = lambda _: projection
        try:
            with self.assertRaisesRegex(ValueError, "lacks ingredient evidence"):
                self.build()
        finally:
            production_catalog.evidence_model.runtime_projection = original

    def test_rejects_basic_exclusion_overlap(self):
        self.exclusions.write_text(json.dumps({
            "schemaVersion": 1, "selectionPolicyVersion": "1.0.0",
            "records": [{"gtin": "00200000000004", "market": "DE", "reason": "bad-overlap"}],
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "overlap detailed products"):
            self.build()

    def test_manifest_digest_tampering_fails(self):
        self.build()
        manifest = json.loads(self.manifest.read_text())
        manifest["sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            production_catalog.validate_catalog(self.database, self.manifest)

    def test_budget_is_enforced(self):
        with self.assertRaisesRegex(ValueError, "exceeds reviewed budget"):
            self.build(max_database_bytes=1)


if __name__ == "__main__":
    unittest.main()
