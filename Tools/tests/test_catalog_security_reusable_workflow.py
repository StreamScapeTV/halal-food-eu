from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Tools"))

import catalog_security  # noqa: E402

CENTRAL_SHA = "565edf3e966ce5628d30f95b60ce72fc9a92df6d"
CENTRAL_REPOSITORY = "StreamScapeTV/ci-workflows"
CENTRAL_TARGET = f"{CENTRAL_REPOSITORY}/.github/workflows/apple.yml@{CENTRAL_SHA}"
XCODEGEN_SHA = "8445e778451c7e44237b90281bde622d764b0084"


class ReusableWorkflowDependencyTests(unittest.TestCase):
    def _fixture(self, target: str) -> tuple[Path, Path, tempfile.TemporaryDirectory[str]]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        workflows = root / ".github/workflows"
        workflows.mkdir(parents=True)
        (workflows / "central.yml").write_text(
            "name: central\n"
            '"on": workflow_dispatch\n'
            "permissions:\n"
            "  contents: read\n"
            "jobs:\n"
            "  validation:\n"
            f"    uses: {target}\n",
            encoding="utf-8",
        )
        manifest = root / "tooling-dependencies-v1.json"
        manifest.write_text(
            json.dumps(
                {
                    "githubActions": {
                        CENTRAL_REPOSITORY: {
                            "commitSha": CENTRAL_SHA,
                            "version": "apple.yml",
                        }
                    },
                    "pythonRuntimeDependencies": [],
                    "reviewedAt": "2026-09-05",
                    "schemaVersion": 1,
                    "xcodegen": {
                        "commitSha": XCODEGEN_SHA,
                        "repository": "yonaskolb/XcodeGen",
                        "version": "2.46.0",
                    },
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return root, manifest, temporary

    def test_sha_pinned_reusable_workflow_is_reviewed_by_repository(self) -> None:
        root, manifest, temporary = self._fixture(CENTRAL_TARGET)
        try:
            sbom = catalog_security.tooling_sbom(root, manifest)
        finally:
            temporary.cleanup()
        self.assertEqual(
            sbom["githubActions"],
            [
                {
                    "repository": CENTRAL_REPOSITORY,
                    "version": "apple.yml",
                    "commitSha": CENTRAL_SHA,
                }
            ],
        )

    def test_mutable_reusable_workflow_ref_remains_rejected(self) -> None:
        root, manifest, temporary = self._fixture(
            f"{CENTRAL_REPOSITORY}/.github/workflows/apple.yml@main"
        )
        try:
            with self.assertRaisesRegex(
                catalog_security.SecurityError,
                "not pinned to a full commit",
            ):
                catalog_security.tooling_sbom(root, manifest)
        finally:
            temporary.cleanup()


if __name__ == "__main__":
    unittest.main()
