from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import production_catalog_logical


SCHEMA = """
CREATE TABLE catalog_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE sources(id INTEGER PRIMARY KEY, source_key TEXT, operator TEXT, source_class TEXT, reference TEXT, license TEXT, attribution TEXT, retrieved_at TEXT, source_snapshot_id TEXT, policy_schema_version INTEGER, policy_sha256 TEXT);
CREATE TABLE products(gtin TEXT PRIMARY KEY, market TEXT, selection_id TEXT, identity_evidence_id TEXT, name TEXT, brand TEXT, brand_owner TEXT, quantity TEXT, identity_source_id INTEGER, identity_source_record_id TEXT, current_observation_id INTEGER, current_assessment_id INTEGER, conflict_flags_json TEXT);
CREATE TABLE product_observations(id INTEGER PRIMARY KEY, evidence_id TEXT, gtin TEXT, source_id INTEGER, source_record_id TEXT, ingredients_text TEXT, language_code TEXT, allergens_text TEXT, traces_text TEXT, observed_at TEXT, retrieved_at TEXT, ingredients_hash TEXT, verification_state TEXT, freshness_state TEXT);
CREATE TABLE product_assessments(id INTEGER PRIMARY KEY, evidence_id TEXT, gtin TEXT, observation_id INTEGER, status TEXT, summary TEXT, methodology_version TEXT, assessed_at TEXT, reviewed_at TEXT, approved_reviewer_count INTEGER, recheck_at TEXT);
CREATE TABLE assessment_reasons(id INTEGER PRIMARY KEY, assessment_id INTEGER, position INTEGER, code TEXT, title TEXT, detail TEXT, ingredient TEXT, severity TEXT, evidence_ids_json TEXT);
CREATE TABLE certification_evidence(id INTEGER PRIMARY KEY, evidence_id TEXT, assessment_id INTEGER, position INTEGER, source_id INTEGER, certifying_body TEXT, scheme TEXT, certificate_reference TEXT, scope TEXT, valid_from TEXT, valid_until TEXT, last_checked_at TEXT);
CREATE TABLE retailer_evidence(id INTEGER PRIMARY KEY, evidence_id TEXT, gtin TEXT, position INTEGER, source_id INTEGER, kind TEXT, retailer_key TEXT, observed_at TEXT, snapshot_at TEXT, scope TEXT, location_id TEXT, limitations TEXT);
CREATE TABLE remote_image_references(id INTEGER PRIMARY KEY, evidence_id TEXT, gtin TEXT, position INTEGER, source_id INTEGER, purpose TEXT, url TEXT, image_id TEXT, revision TEXT);
CREATE TABLE basic_exclusions(gtin TEXT, market TEXT, selection_policy_version TEXT, reason TEXT);
"""


class LogicalCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "catalog.sqlite3"
        conn = sqlite3.connect(self.database)
        conn.executescript(SCHEMA)
        conn.execute("PRAGMA user_version=2")
        metadata = {
            "schemaVersion": "2",
            "methodologyVersion": "1.0.0",
            "selectionPolicyVersion": "1.0.0",
            "evidenceSchemaVersion": "1",
            "catalogVersion": "1.0.0",
            "generatedAt": "2026-08-30T10:00:00Z",
            "sourceCommit": "a" * 40,
            "workflowRun": "github:1",
        }
        conn.executemany("INSERT INTO catalog_metadata(key,value) VALUES (?,?)", metadata.items())
        conn.execute("INSERT INTO sources VALUES (1,'open-food-facts','OFF','public-dataset','https://example.invalid','ODbL-1.0','Open Food Facts','2026-08-30T10:00:00Z','off-1',1,?)", ("1" * 64,))
        conn.execute("INSERT INTO products VALUES ('00000000000001','DE','sel-1','identity-1','Product','Brand',NULL,'100 g',1,'record-1',NULL,NULL,'[]')")
        conn.execute("INSERT INTO basic_exclusions VALUES ('00000000000002','DE','1.0.0','basic-only')")
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _update_metadata(self, key: str, value: str) -> None:
        conn = sqlite3.connect(self.database)
        conn.execute("UPDATE catalog_metadata SET value=? WHERE key=?", (value, key))
        conn.commit()
        conn.close()

    def test_build_lineage_changes_do_not_change_logical_identity(self) -> None:
        first = production_catalog_logical.compute_identity(self.database)
        for key, value in (
            ("catalogVersion", "1.0.1"),
            ("generatedAt", "2026-08-31T10:00:00Z"),
            ("sourceCommit", "b" * 40),
            ("workflowRun", "github:2"),
        ):
            self._update_metadata(key, value)
        second = production_catalog_logical.compute_identity(self.database)
        self.assertEqual(first, second)

    def test_runtime_semantic_change_changes_logical_identity(self) -> None:
        first = production_catalog_logical.compute_identity(self.database)
        conn = sqlite3.connect(self.database)
        conn.execute("UPDATE products SET name='Changed Product' WHERE gtin='00000000000001'")
        conn.commit()
        conn.close()
        second = production_catalog_logical.compute_identity(self.database)
        self.assertNotEqual(first["sha256"], second["sha256"])

    def test_bind_manifest_enforces_reviewed_expected_identity(self) -> None:
        expected = production_catalog_logical.compute_identity(self.database)
        manifest = self.root / "manifest.json"
        manifest.write_text(
            json.dumps({"sha256": hashlib.sha256(self.database.read_bytes()).hexdigest()}) + "\n",
            encoding="utf-8",
        )
        bound = production_catalog_logical.bind_manifest(
            database_path=self.database,
            manifest_path=manifest,
            expected_sha256=expected["sha256"],
        )
        self.assertEqual(bound, expected)
        self.assertEqual(production_catalog_logical.validate_manifest(database_path=self.database, manifest_path=manifest), expected)
        with self.assertRaisesRegex(production_catalog_logical.LogicalCatalogError, "differs from the reviewed proposal"):
            production_catalog_logical.bind_manifest(
                database_path=self.database,
                manifest_path=manifest,
                expected_sha256="0" * 64,
            )


if __name__ == "__main__":
    unittest.main()
