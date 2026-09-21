#!/usr/bin/env python3
from __future__ import annotations

import json
import os
from pathlib import Path
import stat
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINTS = [
    ROOT / ".ci/build.sh",
    ROOT / ".ci/test.sh",
    ROOT / ".ci/test-full.sh",
    ROOT / ".ci/test-ui.sh",
    ROOT / ".ci/release.sh",
]
VALIDATOR = ROOT / ".ci/validate_inputs.py"


def validator(operation: str, inputs: dict[str, object]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["python3", str(VALIDATOR), operation],
        cwd=ROOT,
        env={**os.environ, "CI_INPUTS_JSON": json.dumps({"schemaVersion": 1, "inputs": inputs})},
        text=True,
        capture_output=True,
        check=False,
    )


class RepositoryCiContractTests(unittest.TestCase):
    def test_entrypoints_are_fixed_regular_executables(self) -> None:
        self.assertEqual(
            [p.relative_to(ROOT).as_posix() for p in ENTRYPOINTS],
            [".ci/build.sh", ".ci/test.sh", ".ci/test-full.sh", ".ci/test-ui.sh", ".ci/release.sh"],
        )
        for path in ENTRYPOINTS:
            self.assertTrue(path.is_file(), path)
            self.assertFalse(path.is_symlink(), path)
            self.assertTrue(path.stat().st_mode & stat.S_IXUSR, path)

    def test_semantic_inputs_are_bounded_and_never_commands(self) -> None:
        self.assertEqual(validator("build", {}).returncode, 0)
        self.assertEqual(validator("build", {"product_target": "ios"}).returncode, 0)
        self.assertNotEqual(validator("build", {"command": "xcodebuild clean"}).returncode, 0)
        self.assertNotEqual(validator("build", {"product_target": "other-product"}).returncode, 0)
        self.assertNotEqual(validator("test", {"test_selectors": ["SomeTests/testOne"]}).returncode, 0)
        self.assertEqual(
            validator("release", {"release_kind": "prepare", "build_identity": "123"}).returncode,
            0,
        )

    def test_unsupported_ui_and_generic_release_fail_closed_before_product_tooling(self) -> None:
        cases = [
            (ROOT / ".ci/test-ui.sh", {}, "ci_operation_unsupported: ui-test"),
            (
                ROOT / ".ci/release.sh",
                {"release_kind": "prepare", "build_identity": "123"},
                "ci_operation_unsupported: generic repository release",
            ),
        ]
        for script, inputs, expected in cases:
            result = subprocess.run(
                [str(script)],
                cwd=ROOT,
                env={
                    **os.environ,
                    "CI_HOST_OS": "macos",
                    "CI_INPUTS_JSON": json.dumps({"schemaVersion": 1, "inputs": inputs}),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 64, result.stderr)
            self.assertIn(expected, result.stderr)

    def test_scripts_delegate_to_reviewed_repository_owned_surfaces(self) -> None:
        build = (ROOT / ".ci/build.sh").read_text(encoding="utf-8")
        test = (ROOT / ".ci/test.sh").read_text(encoding="utf-8")
        full = (ROOT / ".ci/test-full.sh").read_text(encoding="utf-8")
        self.assertIn("scripts/ci/run-apple-hosted-validation.sh", build)
        self.assertIn("CI_APPLE_HOSTED_PROFILE=build", build)
        self.assertIn("scripts/ci/run-apple-hosted-validation.sh", test)
        self.assertIn("CI_APPLE_HOSTED_PROFILE=test", test)
        self.assertIn("make catalog-validate", full)
        for path in ENTRYPOINTS:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("eval ", text)
            self.assertNotIn("bash -c", text)
            self.assertNotIn("sh -c", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
