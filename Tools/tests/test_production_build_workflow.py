from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/build-catalog.yml"


class ProductionBuildWorkflowTests(unittest.TestCase):
    def test_reusable_build_requires_all_three_digest_bound_inputs(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("basic_exclusions_artifact_name:", text)
        self.assertIn("catalog_version:", text)
        self.assertIn("Download digest-bound basic exclusions", text)
        self.assertIn('for artifact in normalized quality basic-exclusions; do', text)
        self.assertIn('consumer-stage build', text)

    def test_open_food_facts_uses_production_build_request_and_reviewed_budget(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('elif [[ "$SOURCE_KEY" == "open-food-facts" ]]; then', text)
        self.assertIn("Tools/production_catalog_request.py validate", text)
        self.assertIn("Tools/production_catalog_request.py build", text)
        self.assertIn('"maxDatabaseBytes": 262144000', text)
        self.assertIn('"generatedAt": quality["evaluatedAt"]', text)
        self.assertIn('"sourceCommit": os.environ["GITHUB_SHA"]', text)
        self.assertIn('repository/Data/sources/open-food-facts/source-policy-v1.json', text)

    def test_non_admitted_sources_fail_closed_instead_of_using_fixture_builder(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('No production SQLite build is admitted for source $SOURCE_KEY', text)
        production_clause = text.split('elif [[ "$SOURCE_KEY" == "open-food-facts" ]]; then', 1)[1]
        self.assertNotIn("Tools/catalog_builder.py", production_clause)

    def test_production_handoffs_use_runtime_schema_and_manifest_count(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn('manifest["recordCount"]', text)
        self.assertIn("DATABASE_SCHEMA=production-sqlite-v2", text)
        self.assertIn("MANIFEST_SCHEMA=catalog-manifest-v3", text)
        self.assertIn('--record-count "$RECORD_COUNT"', text)


if __name__ == "__main__":
    unittest.main()
