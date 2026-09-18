from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
sys.path.insert(0, str(TOOLS))

import app_store_connect  # noqa: E402


class TagDrivenTestFlightContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.testflight = ROOT / "scripts/ci/run-apple-testflight.sh"
        self.bump = ROOT / "scripts/ci/advance-mobile-build.sh"

    def _testflight_env(self, root: Path, **overrides: str) -> dict[str, str]:
        key = root / "AuthKey.p8"
        key.write_text("test", encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CI_APPLE_TESTFLIGHT_BUILD_NUMBER": "1",
            "CI_APPLE_TESTFLIGHT_AUTH_KEY_PATH": str(key),
            "CI_APPLE_TESTFLIGHT_TEMP_DIR": str(root),
            "CI_APPLE_TESTFLIGHT_TEAM_ID": "ABCDE12345",
            "CI_APPLE_TESTFLIGHT_KEY_ID": "ABCDE12345",
            "CI_APPLE_TESTFLIGHT_ISSUER_ID": "11111111-2222-3333-4444-555555555555",
        }
        env.update(overrides)
        return env

    def test_wrappers_parse(self) -> None:
        for path in (self.testflight, self.bump):
            with self.subTest(path=path):
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())
                result = subprocess.run(["bash", "-n", str(path)], cwd=ROOT, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_tag_identity_mismatch_fails_before_catalog_or_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for extra, expected in (
                ({"CI_APPLE_TESTFLIGHT_SOURCE_IS_TAG": "true", "CI_APPLE_TESTFLIGHT_RELEASE_VERSION": "9.9.9"}, "MARKETING_VERSION"),
                ({"CI_APPLE_TESTFLIGHT_SOURCE_IS_TAG": "true", "CI_APPLE_TESTFLIGHT_RELEASE_VERSION": "0.1.0", "CI_APPLE_TESTFLIGHT_BUILD_NUMBER": "2"}, "CURRENT_PROJECT_VERSION"),
            ):
                with self.subTest(extra=extra):
                    result = subprocess.run(
                        ["bash", str(self.testflight)], cwd=ROOT, env=self._testflight_env(root, **extra),
                        text=True, capture_output=True, check=False,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(expected, result.stderr)
                    self.assertNotIn("production catalog release receipt", result.stderr)

    def test_matching_tag_still_fails_closed_on_missing_production_receipt(self) -> None:
        self.assertFalse((ROOT / "Data/catalog/production-catalog-release-input-v1.json").exists())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            result = subprocess.run(
                ["bash", str(self.testflight)], cwd=ROOT,
                env=self._testflight_env(root, CI_APPLE_TESTFLIGHT_SOURCE_IS_TAG="true", CI_APPLE_TESTFLIGHT_RELEASE_VERSION="0.1.0"),
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing to package a synthetic catalog", result.stderr)

    def test_testflight_static_contract_binds_tag_values_and_retry_lookup(self) -> None:
        text = self.testflight.read_text(encoding="utf-8")
        for required in (
            "CI_APPLE_TESTFLIGHT_SOURCE_IS_TAG",
            "CI_APPLE_TESTFLIGHT_RELEASE_VERSION",
            "PROJECT_MARKETING_VERSION",
            "PROJECT_BUILD_NUMBER",
            'Tools/app_store_connect.py" build-exists',
            'if test "${SOURCE_IS_TAG}" != true; then',
            "CFBundleShortVersionString",
            "already contains Halal Food EU",
        ):
            self.assertIn(required, text)
        self.assertIn('ARCHIVE_VERSION_ARGS+=(CURRENT_PROJECT_VERSION="${BUILD_NUMBER}")', text)
        self.assertNotIn("GITHUB_RUN_NUMBER", text)

    def test_app_store_lookup_requires_exact_app_version_and_build(self) -> None:
        calls: list[str] = []

        def getter(path: str, *, token: str):
            self.assertEqual(token, "token")
            calls.append(path)
            parsed = urllib.parse.urlparse(path)
            query = urllib.parse.parse_qs(parsed.query)
            if parsed.path == "/v1/apps":
                self.assertEqual(query["filter[bundleId]"], ["tv.streamscape.halalfoodeu"])
                return {"data": [{"type": "apps", "id": "app-1", "attributes": {"bundleId": "tv.streamscape.halalfoodeu"}}]}
            if parsed.path == "/v1/apps/app-1/buildUploads":
                self.assertEqual(query["filter[cfBundleShortVersionString]"], ["1.0.0"])
                self.assertEqual(query["filter[cfBundleVersion]"], ["257"])
                self.assertEqual(query["filter[platform]"], ["IOS"])
                return {"data": []}
            self.assertEqual(parsed.path, "/v1/builds")
            self.assertEqual(query["filter[app]"], ["app-1"])
            self.assertEqual(query["filter[version]"], ["257"])
            self.assertEqual(query["filter[preReleaseVersion.version]"], ["1.0.0"])
            self.assertEqual(query["filter[preReleaseVersion.platform]"], ["IOS"])
            return {
                "data": [{
                    "type": "builds", "id": "build-1", "attributes": {"version": "257"},
                    "relationships": {"preReleaseVersion": {"data": {"type": "preReleaseVersions", "id": "pre-1"}}},
                }],
                "included": [{"type": "preReleaseVersions", "id": "pre-1", "attributes": {"version": "1.0.0"}}],
            }

        self.assertTrue(app_store_connect.build_exists(
            bundle_id="tv.streamscape.halalfoodeu", marketing_version="1.0.0", build_number="257", token="token", getter=getter,
        ))
        self.assertEqual(len(calls), 3)

    def test_app_store_lookup_reports_absent_without_guessing(self) -> None:
        def getter(path: str, *, token: str):
            parsed = urllib.parse.urlparse(path)
            if parsed.path == "/v1/apps":
                return {"data": [{"type": "apps", "id": "app-1", "attributes": {"bundleId": "tv.streamscape.halalfoodeu"}}]}
            if parsed.path == "/v1/apps/app-1/buildUploads":
                return {"data": []}
            return {"data": [], "included": []}

        self.assertFalse(app_store_connect.build_exists(
            bundle_id="tv.streamscape.halalfoodeu", marketing_version="1.0.0", build_number="257", token="token", getter=getter,
        ))

    def test_processing_build_upload_counts_as_already_accepted_before_build_resource_exists(self) -> None:
        calls: list[str] = []

        def getter(path: str, *, token: str):
            calls.append(path)
            parsed = urllib.parse.urlparse(path)
            if parsed.path == "/v1/apps":
                return {"data": [{"type": "apps", "id": "app-1", "attributes": {"bundleId": "tv.streamscape.halalfoodeu"}}]}
            if parsed.path == "/v1/apps/app-1/buildUploads":
                query = urllib.parse.parse_qs(parsed.query)
                self.assertEqual(query["filter[cfBundleShortVersionString]"], ["1.0.0"])
                self.assertEqual(query["filter[cfBundleVersion]"], ["257"])
                self.assertEqual(query["filter[platform]"], ["IOS"])
                return {
                    "data": [
                        {
                            "type": "buildUploads",
                            "id": "upload-awaiting-old",
                            "attributes": {
                                "cfBundleShortVersionString": "1.0.0",
                                "cfBundleVersion": "257",
                                "platform": "IOS",
                                "state": {"state": "AWAITING_UPLOAD"},
                            },
                        },
                        {
                            "type": "buildUploads",
                            "id": "upload-1",
                            "attributes": {
                                "cfBundleShortVersionString": "1.0.0",
                                "cfBundleVersion": "257",
                                "platform": "IOS",
                                "state": {"state": "PROCESSING"},
                            },
                        },
                    ]
                }
            self.fail(f"processed-build lookup must not run while exact upload is processing: {path}")

        self.assertTrue(app_store_connect.build_exists(
            bundle_id="tv.streamscape.halalfoodeu", marketing_version="1.0.0", build_number="257", token="token", getter=getter,
        ))
        self.assertEqual(len(calls), 2)

    def test_failed_build_upload_can_reuse_build_number_but_awaiting_upload_fails_closed(self) -> None:
        def failed_getter(path: str, *, token: str):
            parsed = urllib.parse.urlparse(path)
            if parsed.path == "/v1/apps":
                return {"data": [{"type": "apps", "id": "app-1", "attributes": {"bundleId": "tv.streamscape.halalfoodeu"}}]}
            if parsed.path == "/v1/apps/app-1/buildUploads":
                return {
                    "data": [{
                        "type": "buildUploads",
                        "id": "upload-failed",
                        "attributes": {
                            "cfBundleShortVersionString": "1.0.0",
                            "cfBundleVersion": "257",
                            "platform": "IOS",
                            "state": {"state": "FAILED"},
                        },
                    }]
                }
            return {"data": [], "included": []}

        self.assertFalse(app_store_connect.build_exists(
            bundle_id="tv.streamscape.halalfoodeu", marketing_version="1.0.0", build_number="257", token="token", getter=failed_getter,
        ))

        def awaiting_getter(path: str, *, token: str):
            parsed = urllib.parse.urlparse(path)
            if parsed.path == "/v1/apps":
                return {"data": [{"type": "apps", "id": "app-1", "attributes": {"bundleId": "tv.streamscape.halalfoodeu"}}]}
            return {
                "data": [{
                    "type": "buildUploads",
                    "id": "upload-awaiting",
                    "attributes": {
                        "cfBundleShortVersionString": "1.0.0",
                        "cfBundleVersion": "257",
                        "platform": "IOS",
                        "state": {"state": "AWAITING_UPLOAD"},
                    },
                }]
            }

        with self.assertRaisesRegex(app_store_connect.AppStoreConnectError, "awaiting upload"):
            app_store_connect.build_exists(
                bundle_id="tv.streamscape.halalfoodeu", marketing_version="1.0.0", build_number="257", token="token", getter=awaiting_getter,
            )

    def test_es256_signing_produces_fixed_width_jws_signature(self) -> None:
        self.assertEqual(
            app_store_connect.der_es256_to_raw(bytes.fromhex("3006020101020102")),
            b"\0" * 31 + b"\x01" + b"\0" * 31 + b"\x02",
        )
        if not shutil.which("openssl"):
            self.skipTest("OpenSSL is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "AuthKey_TEST1.p8"
            generated = subprocess.run(
                ["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key)],
                text=True, capture_output=True, check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            token = app_store_connect.make_token(
                issuer_id="11111111-2222-3333-4444-555555555555", key_id="ABCDE12345", key_path=key, now=1_800_000_000,
            )
            signature = token.split(".")[2]
            signature += "=" * (-len(signature) % 4)
            self.assertEqual(len(base64.urlsafe_b64decode(signature)), 64)

    def test_build_bump_changes_only_build_number_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            worktree = Path(temporary)
            source = (ROOT / "project.yml").read_text(encoding="utf-8")
            target = worktree / "project.yml"
            target.write_text(source, encoding="utf-8")
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "CI_MOBILE_RELEASE_VERSION": "0.1.0",
                "CI_MOBILE_RELEASE_BUILD_NUMBER": "1",
                "CI_MOBILE_BUMP_WORKTREE": str(worktree),
            }
            for _ in range(2):
                result = subprocess.run(["bash", str(self.bump)], cwd=ROOT, env=env, text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
            updated = target.read_text(encoding="utf-8")
            self.assertEqual(source.replace("CURRENT_PROJECT_VERSION: 1", "CURRENT_PROJECT_VERSION: 2"), updated)
            self.assertIn("MARKETING_VERSION: 0.1.0", updated)

    def test_build_bump_rejects_wrong_release_state_and_has_no_git_transport(self) -> None:
        for version, build, expected in (("9.9.9", "1", "MARKETING_VERSION"), ("0.1.0", "3", "neither the accepted build")):
            with self.subTest(version=version, build=build), tempfile.TemporaryDirectory() as temporary:
                worktree = Path(temporary)
                (worktree / "project.yml").write_text((ROOT / "project.yml").read_text(encoding="utf-8"), encoding="utf-8")
                result = subprocess.run(
                    ["bash", str(self.bump)], cwd=ROOT,
                    env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "CI_MOBILE_RELEASE_VERSION": version,
                         "CI_MOBILE_RELEASE_BUILD_NUMBER": build, "CI_MOBILE_BUMP_WORKTREE": str(worktree)},
                    text=True, capture_output=True, check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(expected, result.stderr)
        text = self.bump.read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?m)^\s*git\s")
        self.assertIn("-name .git", text)
        self.assertNotIn("GITHUB_TOKEN", text)
        self.assertNotIn("GH_TOKEN", text)


if __name__ == "__main__":
    unittest.main()
