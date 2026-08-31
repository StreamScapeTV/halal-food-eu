from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

from production_catalog_release_notes import ReleaseNotesError, finalize_release_notes


class ProductionCatalogReleaseNotesTests(unittest.TestCase):
    def manifest(self) -> dict:
        return {
            "catalogVersion": "1.0.0",
            "recordCount": 53774,
            "schemaVersion": 2,
            "methodologyVersion": "unreviewed",
            "rights": {
                "licenses": ["ODbL-1.0"],
                "attributions": ["Open Food Facts contributors"],
            },
        }

    def quality(self, *, comparison: bool = True) -> dict:
        return {
            "status": "pass",
            "changes": {
                "available": comparison,
                "baseline": "accepted-2026-08-01" if comparison else None,
                "additions": 120,
                "formulationChanges": 14,
                "removals": 3,
                "statusChanges": [{"gtin": "00000000000000"}, {"gtin": "00000000000001"}],
                "reviewQueueCount": 9,
            },
            "metrics": {"formulationFreshness": {"stale": 27}},
            "sourceRights": {
                "licenseIdentifier": "ODbL-1.0",
                "attributionPresent": True,
            },
        }

    def test_finalizes_complete_pipeline_010_summary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release-notes.md"
            path.write_text("# Catalog 1.0.0\n\n- Products: 53,774\n", encoding="utf-8")
            first = finalize_release_notes(
                release_notes_path=path,
                manifest=self.manifest(),
                quality=self.quality(),
            )
            self.assertIn("## HF-PIPELINE-010 release summary", first)
            self.assertIn("- Record count: 53,774", first)
            self.assertIn("- Additions: 120", first)
            self.assertIn("- Formulation changes: 14", first)
            self.assertIn("- Status changes: 2", first)
            self.assertIn("- Stale formulation records: 27", first)
            self.assertIn("- SQLite schema version: `2`", first)
            self.assertIn("- Methodology version: `unreviewed`", first)
            self.assertIn("- Current licenses: `ODbL-1.0`", first)
            self.assertIn("- Current attributions: `Open Food Facts contributors`", first)

    def test_truthfully_marks_initial_change_and_rights_comparison_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release-notes.md"
            path.write_text("# Catalog 1.0.0\n", encoding="utf-8")
            text = finalize_release_notes(
                release_notes_path=path,
                manifest=self.manifest(),
                quality=self.quality(comparison=False),
            )
            self.assertIn("- Change comparison available: no", text)
            self.assertIn("- Additions: unavailable (no accepted comparison baseline)", text)
            self.assertIn("previous accepted production source-rights baseline was not supplied", text)

    def test_rejects_nonpassing_quality_and_mismatched_compiler_notes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "release-notes.md"
            path.write_text("# Catalog 0.9.0\n", encoding="utf-8")
            with self.assertRaises(ReleaseNotesError):
                finalize_release_notes(
                    release_notes_path=path,
                    manifest=self.manifest(),
                    quality=self.quality(),
                )
            path.write_text("# Catalog 1.0.0\n", encoding="utf-8")
            quality = self.quality()
            quality["status"] = "blocked"
            with self.assertRaises(ReleaseNotesError):
                finalize_release_notes(
                    release_notes_path=path,
                    manifest=self.manifest(),
                    quality=quality,
                )

    def test_build_workflow_materializes_and_binds_formal_notes(self) -> None:
        workflow = (ROOT / ".github/workflows/build-catalog.yml").read_text(encoding="utf-8")
        self.assertIn('"releaseNotesOutputPath": "release-notes/payload/catalog-release-notes.md"', workflow)
        self.assertIn("Tools/production_catalog_release_notes.py", workflow)
        self.assertIn("--artifact-kind catalog-release-notes", workflow)
        self.assertIn("release_notes_artifact_name", workflow)

    def test_workflow_contract_declares_release_notes_as_build_output(self) -> None:
        contract = json.loads((ROOT / "Data/workflows/catalog-workflow-contract-v1.json").read_text(encoding="utf-8"))
        build = next(stage for stage in contract["stages"] if stage["key"] == "build")
        self.assertIn("catalog-release-notes", build["produces"])
        self.assertEqual(contract["artifactKinds"]["catalog-release-notes"]["maxRecords"], 1)


if __name__ == "__main__":
    unittest.main()
