from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import product_search_index
import production_catalog_logical
import production_catalog_request


class LogicalBuildRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in (
            "normalized/handoff.json",
            "quality/handoff.json",
            "basic-exclusions/handoff.json",
            "quality/policy.json",
            "policies/source.json",
        ):
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("{}\n", encoding="utf-8")
        self.request = {
            "schemaVersion": 1,
            "evidenceHandoffPath": "normalized/handoff.json",
            "qualityHandoffPath": "quality/handoff.json",
            "basicExclusionsHandoffPath": "basic-exclusions/handoff.json",
            "qualityPolicyPath": "quality/policy.json",
            "sourcePolicyPaths": ["policies/source.json"],
            "databaseOutputPath": "output/catalog.sqlite3",
            "manifestOutputPath": "output/manifest.json",
            "catalogVersion": "1.0.0",
            "selectionPolicyVersion": "1.0.0",
            "generatedAt": "2026-08-30T10:00:00Z",
            "sourceCommit": "a" * 40,
            "workflowRun": "github:33300000123:attempt:1",
            "maxDatabaseBytes": 262144000,
            "expectedLogicalCatalogSha256": "5" * 64,
        }
        self.request_path = self.root / "request.json"
        self.request_path.write_text(json.dumps(self.request) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_optional_expected_logical_identity_is_not_treated_as_a_path(self) -> None:
        validated = production_catalog_request.validate_request(self.request)
        self.assertEqual(validated["expectedLogicalCatalogSha256"], "5" * 64)
        bad = dict(self.request, expectedLogicalCatalogSha256="../catalog")
        with self.assertRaisesRegex(production_catalog_request.BuildRequestError, "exact lowercase SHA-256"):
            production_catalog_request.validate_request(bad)

    def test_build_binds_search_index_then_manifest_and_passes_reviewed_expected_identity(self) -> None:
        base_manifest = {
            "catalogVersion": self.request["catalogVersion"],
            "sha256": "1" * 64,
        }
        indexed_manifest = {
            **base_manifest,
            "sha256": "2" * 64,
            "searchIndex": {"schemaVersion": 1},
        }
        fake_catalog = types.SimpleNamespace(build_catalog=lambda **kwargs: base_manifest)
        identity = {"schemaVersion": 1, "sha256": "5" * 64}
        payloads = (
            self.root / "normalized/payload/evidence.json",
            self.root / "quality/payload/quality-report.json",
            self.root / "basic-exclusions/payload/basic-exclusions.json",
        )
        with patch.dict(sys.modules, {"production_catalog": fake_catalog}), patch.object(
            production_catalog_request,
            "validate_build_handoffs",
            return_value=payloads,
        ), patch.object(
            product_search_index,
            "install_search_index",
            return_value=indexed_manifest,
        ) as installer, patch.object(
            product_search_index,
            "validate_search_index",
        ) as search_validator, patch.object(
            production_catalog_logical,
            "bind_manifest",
            return_value=identity,
        ) as binder:
            manifest = production_catalog_request.build_from_request(
                request_path=self.request_path,
                root=self.root,
                workflow_contract_path=self.root / "contract.json",
            )
        installer.assert_called_once_with(
            database_path=self.root / "output/catalog.sqlite3",
            manifest_path=self.root / "output/manifest.json",
            release_notes_path=None,
        )
        search_validator.assert_called_once_with(
            database_path=self.root / "output/catalog.sqlite3",
            manifest_path=self.root / "output/manifest.json",
        )
        binder.assert_called_once_with(
            database_path=self.root / "output/catalog.sqlite3",
            manifest_path=self.root / "output/manifest.json",
            expected_sha256="5" * 64,
        )
        self.assertEqual(manifest["searchIndex"], {"schemaVersion": 1})
        self.assertEqual(manifest["logicalCatalog"], identity)

    def test_unreviewed_build_still_binds_identity_without_expected_digest(self) -> None:
        request = dict(self.request)
        request.pop("expectedLogicalCatalogSha256")
        self.request_path.write_text(json.dumps(request) + "\n", encoding="utf-8")
        base_manifest = {"catalogVersion": "1.0.0", "sha256": "1" * 64}
        indexed_manifest = {
            **base_manifest,
            "sha256": "2" * 64,
            "searchIndex": {"schemaVersion": 1},
        }
        fake_catalog = types.SimpleNamespace(build_catalog=lambda **kwargs: base_manifest)
        payloads = (
            self.root / "normalized/payload/evidence.json",
            self.root / "quality/payload/quality-report.json",
            self.root / "basic-exclusions/payload/basic-exclusions.json",
        )
        with patch.dict(sys.modules, {"production_catalog": fake_catalog}), patch.object(
            production_catalog_request,
            "validate_build_handoffs",
            return_value=payloads,
        ), patch.object(
            product_search_index,
            "install_search_index",
            return_value=indexed_manifest,
        ), patch.object(
            product_search_index,
            "validate_search_index",
        ), patch.object(
            production_catalog_logical,
            "bind_manifest",
            return_value={"schemaVersion": 1, "sha256": "6" * 64},
        ) as binder:
            production_catalog_request.build_from_request(
                request_path=self.request_path,
                root=self.root,
                workflow_contract_path=self.root / "contract.json",
            )
        self.assertIsNone(binder.call_args.kwargs["expected_sha256"])


if __name__ == "__main__":
    unittest.main()
