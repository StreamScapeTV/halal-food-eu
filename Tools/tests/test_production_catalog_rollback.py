from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).with_name("test_production_catalog.py")
SPEC = importlib.util.spec_from_file_location("rollback_fixture_production_catalog", MODULE_PATH)
assert SPEC and SPEC.loader
fixture_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fixture_module
SPEC.loader.exec_module(fixture_module)


class ProductionCatalogRollbackTests(unittest.TestCase):
    def test_previous_accepted_catalog_can_be_rebuilt_as_logical_rollback(self) -> None:
        fixture = fixture_module.ProductionCatalogTests(
            methodName="test_builds_canonical_evidence_and_persists_review_quality_state"
        )
        fixture.setUp()
        try:
            accepted_database = fixture.root / "accepted.sqlite3"
            accepted_manifest = fixture.root / "accepted-manifest.json"
            accepted_logical = fixture.root / "accepted-logical.json"
            fixture.build(
                database_path=accepted_database,
                manifest_path=accepted_manifest,
                logical_dump_path=accepted_logical,
                release_notes_path=fixture.root / "accepted-release.md",
                catalog_version="1.4.0",
            )
            fixture_module.production_catalog.validate_catalog(
                accepted_database,
                accepted_manifest,
            )

            forward_database = fixture.root / "forward.sqlite3"
            forward_manifest = fixture.root / "forward-manifest.json"
            forward_notes = fixture.root / "forward-release.md"
            forward = fixture.build(
                database_path=forward_database,
                manifest_path=forward_manifest,
                logical_dump_path=fixture.root / "forward-logical.json",
                release_notes_path=forward_notes,
                previous_manifest_path=accepted_manifest,
                catalog_version="1.5.0",
            )
            fixture_module.production_catalog.validate_catalog(
                forward_database,
                forward_manifest,
            )
            self.assertEqual(forward["catalogVersion"], "1.5.0")
            self.assertIn("(+0 vs previous accepted manifest)", forward_notes.read_text(encoding="utf-8"))

            rollback_database = fixture.root / "rollback.sqlite3"
            rollback_manifest = fixture.root / "rollback-manifest.json"
            rollback_logical = fixture.root / "rollback-logical.json"
            rollback_notes = fixture.root / "rollback-release.md"
            rollback = fixture.build(
                database_path=rollback_database,
                manifest_path=rollback_manifest,
                logical_dump_path=rollback_logical,
                release_notes_path=rollback_notes,
                previous_manifest_path=forward_manifest,
                catalog_version="1.4.0",
            )
            fixture_module.production_catalog.validate_catalog(
                rollback_database,
                rollback_manifest,
            )

            self.assertEqual(rollback["catalogVersion"], "1.4.0")
            self.assertEqual(rollback["recordCount"], forward["recordCount"])
            self.assertEqual(
                json.loads(accepted_logical.read_text(encoding="utf-8")),
                json.loads(rollback_logical.read_text(encoding="utf-8")),
            )
            self.assertIn("(+0 vs previous accepted manifest)", rollback_notes.read_text(encoding="utf-8"))
        finally:
            fixture.tearDown()


if __name__ == "__main__":
    unittest.main()
