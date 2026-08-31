from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCHEDULED = ROOT / ".github/workflows/scheduled-catalog-refresh.yml"
NORMALIZE = ROOT / ".github/workflows/normalize-and-diff.yml"
QUALITY = ROOT / ".github/workflows/catalog-quality.yml"
BUILD = ROOT / ".github/workflows/build-catalog.yml"
RELEASE = ROOT / ".github/workflows/catalog-release.yml"
RELEASE_INPUT = ROOT / "Tools/production_catalog_release_input.py"


class ProductionRetailerWorkflowTests(unittest.TestCase):
    def test_manual_production_lane_pairs_open_prices_before_build(self) -> None:
        text = SCHEDULED.read_text(encoding="utf-8")
        self.assertIn("PRODUCTION_AGGREGATE_ENABLED=true", text)
        self.assertIn('RETAILER_SNAPSHOT_ID="op-production-${GITHUB_RUN_ID}"', text)
        for job in ("retailer-policy:", "retailer-acquire:", "retailer-normalize:", "retailer-quality:", "aggregate-normalize:", "aggregate-quality:"):
            self.assertIn(job, text)
        build = text.index("  build:")
        self.assertLess(text.index("  aggregate-quality:"), build)
        self.assertIn("needs.aggregate-quality.result == 'success'", text[build:])

    def test_normalize_composition_is_observational_only_and_run_bound(self) -> None:
        text = NORMALIZE.read_text(encoding="utf-8")
        self.assertIn("aggregate_retailer_evidence:", text)
        self.assertIn("Tools/production_catalog_aggregate.py merge-evidence", text)
        self.assertIn("--retailer-source-key open-prices", text)
        self.assertIn("production composition inputs were not produced in this exact trusted run", text)
        self.assertIn("retention-days: 90", text)

    def test_aggregate_quality_re_evaluates_merged_evidence_then_binds_components(self) -> None:
        text = QUALITY.read_text(encoding="utf-8")
        base = text.index('if [[ "$AGGREGATE_RETAILER" == "true" ]]')
        evaluate = text.index("Tools/catalog_quality.py evaluate", base)
        merge = text.index("Tools/production_catalog_aggregate.py merge-quality", base)
        self.assertLess(evaluate, merge)
        self.assertIn('--base-quality "$RUNNER_TEMP/quality/base-quality-report.json"', text)
        self.assertIn("aggregate quality components must come from this exact reviewed run", text)
        self.assertIn("retention-days: 90", text)

    def test_build_and_post_merge_release_bind_both_source_policies(self) -> None:
        build = BUILD.read_text(encoding="utf-8")
        release = RELEASE.read_text(encoding="utf-8")
        for text in (build, release):
            self.assertIn("Data/sources/open-food-facts/source-policy-v1.json", text)
            self.assertIn("Data/sources/open-prices/source-policy-v1.json", text)
        self.assertIn('"repository/Data/sources/open-prices/source-policy-v1.json"', build)
        self.assertIn("retention-days: 90", release)

    def test_post_merge_materialization_request_lists_both_reviewed_sources(self) -> None:
        spec = importlib.util.spec_from_file_location("production_catalog_release_input_retailer_test", RELEASE_INPUT)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(
            module.PRODUCTION_SOURCE_POLICY_PATHS,
            [
                "repository/Data/sources/open-food-facts/source-policy-v1.json",
                "repository/Data/sources/open-prices/source-policy-v1.json",
            ],
        )


if __name__ == "__main__":
    unittest.main()
