from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SWIFT_TEST = ROOT / "HalalFoodEUTests/ProductionCatalogArtifactCompatibilityTests.swift"


class ProductionIOSArtifactCompatibilitySourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SWIFT_TEST.read_text(encoding="utf-8")

    def test_throwing_operations_are_evaluated_before_testing_require_macros(self) -> None:
        self.assertIn(
            "let candidateGTIN = try firstGTIN(databaseURL: fixture.database)",
            self.source,
        )
        self.assertIn("let gtin = try #require(\n            candidateGTIN,", self.source)
        self.assertIn("let candidateProduct = try await catalog.product", self.source)
        self.assertNotIn("#require(\n            firstGTIN(", self.source)
        self.assertNotIn("#require(try await catalog.product", self.source)
        self.assertNotIn("#require(\n            try JSONSerialization.jsonObject", self.source)

    def test_artifact_test_owns_its_bundle_token(self) -> None:
        self.assertIn(
            "private final class ProductionCatalogArtifactCompatibilityBundleToken {}",
            self.source,
        )
        self.assertIn(
            "Bundle(for: ProductionCatalogArtifactCompatibilityBundleToken.self)",
            self.source,
        )
        self.assertNotIn("Bundle(for: TestBundleToken.self)", self.source)


if __name__ == "__main__":
    unittest.main()
