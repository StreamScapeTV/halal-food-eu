from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
IOS_SCRIPT = ROOT / "Scripts/ci-ios.sh"


class ProductionCIOSShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.script = IOS_SCRIPT.read_text(encoding="utf-8")

    def test_xcodebuild_arguments_are_nonempty_before_strict_array_expansion(self) -> None:
        self.assertIn("XCODEBUILD_ARGS=(", self.script)
        self.assertIn("-project HalalFoodEU.xcodeproj", self.script)
        self.assertIn(
            "XCODEBUILD_ARGS+=(-only-testing:HalalFoodEUTests/ProductionCatalogArtifactCompatibilityTests)",
            self.script,
        )
        self.assertIn("XCODEBUILD_ARGS+=(test)", self.script)
        self.assertIn('xcodebuild "${XCODEBUILD_ARGS[@]}"', self.script)
        self.assertNotIn("TEST_FILTER=()", self.script)
        self.assertNotIn('"${TEST_FILTER[@]}"', self.script)


if __name__ == "__main__":
    unittest.main()
