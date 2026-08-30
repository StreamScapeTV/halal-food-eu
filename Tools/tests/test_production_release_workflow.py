from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/catalog-release.yml"


class ProductionReleaseWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_metadata_receipt_drives_cross_run_input_downloads(self) -> None:
        self.assertIn("Data/catalog/production-catalog-release-input-v1.json", self.text)
        self.assertIn("Tools/production_catalog_release_input.py validate", self.text)
        self.assertIn("--integrated-source-commit \"$GITHUB_SHA\"", self.text)
        self.assertIn("Tools/production_catalog_release_input.py materialize-request", self.text)
        self.assertEqual(self.text.count("run-id: ${{ steps.mode.outputs.source_run_id }}"), 3)
        self.assertEqual(self.text.count("github-token: ${{ github.token }}"), 3)
        self.assertIn("actions: read", self.text)

    def test_production_release_rebuilds_locally_and_validates_exact_sqlite(self) -> None:
        self.assertIn("if: steps.mode.outputs.production == 'true'", self.text)
        self.assertIn("Tools/production_catalog_request.py validate", self.text)
        self.assertIn("Tools/production_catalog_request.py build", self.text)
        self.assertIn("Tools/production_catalog.py validate", self.text)
        self.assertIn("--workflow-contract Data/workflows/catalog-workflow-contract-v1.json", self.text)
        self.assertIn("reviewedSourceCommit", self.text)
        self.assertIn("reviewedSourceRunId", self.text)
        self.assertIn("post-merge manifest {key} differs from reviewed release lineage", self.text)

    def test_production_release_finalizes_review_metadata_and_release_notes(self) -> None:
        production = self.text.index("- name: Materialize production catalog from reviewed immutable inputs")
        fallback = self.text.index("- name: Materialize deterministic synthetic fallback")
        block = self.text[production:fallback]
        self.assertIn("request['releaseNotesOutputPath'] = 'release-notes/payload/catalog-release-notes.md'", block)
        self.assertIn("Tools/production_catalog_release_notes.py", block)
        self.assertIn("Tools/production_catalog_manifest_review.py", block)
        self.assertIn("post-merge manifest is missing releaseReview metadata", block)
        self.assertIn("## HF-PIPELINE-010 release summary", block)
        build = block.index("Tools/production_catalog_request.py build")
        first_validate = block.index("Tools/production_catalog.py validate", build)
        notes = block.index("Tools/production_catalog_release_notes.py", first_validate)
        review = block.index("Tools/production_catalog_manifest_review.py", notes)
        second_validate = block.index("Tools/production_catalog.py validate", review)
        lineage = block.index("post-merge manifest {key} differs from reviewed release lineage", second_validate)
        self.assertLess(build, first_validate)
        self.assertLess(first_validate, notes)
        self.assertLess(notes, review)
        self.assertLess(review, second_validate)
        self.assertLess(second_validate, lineage)

    def test_release_workflow_tracks_finalizers_and_packages_notes_in_evidence(self) -> None:
        self.assertIn('- "Tools/production_catalog_release_notes.py"', self.text)
        self.assertIn('- "Tools/production_catalog_manifest_review.py"', self.text)
        self.assertIn("releaseNotesSha256", self.text)
        self.assertIn("${{ runner.temp }}/release-root/release-notes", self.text)

    def test_legacy_fixture_builder_is_confined_to_explicit_nonproduction_fallback(self) -> None:
        fallback = self.text.index("- name: Materialize deterministic synthetic fallback")
        builder = self.text.index("python3 Tools/catalog_builder.py", fallback)
        report = self.text.index("- name: Emit post-merge checksum and lineage report")
        self.assertLess(fallback, builder)
        self.assertLess(builder, report)
        self.assertIn("if: steps.mode.outputs.production != 'true'", self.text[fallback:builder])

    def test_attestation_reuses_the_subjects_validated_by_evidence_job(self) -> None:
        attestation = self.text.index("  attestation:")
        tail = self.text[attestation:]
        self.assertIn("needs: evidence", tail)
        self.assertIn("release-evidence-${{ github.sha }}", tail)
        self.assertIn("Revalidate exact attestation subjects", tail)
        self.assertIn("release evidence commit differs from attestation commit", tail)
        self.assertIn("release evidence {key} differs from attestation subject", tail)
        self.assertNotIn("Tools/catalog_builder.py", tail)
        self.assertNotIn("Tools/production_catalog_request.py build", tail)

    def test_external_actions_are_full_sha_pinned_and_product_images_are_absent(self) -> None:
        action_refs = re.findall(r"uses:\s+[^@\s]+@([^\s#]+)", self.text)
        self.assertGreaterEqual(len(action_refs), 7)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in action_refs))
        self.assertNotIn("product image", self.text.lower())
        self.assertNotRegex(self.text.lower(), r"\.(?:png|jpe?g|webp)(?:\s|$)")


if __name__ == "__main__":
    unittest.main()
