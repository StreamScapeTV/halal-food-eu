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
from catalog_workflow_common import ContractError  # noqa: E402
from catalog_workflow_policy import validate_workflows  # noqa: E402

CENTRAL_SHA = "565edf3e966ce5628d30f95b60ce72fc9a92df6d"
CENTRAL_USE = f"StreamScapeTV/ci-workflows/.github/workflows/apple.yml@{CENTRAL_SHA}"


class AppleCIAdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.common = ROOT / "scripts/ci/apple-common.sh"
        self.hosted = ROOT / "scripts/ci/run-apple-hosted-validation.sh"
        self.testflight = ROOT / "scripts/ci/run-apple-testflight.sh"
        self.bump = ROOT / "scripts/ci/advance-mobile-build.sh"
        self.ios_ci = ROOT / "Scripts/ci-ios.sh"
        self.caller = ROOT / ".github/workflows/central-apple.yml"

    def _testflight_env(self, root: Path, **overrides: str) -> dict[str, str]:
        auth_key = root / "AuthKey.p8"
        auth_key.write_text("test", encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "CI_APPLE_TESTFLIGHT_BUILD_NUMBER": "1",
            "CI_APPLE_TESTFLIGHT_AUTH_KEY_PATH": str(auth_key),
            "CI_APPLE_TESTFLIGHT_TEMP_DIR": str(root),
            "CI_APPLE_TESTFLIGHT_TEAM_ID": "ABCDE12345",
            "CI_APPLE_TESTFLIGHT_KEY_ID": "ABCDE12345",
            "CI_APPLE_TESTFLIGHT_ISSUER_ID": "11111111-2222-3333-4444-555555555555",
        }
        env.update(overrides)
        return env

    def test_fixed_wrapper_scripts_are_tracked_source_shapes_and_parse(self) -> None:
        for path in (self.common, self.hosted, self.testflight, self.bump, self.ios_ci):
            with self.subTest(path=path):
                self.assertTrue(path.is_file())
                self.assertFalse(path.is_symlink())
                result = subprocess.run(
                    ["bash", "-n", str(path)],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_hosted_wrapper_fails_closed_on_missing_or_unknown_profile(self) -> None:
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        missing = subprocess.run(
            ["bash", str(self.hosted)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("CI_APPLE_HOSTED_PROFILE is required", missing.stderr)

        env["CI_APPLE_HOSTED_PROFILE"] = "arbitrary-command"
        unknown = subprocess.run(
            ["bash", str(self.hosted)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
        )
        self.assertEqual(unknown.returncode, 2)
        self.assertIn("Unsupported CI_APPLE_HOSTED_PROFILE", unknown.stderr)

    def test_hosted_wrapper_is_bounded_and_reuses_existing_ios_script(self) -> None:
        text = self.hosted.read_text(encoding="utf-8")
        self.assertIn("build|test|simulator", text)
        self.assertIn('export HFEU_IOS_VALIDATION_PROFILE="${PROFILE}"', text)
        self.assertIn('exec bash "${ROOT_DIR}/Scripts/ci-ios.sh"', text)
        self.assertNotIn("eval ", text)
        ios_text = self.ios_ci.read_text(encoding="utf-8")
        self.assertIn('VALIDATION_PROFILE="${HFEU_IOS_VALIDATION_PROFILE:-test}"', ios_text)
        self.assertIn('if [[ "$VALIDATION_PROFILE" == "build" ]]', ios_text)
        self.assertIn('XCODEBUILD_ARGS+=(test)', ios_text)

    def test_testflight_requires_fixed_context_and_rejects_non_numeric_build_number(self) -> None:
        base_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        missing = subprocess.run(
            ["bash", str(self.testflight)], cwd=ROOT, env=base_env, text=True, capture_output=True, check=False
        )
        self.assertEqual(missing.returncode, 2)
        self.assertIn("Missing required fixed Central TestFlight context", missing.stderr)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = self._testflight_env(root, CI_APPLE_TESTFLIGHT_BUILD_NUMBER="Build.253")
            invalid = subprocess.run(
                ["bash", str(self.testflight)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("positive numeric CFBundleVersion", invalid.stderr)

    def test_tag_release_identity_mismatch_fails_before_catalog_or_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bad_version = subprocess.run(
                ["bash", str(self.testflight)],
                cwd=ROOT,
                env=self._testflight_env(
                    root,
                    CI_APPLE_TESTFLIGHT_SOURCE_IS_TAG="true",
                    CI_APPLE_TESTFLIGHT_RELEASE_VERSION="9.9.9",
                ),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(bad_version.returncode, 2)
            self.assertIn("does not match project.yml MARKETING_VERSION", bad_version.stderr)
            self.assertNotIn("production catalog release receipt", bad_version.stderr)

            bad_build = subprocess.run(
                ["bash", str(self.testflight)],
                cwd=ROOT,
                env=self._testflight_env(
                    root,
                    CI_APPLE_TESTFLIGHT_SOURCE_IS_TAG="true",
                    CI_APPLE_TESTFLIGHT_RELEASE_VERSION="0.1.0",
                    CI_APPLE_TESTFLIGHT_BUILD_NUMBER="2",
                ),
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(bad_build.returncode, 2)
            self.assertIn("does not match project.yml CURRENT_PROJECT_VERSION", bad_build.stderr)
            self.assertNotIn("production catalog release receipt", bad_build.stderr)

    def test_testflight_refuses_synthetic_catalog_before_network_or_xcode(self) -> None:
        self.assertFalse((ROOT / "Data/catalog/production-catalog-release-input-v1.json").exists())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for extra in (
                {},
                {
                    "CI_APPLE_TESTFLIGHT_SOURCE_IS_TAG": "true",
                    "CI_APPLE_TESTFLIGHT_RELEASE_VERSION": "0.1.0",
                },
            ):
                with self.subTest(extra=extra):
                    result = subprocess.run(
                        ["bash", str(self.testflight)],
                        cwd=ROOT,
                        env=self._testflight_env(root, **extra),
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("refusing to package a synthetic catalog", result.stderr)

    def test_testflight_static_contract_preserves_exact_catalog_and_release_identity(self) -> None:
        text = self.testfl²È="24 marketing_version="1.0.0",
                build_number="257",
                token="token",
                getter=getter,
            )
        )
        self.assertEqual(len(calls), 2)

    def test_app_store_connect_lookup_reports_absent_build_without_guessing(self) -> None:
        def getter(path: str, *, token: str):
            parsed = urllib.parse.urlparse(path)
            if parsed.path == "/v1/apps":
                return {
                    "data": [
                        {
                            "type": "apps",
                            "id": "app-1",
                            "attributes": {"bundleId": "tv.streamscape.halalfoodeu"},
                        }
                    ]
                }
            return {"data": [], "included": []}

        self.assertFalse(
            app_store_connect.build_exists(
                bundle_id="tv.streamscape.halalfoodeu",
                marketing_version="1.0.0",
                build_number="257",
                token="token",
                getter=getter,
            )
        )

    def test_es256_der_conversion_is_fixed_width(self) -> None:
        raw = app_store_connect.der_es256_to_raw(bytes.fromhex("3006020101020102"))
        self.assertEqual(len(raw), 64)
        self.assertEqual(raw[:32], b"\0" * 31 + b"\x01")
        self.assertEqual(raw[32:], b"\0" * 31 + b"\x02")

    def test_app_store_connect_token_signing_uses_es256_fixed_width_signature(self) -> None:
        if not shutil.which("openssl"):
            self.skipTest("OpenSSL is unavailable")
        with tempfile.TemporaryDirectory() as temporary:
            key = Path(temporary) / "AuthKey_TEST1.p8"
            generated = subprocess.run(
                ["openssl", "ecparam", "-name", "prime256v1", "-genkey", "-noout", "-out", str(key)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(generated.returncode, 0, generated.stderr)
            token = app_store_connect.make_token(
                issuer_id="11111111-2222-3333-4444-555555555555",
                key_id="ABCDE12345",
                key_path=key,
                now=1_800_000_000,
            )
            segments = token.split(".")
            self.assertEqual(len(segments), 3)
            signature = segments[2] + "=" * (-len(segments[2]) % 4)
            self.assertEqual(len(base64.urlsafe_b64decode(signature)), 64)

    def test_mobile_build_bump_changes_only_current_project_version_and_is_idempotent(self) -> None:
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
            first = subprocess.run(
                ["bash", str(self.bump)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            updated = target.read_text(encoding="utf-8")
            self.assertIn("CURRENT_PROJECT_VERSION: 2", updated)
            self.assertIn("MARKETING_VERSION: 0.1.0", updated)
            self.assertEqual(
                source.replace("CURRENT_PROJECT_VERSION: 1", "CURRENT_PROJECT_VERSION: 2"),
                updated,
            )

            second = subprocess.run(
                ["bash", str(self.bump)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(target.read_text(encoding="utf-8"), updated)

    def test_mobile_build_bump_rejects_wrong_version_or_unexpected_build(self) -> None:
        cases = (
            ("9.9.9", "1", "MARKETING_VERSION"),
            ("0.1.0", "3", "neither the accepted build"),
        )
        for release_version, release_build, expected in cases:
            with self.subTest(release_version=release_version, release_build=release_build):
                with tempfile.TemporaryDirectory() as temporary:
                    worktree = Path(temporary)
                    (worktree / "project.yml").write_text(
                        (ROOT / "project.yml").read_text(encoding="utf-8"), encoding="utf-8"
                    )
                    result = subprocess.run(
                        ["bash", str(self.bump)],
                        cwd=ROOT,
                        env={
                            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                            "CI_MOBILE_RELEASE_VERSION": release_version,
                            "CI_MOBILE_RELEASE_BUILD_NUMBER": release_build,
                            "CI_MOBILE_BUMP_WORKTREE": str(worktree),
                        },
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(expected, result.stderr)

    def test_mobile_build_bump_has_no_git_transport_surface(self) -> None:
        text = self.bump.read_text(encoding="utf-8")
        self.assertIn("CI_MOBILE_BUMP_WORKTREE", text)
        self.assertIn("CURRENT_PROJECT_VERSION", text)
        self.assertIn("MARKETING_VERSION", text)
        self.assertNotRegex(text, r"(?m)^\s*git\s")
        self.assertIn("-name .git", text)
        self.assertNotIn("GITHUB_TOKEN", text)
        self.assertNotIn("GH_TOKEN", text)

    def test_product_caller_is_manual_pinned_and_keeps_normal_ci_independent(self) -> None:
        text = self.caller.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", text)
        self.assertNotIn("pull_request:", text)
        self.assertNotIn("push:", text)
        self.assertEqual(text.count(CENTRAL_USE), 2)
        self.assertNotIn("ci-workflows/.github/workflows/apple.yml@main", text)
        self.assertIn("github.ref == 'refs/heads/main'", text)
        self.assertIn("repository: StreamScapeTV/halal-food-eu", text)
        self.assertIn("build_number: ${{ inputs.build_number }}", text)

    def test_workflow_policy_accepts_pinned_reusable_and_rejects_mutable_ref(self) -> None:
        def workflow(target: str) -> str:
            return (
                "name: central fixture\n"
                '"on":\n'
                "  workflow_dispatch:\n"
                "permissions:\n"
                "  contents: read\n"
                "jobs:\n"
                "  central:\n"
                f"    uses: {target}\n"
            )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "central.yml"
            path.write_text(workflow(CENTRAL_USE), encoding="utf-8")
            self.assertEqual(validate_workflows(root), ["central.yml"])

            path.write_text(
                workflow("StreamScapeTV/ci-workflows/.github/workflows/apple.yml@main"), encoding="utf-8"
            )
            with self.assertRaisesRegex(ContractError, "unpinned"):
                validate_workflows(root)


if __name__ == "__main__":
    unittest.main()
