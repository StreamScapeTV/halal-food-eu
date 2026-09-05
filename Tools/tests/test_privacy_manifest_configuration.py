from __future__ import annotations

import plistlib
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import privacy_manifest


def _manifest(entries: list[dict[str, object]]) -> dict[str, object]:
    return {
        "NSPrivacyTracking": False,
        "NSPrivacyTrackingDomains": [],
        "NSPrivacyCollectedDataTypes": [],
        "NSPrivacyAccessedAPITypes": entries,
    }


def _user_defaults_entry(reason: str = "CA92.1") -> dict[str, object]:
    return {
        "NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategoryUserDefaults",
        "NSPrivacyAccessedAPITypeReasons": [reason],
    }


def _write_fixture(
    root: Path,
    *,
    source: str = "let defaults = UserDefaults.standard\n",
    entries: list[dict[str, object]] | None = None,
    include_source_root: bool = True,
) -> None:
    resources = root / "HalalFoodEU/Resources"
    app = root / "HalalFoodEU/App"
    resources.mkdir(parents=True)
    app.mkdir(parents=True)
    (app / "Fixture.swift").write_text(source, encoding="utf-8")
    with (resources / "PrivacyInfo.xcprivacy").open("wb") as handle:
        plistlib.dump(
            _manifest(entries if entries is not None else [_user_defaults_entry()]),
            handle,
            sort_keys=False,
        )
    (root / "project.yml").write_text(
        "targets:\n"
        "  HalalFoodEU:\n"
        "    sources:\n"
        + ("      - path: HalalFoodEU\n" if include_source_root else "      - path: Data\n"),
        encoding="utf-8",
    )


class PrivacyManifestConfigurationTests(unittest.TestCase):
    def test_committed_privacy_manifest_matches_shipping_required_reason_api_use(self) -> None:
        report = privacy_manifest.validate(ROOT)
        self.assertFalse(report["tracking"])
        self.assertEqual(report["collectedDataTypes"], 0)
        self.assertEqual(
            report["requiredReasonAPIs"][0]["category"],
            "NSPrivacyAccessedAPICategoryUserDefaults",
        )
        self.assertEqual(report["requiredReasonAPIs"][0]["reasons"], ["CA92.1"])
        self.assertIn(
            "HalalFoodEU/App/AppPreferences.swift",
            report["requiredReasonAPIs"][0]["sourceFiles"],
        )

    def test_missing_user_defaults_declaration_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root, entries=[])
            with self.assertRaisesRegex(
                privacy_manifest.PrivacyManifestError,
                "missing required-reason categories",
            ):
                privacy_manifest.validate(root)

    def test_unapproved_user_defaults_reason_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root, entries=[_user_defaults_entry("1C8F.1")])
            with self.assertRaisesRegex(
                privacy_manifest.PrivacyManifestError,
                "exact reviewed reason set",
            ):
                privacy_manifest.validate(root)

    def test_new_unreviewed_required_reason_category_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(
                root,
                source=(
                    "let defaults = UserDefaults.standard\n"
                    "let uptime = ProcessInfo.processInfo.systemUptime\n"
                ),
            )
            with self.assertRaisesRegex(
                privacy_manifest.PrivacyManifestError,
                "without a reviewed mapping.*SystemBootTime",
            ):
                privacy_manifest.validate(root)

    def test_unused_required_reason_declaration_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            entries = [
                _user_defaults_entry(),
                {
                    "NSPrivacyAccessedAPIType": "NSPrivacyAccessedAPICategorySystemBootTime",
                    "NSPrivacyAccessedAPITypeReasons": ["35F9.1"],
                },
            ]
            _write_fixture(root, entries=entries)
            with self.assertRaisesRegex(
                privacy_manifest.PrivacyManifestError,
                "not used by shipping source",
            ):
                privacy_manifest.validate(root)

    def test_project_must_package_shipping_source_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root, include_source_root=False)
            with self.assertRaisesRegex(
                privacy_manifest.PrivacyManifestError,
                "must include HalalFoodEU",
            ):
                privacy_manifest.validate(root)


if __name__ == "__main__":
    unittest.main()
