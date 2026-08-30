from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import production_catalog_request


class ProductionCatalogRequestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        for relative in (
            "normalized/handoff.json",
            "quality/handoff.json",
            "basic-exclusions/handoff.json",
            "quality/policy.json",
            "policies/open-food-facts.json",
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
            "sourcePolicyPaths": ["policies/open-food-facts.json"],
            "databaseOutputPath": "output/catalog.sqlite3",
            "manifestOutputPath": "output/catalog-manifest.json",
            "logicalDumpOutputPath": "output/catalog-logical.json",
            "releaseNotesOutputPath": "output/catalog-release.md",
            "catalogVersion": "1.0.0",
            "selectionPolicyVersion": "1.0.0",
            "generatedAt": "2026-08-30T10:45:00Z",
            "sourceCommit": "6fca2ef70c467a36f43e3c5841a6e98c13f98699",
            "workflowRun": "github-actions:33306796756",
            "maxDatabaseBytes": 262144000,
        }

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_accepts_complete_local_only_request_and_resolves_under_root(self):
        validated = production_catalog_request.validate_request(self.request)
        resolved = production_catalog_request.resolve_request_paths(validated, self.root)
        self.assertEqual(resolved["evidenceHandoffPath"], self.root / "normalized/handoff.json")
        self.assertEqual(
            resolved["basicExclusionsHandoffPath"],
            self.root / "basic-exclusions/handoff.json",
        )
        self.assertEqual(resolved["databaseOutputPath"], self.root / "output/catalog.sqlite3")
        self.assertEqual(resolved["sourcePolicyPaths"], [self.root / "policies/open-food-facts.json"])

    def test_rejects_unknown_fields_instead_of_silently_weakening_contract(self):
        request = dict(self.request, downloadURL="https://example.invalid/catalog.json")
        with self.assertRaisesRegex(production_catalog_request.BuildRequestError, "unexpected keys"):
            production_catalog_request.validate_request(request)

    def test_rejects_legacy_unbound_basic_exclusions_path(self):
        request = dict(self.request)
        request["basicExclusionsPath"] = "selection/basic-exclusions.json"
        with self.assertRaisesRegex(production_catalog_request.BuildRequestError, "unexpected keys"):
            production_catalog_request.validate_request(request)

    def test_rejects_missing_digest_bound_basic_exclusions_handoff(self):
        request = dict(self.request)
        del request["basicExclusionsHandoffPath"]
        with self.assertRaisesRegex(production_catalog_request.BuildRequestError, "missing required keys"):
            production_catalog_request.validate_request(request)

    def test_rejects_traversal_and_absolute_paths(self):
        for bad in ("../evidence.json", "/tmp/evidence.json", r"normalized\handoff.json"):
            request = dict(self.request, evidenceHandoffPath=bad)
            with self.subTest(path=bad):
                with self.assertRaises(production_catalog_request.BuildRequestError):
                    production_catalog_request.validate_request(request)

    def test_rejects_non_exact_source_commit(self):
        request = dict(self.request, sourceCommit="6fca2ef")
        with self.assertRaisesRegex(production_catalog_request.BuildRequestError, "exact lowercase 40-character"):
            production_catalog_request.validate_request(request)

    def test_rejects_output_input_alias(self):
        request = dict(self.request, databaseOutputPath="quality/policy.json")
        with self.assertRaisesRegex(production_catalog_request.BuildRequestError, "overlap immutable inputs"):
            production_catalog_request.validate_request(request)

    def test_rejects_duplicate_or_unbounded_source_policy_sets(self):
        request = dict(
            self.request,
            sourcePolicyPaths=["policies/open-food-facts.json", "policies/open-food-facts.json"],
        )
        with self.assertRaisesRegex(production_catalog_request.BuildRequestError, "duplicates"):
            production_catalog_request.validate_request(request)

        request = dict(
            self.request,
            sourcePolicyPaths=[f"policies/source-{index}.json" for index in range(33)],
        )
        with self.assertRaisesRegex(production_catalog_request.BuildRequestError, "reviewed source-policy bound"):
            production_catalog_request.validate_request(request)

    def test_rejects_database_budget_above_reviewed_runtime_limit(self):
        request = dict(self.request, maxDatabaseBytes=262144001)
        with self.assertRaisesRegex(production_catalog_request.BuildRequestError, "between 1 and 262144000"):
            production_catalog_request.validate_request(request)

    def test_load_request_rejects_non_object_json(self):
        path = self.root / "request.json"
        path.write_text("[]\n", encoding="utf-8")
        with self.assertRaisesRegex(production_catalog_request.BuildRequestError, "must be a JSON object"):
            production_catalog_request.load_request(path)

    def test_committed_schema_and_sample_are_semantically_valid(self):
        schema = ROOT / "Data" / "catalog" / "production-catalog-build-request-v1.schema.json"
        sample = ROOT / "Data" / "catalog" / "sample-production-catalog-build-request-v1.json"
        raw_schema = json.loads(schema.read_text(encoding="utf-8"))
        self.assertEqual(raw_schema["type"], "object")
        self.assertIn("basicExclusionsHandoffPath", raw_schema["required"])
        self.assertNotIn("basicExclusionsPath", raw_schema["properties"])
        validated = production_catalog_request.validate_request(
            json.loads(sample.read_text(encoding="utf-8"))
        )
        self.assertEqual(validated["schemaVersion"], 1)

    def test_real_workflow_handoffs_are_digest_snapshot_and_policy_bound(self):
        from catalog_workflow_contract import WorkflowContract
        from catalog_workflow_handoff import emit_handoff

        contract = WorkflowContract.load(
            ROOT / "Data" / "workflows" / "catalog-workflow-contract-v1.json"
        )
        normalized_payload = self.root / "normalized" / "payload" / "evidence.json"
        quality_payload = self.root / "quality" / "payload" / "quality-report.json"
        exclusions_payload = self.root / "basic-exclusions" / "payload" / "basic-exclusions.json"
        normalized_payload.parent.mkdir(parents=True, exist_ok=True)
        quality_payload.parent.mkdir(parents=True, exist_ok=True)
        exclusions_payload.parent.mkdir(parents=True, exist_ok=True)
        normalized_payload.write_text("{\"schemaVersion\":1}\n", encoding="utf-8")
        quality_payload.write_text("{\"schemaVersion\":1}\n", encoding="utf-8")
        exclusions_payload.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "selectionPolicyVersion": "1.0.0",
                    "records": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        common = dict(
            contract=contract,
            source_key="open-food-facts",
            snapshot_id="off-2026-08-30",
            producer_commit="0123456789abcdef0123456789abcdef01234567",
            producer_workflow="normalize-and-diff.yml",
            run_id="33306796756",
            record_count=1,
            completeness="complete",
            redistribution_class="redistributable",
            content_schema_version="1",
            created_at="2026-08-30T10:45:00Z",
        )
        evidence = emit_handoff(
            artifact_kind="normalized-evidence",
            payload=normalized_payload,
            payload_relative_path="payload/evidence.json",
            **common,
        )
        quality_common = dict(common)
        quality_common["producer_workflow"] = "catalog-quality.yml"
        quality = emit_handoff(
            artifact_kind="quality-report",
            payload=quality_payload,
            payload_relative_path="payload/quality-report.json",
            **quality_common,
        )
        exclusions_common = dict(common)
        exclusions_common["record_count"] = 0
        exclusions = emit_handoff(
            artifact_kind="basic-exclusions",
            payload=exclusions_payload,
            payload_relative_path="payload/basic-exclusions.json",
            **exclusions_common,
        )
        (self.root / "normalized" / "handoff.json").write_text(
            json.dumps(evidence) + "\n", encoding="utf-8"
        )
        (self.root / "quality" / "handoff.json").write_text(
            json.dumps(quality) + "\n", encoding="utf-8"
        )
        (self.root / "basic-exclusions" / "handoff.json").write_text(
            json.dumps(exclusions) + "\n", encoding="utf-8"
        )

        resolved = production_catalog_request.resolve_request_paths(self.request, self.root)
        evidence_path, quality_path, basic_path = production_catalog_request.validate_build_handoffs(
            resolved=resolved,
            workflow_contract_path=ROOT / "Data" / "workflows" / "catalog-workflow-contract-v1.json",
            selection_policy_version="1.0.0",
        )
        self.assertEqual(evidence_path, normalized_payload)
        self.assertEqual(quality_path, quality_payload)
        self.assertEqual(basic_path, exclusions_payload)

        quality["snapshotId"] = "different-snapshot"
        (self.root / "quality" / "handoff.json").write_text(
            json.dumps(quality) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(
            production_catalog_request.BuildRequestError,
            "different snapshotId",
        ):
            production_catalog_request.validate_build_handoffs(
                resolved=resolved,
                workflow_contract_path=ROOT / "Data" / "workflows" / "catalog-workflow-contract-v1.json",
                selection_policy_version="1.0.0",
            )

        quality["snapshotId"] = "off-2026-08-30"
        (self.root / "quality" / "handoff.json").write_text(
            json.dumps(quality) + "\n", encoding="utf-8"
        )
        exclusions_payload.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "selectionPolicyVersion": "0.9.0",
                    "records": [],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        # The handoff digest must be regenerated for a meaningful policy-binding test.
        exclusions = emit_handoff(
            artifact_kind="basic-exclusions",
            payload=exclusions_payload,
            payload_relative_path="payload/basic-exclusions.json",
            **exclusions_common,
        )
        (self.root / "basic-exclusions" / "handoff.json").write_text(
            json.dumps(exclusions) + "\n", encoding="utf-8"
        )
        with self.assertRaisesRegex(
            production_catalog_request.BuildRequestError,
            "selection-policy version differs",
        ):
            production_catalog_request.validate_build_handoffs(
                resolved=resolved,
                workflow_contract_path=ROOT / "Data" / "workflows" / "catalog-workflow-contract-v1.json",
                selection_policy_version="1.0.0",
            )


if __name__ == "__main__":
    unittest.main()
