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
            'Tools/app_store_connect.py" build-state',
            'if test "${SOURCE_IS_TAG}" != true; then',
            "CFBundleShortVersionString",
            "already accepted Halal Food EU",
        ):
            self.assertIn(required, text)
        self.assertIn('ARCHIVE_VERSION_ARGS+=(CURRENT_PROJECT_VERSION="${BUILD_NUMBER}")', text)
        self.assertIn('build-state', text)
        self.assertIn('processing)', text)
        self.assertIn('retryable)', text)
        self.assertIn('POST_UPLOAD_MAX_ATTEMPTS=80', text)
        self.assertIn('source build number remains unchanged', text)
        self.assertNotIn("GITHUB_RUN_NUMBER", text)

    def _app_payload(self):
        return {"data": [{"type": "apps", "id": "app-1", "attributes": {"bundleId": "tv.streamscape.halalfoodeu"}}]}

    def _build(self, state: str):
        return {
            "type": "builds",
            "id": f"build-{state.lower()}",
            "attributes": {"version": "257", "processingState": state},
            "relationships": {"preReleaseVersion": {"data": {"type": "preReleaseVersions", "id": "pre-1"}}},
        }

    def _included(self):
        return [{"type": "preReleaseVersions", "id": "pre-1", "attributes": {"version": "1.0.0"}}]

    def _upload(self, state: str):
        return {
            "type": "buildUploads",
            "id": f"upload-{state.lower()}",
            "attributes": {
                "cfBundleShortVersionString": "1.0.0",
                "cfBundleVersion": "257",
                "platform": "IOS",
                "state": {"state": state},
            },
        }

    def test_app_store_reconciliation_accepts_only_complete_or_valid_identity(self) -> None:
        for upload_state, build_state in (("COMPLETE", None), ("FAILED", "VALID")):
            with self.subTest(upload_state=upload_state, build_state=build_state):
                def getter(path: str, *, token: str):
                    parsed = urllib.parse.urlparse(path)
                    if parsed.path == "/v1/apps":
                        return self._app_payload()
                    if parsed.path == "/v1/apps/app-1/buildUploads":
                        return {"data": [self._upload(upload_state)]}
                    builds = [] if build_state is None else [self._build(build_state)]
                    return {"data": builds, "included": self._included() if builds else []}

                self.assertEqual(
                    app_store_connect.build_reconciliation_state(
                        bundle_id="tv.streamscape.halalfoodeu",
                        marketing_version="1.0.0",
                        build_number="257",
                        token="token",
                        getter=getter,
                    ),
                    "accepted",
                )

    def test_app_store_reconciliation_processing_blocks_duplicate_upload_and_bump(self) -> None:
        for upload_state, build_state in (("PROCESSING", None), ("AWAITING_UPLOAD", None), ("FAILED", "PROCESSING")):
            with self.subTest(upload_state=upload_state, build_state=build_state):
                def getter(path: str, *, token: str):
                    parsed = urllib.parse.urlparse(path)
                    if parsed.path == "/v1/apps":
                        return self._app_payload()
                    if parsed.path == "/v1/apps/app-1/buildUploads":
                        return {"data": [self._upload(upload_state)]}
                    builds = [] if build_state is None else [self._build(build_state)]
                    return {"data": builds, "included": self._included() if builds else []}

                self.assertEqual(
                    app_store_connect.build_reconciliation_state(
                        bundle_id="tv.streamscape.halalfoodeu",
                        marketing_version="1.0.0",
                        build_number="257",
                        token="token",
                        getter=getter,
                    ),
                    "processing",
                )

    def test_app_store_reconciliation_failed_identity_is_retryable_and_absent_is_absent(self) -> None:
        def failed_getter(path: str, *, token: str):
            parsed = urllib.parse.urlparse(path)
            if parsed.path == "/v1/apps":
                return self._app_payload()
            if parsed.path == "/v1/apps/app-1/buildUploads":
                return {"data": [self._upload("FAILED")]}
            return {"data": [self._build("INVALID")], "included": self._included()}

        self.assertEqual(
            app_store_connect.build_reconciliation_state(
                bundle_id="tv.streamscape.halalfoodeu",
                marketing_version="1.0.0",
                build_number="257",
                token="token",
                getter=failed_getter,
            ),
            "retryable",
        )

        def absent_getter(path: str, *, token: str):
            if urllib.parse.urlparse(path).path == "/v1/apps":
                return self._app_payload()
            return {"data": [], "included": []}

        self.assertEqual(
            app_store_connect.build_reconciliation_state(
                bundle_id="tv.streamscape.halalfoodeu",
                marketing_version="1.0.0",
                build_number="257",
                token="token",
                getter=absent_getter,
            ),
            "absent",
        )

    def test_app_store_reconciliation_is_order_independent_and_unknown_state_fails_closed(self) -> None:
        def mixed_getter(path: str, *, token: str):
            parsed = urllib.parse.urlparse(path)
            if parsed.path == "/v1/apps":
                return self._app_payload()
            if parsed.path == "/v1/apps/app-1/buildUploads":
                return {"data": [self._upload("FAILED"), self._upload("PROCESSING")]}
            return {"data": [], "included": []}

        self.assertEqual(
            app_store_connect.build_reconciliation_state(
                bundle_id="tv.streamscape.halalfoodeu",
                marketing_version="1.0.0",
                build_number="257",
                token="token",
                getter=mixed_getter,
            ),
            "processing",
        )

        def unknown_getter(path: str, *, token: str):
            parsed = urllib.parse.urlparse(path)
            if parsed.path == "/v1/apps":
                return self._app_payload()
            if parsed.path == "/v1/apps/app-1/buildUploads":
                return {"data": [self._upload("MYSTERY")]}
            return {"data": [], "included": []}

        with self.assertRaisesRegex(app_store_connect.AppStoreConnectError, "state is missing or unsupported"):
            app_store_connect.build_reconciliation_state(
                bundle_id="tv.streamscape.halalfoodeu",
                marketing_version="1.0.0",
                build_number="257",
                token="token",
                getter=unknown_getter,
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
