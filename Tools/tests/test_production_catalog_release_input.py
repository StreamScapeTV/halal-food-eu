from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "production_catalog_release_input.py"
SPEC = importlib.util.spec_from_file_location("production_catalog_release_input", MODULE_PATH)
assert SPEC and SPEC.loader
release_input = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release_input
SPEC.loader.exec_module(release_input)

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "Data/catalog/production-catalog-release-input-v1.schema.json"


class ProductionCatalogReleaseInputTests(unittest.TestCase):
    def _write_payload(self, root: Path, name: str, value: object) -> Path:
        payload = root / "payload" / name
        payload.parent.mkdir(parents=True, exist_ok=True)
        payload.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        return payload

    def _handoff(
        self,
        *,
        root: Path,
        artifact_kind: str,
        workflow: str,
        payload_name: str,
        value: object,
        record_count: int,
        redistribution: str,
        schema: str,
        run_id: str = "33300000123",
        commit: str = "a" * 40,
    ) -> Path:
        payload = self._write_payload(root, payload_name, value)
        handoff = {
            "schemaVersion": 1,
            "artifactKind": artifact_kind,
            "sourceKey": "open-food-facts",
            "snapshotId": "off-2026-08-30",
            "producer": {
                "repository": "StreamScapeTV/halal-food-eu",
                "commitSha": commit,
                "workflow": workflow,
                "runId": run_id,
            },
            "payload": {
                "relativePath": f"payload/{payload_name}",
                "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
                "byteCount": payload.stat().st_size,
            },
            "recordCount": record_count,
            "completeness": "complete",
            "redistributionClass": redistribution,
            "contentSchemaVersion": schema,
            "createdAt": "2026-08-30T15:00:00Z",
        }
        path = root / "handoff.json"
        path.write_text(json.dumps(handoff, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def _fixture(self, temporary: str) -> dict[str, object]:
        root = Path(temporary)
        normalized_root = root / "normalized"
        quality_root = root / "quality"
        exclusions_root = root / "basic-exclusions"
        normalized = self._handoff(
            root=normalized_root,
            artifact_kind="normalized-evidence",
            workflow="normalize-and-diff.yml",
            payload_name="evidence.json",
            value={"schemaVersion": 1, "currentSelections": [{"id": "one"}]},
            record_count=1,
            redistribution="redistributable",
            schema="evidence-envelope-v1",
        )
        quality = self._handoff(
            root=quality_root,
            artifact_kind="quality-report",
            workflow="catalog-quality.yml",
            payload_name="quality-report.json",
            value={"schemaVersion": 1, "status": "pass"},
            record_count=1,
            redistribution="metadata-only",
            schema="catalog-quality-report-v1",
        )
        exclusions = self._handoff(
            root=exclusions_root,
            artifact_kind="basic-exclusions",
            workflow="normalize-and-diff.yml",
            payload_name="basic-exclusions.json",
            value={"schemaVersion": 1, "selectionPolicyVersion": "1.0.0", "records": []},
            record_count=0,
            redistribution="redistributable",
            schema="basic-exclusions-v1",
        )
        proposal = root / "proposal-report.json"
        proposal.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "proposalKey": "catalog-update/open-food-facts-0123456789abcdef",
                    "sourceKey": "open-food-facts",
                    "snapshotId": "off-2026-08-30",
                    "catalogVersion": "1.2.0",
                    "catalogSha256": "1" * 64,
                    "manifestSha256": "2" * 64,
                    "logicalCatalogSha256": "5" * 64,
                    "recordCount": 1,
                    "selectionPolicyVersion": "1.0.0",
                    "qualityReportSha256": "3" * 64,
                    "qualityDecisionSha256": "4" * 64,
                    "qualityEvaluatedAt": "2026-08-30T15:00:00Z",
                    "counts": {},
                    "statusDistribution": {},
                    "rights": {},
                    "materialChangeAutoMergeAllowed": False,
                    "requiresHumanReview": True,
                    "fixtureOnly": False,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return {
            "root": root,
            "proposal": proposal,
            "normalized_root": normalized_root,
            "normalized": normalized,
            "quality_root": quality_root,
            "quality": quality,
            "exclusions_root": exclusions_root,
            "exclusions": exclusions,
        }

    def _prepare(self, fixture: dict[str, object]) -> dict[str, object]:
        return release_input.prepare_release_input(
            source_key="open-food-facts",
            snapshot_id="off-2026-08-30",
            proposal_report_path=fixture["proposal"],
            normalized_handoff_path=fixture["normalized"],
            normalized_root=fixture["normalized_root"],
            normalized_artifact_name="normalized-open-food-facts-off-2026-08-30-33300000123",
            quality_handoff_path=fixture["quality"],
            quality_root=fixture["quality_root"],
            quality_artifact_name="quality-open-food-facts-off-2026-08-30-33300000123",
            basic_exclusions_handoff_path=fixture["exclusions"],
            basic_exclusions_root=fixture["exclusions_root"],
            basic_exclusions_artifact_name="basic-exclusions-open-food-facts-off-2026-08-30-33300000123",
        )

    def test_release_receipt_is_deterministic_and_metadata_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(temporary)
            first = self._prepare(fixture)
            second = self._prepare(fixture)
            self.assertEqual(first, second)
            self.assertEqual(first["sourceRunId"], "33300000123")
            self.assertEqual(first["reviewedSourceCommit"], "a" * 40)
            self.assertEqual(first["catalogVersion"], "1.2.0")
            self.assertEqual(first["logicalCatalogSha256"], "5" * 64)
            serialized = json.dumps(first, sort_keys=True)
            self.assertNotIn("http", serialized.lower())
            self.assertNotIn("token", serialized.lower())
            self.assertNotIn("catalog.sqlite3", serialized)

    def test_mixed_source_runs_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(temporary)
            quality = json.loads(Path(fixture["quality"]).read_text(encoding="utf-8"))
            quality["producer"]["runId"] = "33300000999"
            Path(fixture["quality"]).write_text(json.dumps(quality, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(release_input.ReleaseInputError, "different workflow runs"):
                self._prepare(fixture)

    def test_downloaded_payload_tamper_is_rejected_against_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(temporary)
            receipt = self._prepare(fixture)
            payload = Path(fixture["normalized_root"]) / "payload/evidence.json"
            payload.write_text('{"tampered":true}\n', encoding="utf-8")
            with self.assertRaisesRegex(release_input.ReleaseInputError, "byte count|SHA-256"):
                release_input.verify_downloaded_inputs(
                    receipt=receipt,
                    normalized_handoff_path=fixture["normalized"],
                    normalized_root=fixture["normalized_root"],
                    quality_handoff_path=fixture["quality"],
                    quality_root=fixture["quality_root"],
                    basic_exclusions_handoff_path=fixture["exclusions"],
                    basic_exclusions_root=fixture["exclusions_root"],
                )

    def test_post_merge_build_request_uses_integrated_main_sha_and_reviewed_logical_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            receipt = self._prepare(self._fixture(temporary))
            request = release_input.build_request_from_release_input(
                receipt,
                integrated_source_commit="b" * 40,
                workflow_run="github:33311122233:attempt:1",
            )
            self.assertEqual(request["sourceCommit"], "b" * 40)
            self.assertNotEqual(request["sourceCommit"], receipt["reviewedSourceCommit"])
            self.assertEqual(request["catalogVersion"], "1.2.0")
            self.assertEqual(request["selectionPolicyVersion"], "1.0.0")
            self.assertEqual(request["expectedLogicalCatalogSha256"], "5" * 64)
            self.assertEqual(request["maxDatabaseBytes"], 250 * 1024 * 1024)

    def test_receipt_rejects_future_or_demo_semver(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = self._fixture(temporary)
            receipt = self._prepare(fixture)
            demo = copy.deepcopy(receipt)
            demo["catalogVersion"] = "1.2.0-demo.1"
            with self.assertRaisesRegex(release_input.ReleaseInputError, "production semantic version"):
                release_input.validate_release_input(demo)

    def test_schema_has_no_additional_properties_and_matches_runtime_keys(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), release_input._RECEIPT_KEYS)
        self.assertFalse(schema["properties"]["inputs"]["additionalProperties"])
        self.assertEqual(
            set(schema["properties"]["inputs"]["required"]),
            set(release_input._EXPECTED_INPUTS),
        )


if __name__ == "__main__":
    unittest.main()
