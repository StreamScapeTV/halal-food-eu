from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROPOSAL_WORKFLOW = ROOT / ".github/workflows/propose-catalog-update.yml"
IOS_SCRIPT = ROOT / "Scripts/ci-ios.sh"
COMPATIBILITY_TEST = ROOT / "HalalFoodEUTests/ProductionCatalogArtifactCompatibilityTests.swift"


class ProductionIOSValidationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = PROPOSAL_WORKFLOW.read_text(encoding="utf-8")
        cls.script = IOS_SCRIPT.read_text(encoding="utf-8")
        cls.compatibility = COMPATIBILITY_TEST.read_text(encoding="utf-8")

    def test_production_proposal_runs_exact_ios_gate_before_repository_mutation(self) -> None:
        ios_job = self.workflow.index("  ios-validation:")
        mutation = self.workflow.index("  materialize-production-proposal:")
        self.assertLess(ios_job, mutation)
        self.assertIn("needs: proposal", self.workflow[ios_job:mutation])
        self.assertIn("needs: [proposal, ios-validation]", self.workflow[mutation:])
        self.assertIn("if: inputs.source_key == 'open-food-facts'", self.workflow[ios_job:mutation])

    def test_ios_gate_consumes_exact_build_handoffs_and_prebuilt_payloads(self) -> None:
        self.assertGreaterEqual(self.workflow.count("--consumer-stage ios-validation"), 2)
        self.assertIn("database and manifest must come from the same immutable build", self.workflow)
        self.assertIn("manifest does not bind the exact database handoff digest", self.workflow)
        self.assertIn("sourceCommit differs from exact reviewed workflow code", self.workflow)
        self.assertIn(
            "HFEU_PREBUILT_CATALOG_DATABASE: ${{ runner.temp }}/database/payload/catalog.sqlite3",
            self.workflow,
        )
        self.assertIn(
            "HFEU_PREBUILT_CATALOG_MANIFEST: ${{ runner.temp }}/manifest/payload/catalog-manifest.json",
            self.workflow,
        )
        self.assertIn("run: ./Scripts/ci-ios.sh", self.workflow)

    def test_ios_gate_emits_digest_bound_validation_evidence(self) -> None:
        self.assertIn("--artifact-kind ios-validation-report", self.workflow)
        self.assertIn("'databaseSha256': database['payload']['sha256']", self.workflow)
        self.assertIn("'manifestSha256': manifest_handoff['payload']['sha256']", self.workflow)
        self.assertIn("'status': 'pass'", self.workflow)
        self.assertIn("ios-validation-${{ inputs.snapshot_id }}-${{ github.run_id }}", self.workflow)

    def test_ci_script_requires_paired_prebuilt_catalog_and_selects_generic_suite(self) -> None:
        self.assertIn("HFEU_PREBUILT_CATALOG_DATABASE", self.script)
        self.assertIn("HFEU_PREBUILT_CATALOG_MANIFEST", self.script)
        self.assertIn(
            "Both HFEU_PREBUILT_CATALOG_DATABASE and HFEU_PREBUILT_CATALOG_MANIFEST are required.",
            self.script,
        )
        self.assertIn(
            "-only-testing:HalalFoodEUTests/ProductionCatalogArtifactCompatibilityTests",
            self.script,
        )
        prebuilt_branch = self.script.index('if [[ -n "$PREBUILT_DATABASE" || -n "$PREBUILT_MANIFEST" ]]')
        fixture_branch = self.script.index("python3 Tools/build_production_fixture.py")
        self.assertLess(prebuilt_branch, fixture_branch)

    def test_generic_swift_suite_uses_catalog_content_instead_of_fixture_gtins(self) -> None:
        self.assertIn('@Suite("Production catalog artifact compatibility")', self.compatibility)
        self.assertIn("SELECT gtin FROM products", self.compatibility)
        self.assertIn("current_observation_id IS NULL", self.compatibility)
        self.assertIn("current_assessment_id IS NULL", self.compatibility)
        self.assertNotRegex(self.compatibility, re.compile(r"002000000000(?:04|28)"))
        self.assertIn("product.assessment.status == .unknown", self.compatibility)


if __name__ == "__main__":
    unittest.main()
