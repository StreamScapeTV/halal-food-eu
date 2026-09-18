from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
sys.path.insert(0, str(TOOLS))

from catalog_workflow_common import ContractError  # noqa: E402
from catalog_workflow_policy import validate_workflows  # noqa: E402

CENTRAL_SHA = "565edf3e966ce5628d30f95b60ce72fc9a92df6d"
CENTRAL_USE = f"StreamScapeTV/ci-workflows/.github/workflows/apple.yml@{CENTRAL_SHA}"


class AppleCIAdoptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.common = ROOT / "scripts/ci/apple-common.sh"
        self.hosted = ROOT / "scripts/ci/run-apple-hosted-validation.sh"
        self.testflight = ROOT / "scripts/ci/run-apple-testflight.sh"
        self.ios_ci = ROOT / "Scripts/ci-ios.sh"
        self.caller = ROOT / ".github/workflows/central-apple.yml"

    def test_fixed_wrapper_scripts_are_tracked_source_shapes_and_parse(self) -> None:
        for path in (self.common, self.hosted, self.testflight, self.ios_ci):
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
            auth_key = root / "AuthKey.p8"
            auth_key.write_text("test", encoding="utf-8")
            env = dict(base_env)
            env.update(
                {
                    "CI_APPLE_TESTFLIGHT_BUILD_NUMBER": "Build.253",
                    "CI_APPLE_TESTFLIGHT_AUTH_KEY_PATH": str(auth_key),
                    "CI_APPLE_TESTFLIGHT_TEMP_DIR": str(root),
                    "CI_APPLE_TESTFLIGHT_TEAM_ID": "ABCDE12345",
                    "CI_APPLE_TESTFLIGHT_KEY_ID": "ABCDE12345",
                    "CI_APPLE_TESTFLIGHT_ISSUER_ID": "11111111-2222-3333-4444-555555555555",
                }
            )
            invalid = subprocess.run(
                ["bash", str(self.testflight)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertIn("positive numeric CFBundleVersion", invalid.stderr)

    def test_testflight_refuses_synthetic_catalog_before_network_or_xcode(self) -> None:
        self.assertFalse((ROOT / "Data/catalog/production-catalog-release-input-v1.json").exists())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            auth_key = root / "AuthKey.p8"
            auth_key.write_text("test", encoding="utf-8")
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "CI_APPLE_TESTFLIGHT_BUILD_NUMBER": "253",
                "CI_APPLE_TESTFLIGHT_AUTH_KEY_PATH": str(auth_key),
                "CI_APPLE_TESTFLIGHT_TEMP_DIR": str(root),
                "CI_APPLE_TESTFLIGHT_TEAM_ID": "ABCDE12345",
                "CI_APPLE_TESTFLIGHT_KEY_ID": "ABCDE12345",
                "CI_APPLE_TESTFLIGHT_ISSUER_ID": "11111111-2222-3333-4444-555555555555",
            }
            result = subprocess.run(
                ["bash", str(self.testflight)], cwd=ROOT, env=env, text=True, capture_output=True, check=False
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("refusing to package a synthetic catalog", result.stderr)

    def test_testflight_static_contract_preserves_exact_catalog_and_build_identity(self) -> None:
        text = self.testflight.read_text(encoding="utf-8")
        for required in (
            "Data/catalog/production-catalog-release-input-v1.json",
            'report.get("releaseMode") != "production"',
            'ARTIFACT_NAME="release-evidence-${SOURCE_SHA}"',
            'CURRENT_PROJECT_VERSION="${BUILD_NUMBER}"',
            "manageAppVersionAndBuildNumber",
            'hfeu_sha256 "${ARCHIVED_APP}/catalog.sqlite3"',
            'hfeu_sha256 "${EXPORTED_APP}/catalog.sqlite3"',
            '--file "${IPA_PATH}"',
            '--p8-file-path "${AUTH_KEY_PATH}"',
        ):
            self.assertIn(required, text)
        self.assertNotIn("GITHUB_RUN_NUMBER", text)
        self.assertNotIn("build_number=", text)

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
