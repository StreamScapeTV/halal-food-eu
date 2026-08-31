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
from catalog_workflow_contract import WorkflowContract
from catalog_workflow_handoff import emit_handoff


class BasicExclusionHandoffCountTests(unittest.TestCase):
    def test_rejects_digest_valid_basic_exclusion_handoff_with_false_record_count(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for relative in (
                "normalized/payload/evidence.json",
                "quality/payload/quality-report.json",
                "basic-exclusions/payload/basic-exclusions.json",
                "quality/policy.json",
                "policies/open-food-facts.json",
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}\n", encoding="utf-8")

            exclusions_payload = root / "basic-exclusions/payload/basic-exclusions.json"
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

            contract = WorkflowContract.load(ROOT / "Data/workflows/catalog-workflow-contract-v1.json")
            common = dict(
                contract=contract,
                source_key="open-food-facts",
                snapshot_id="off-2026-08-30",
                producer_commit="0123456789abcdef0123456789abcdef01234567",
                run_id="33306796756",
                completeness="complete",
                redistribution_class="redistributable",
                content_schema_version="1",
                created_at="2026-08-30T10:45:00Z",
            )

            normalized_payload = root / "normalized/payload/evidence.json"
            evidence = emit_handoff(
                artifact_kind="normalized-evidence",
                producer_workflow="normalize-and-diff.yml",
                payload=normalized_payload,
                payload_relative_path="payload/evidence.json",
                record_count=1,
                **common,
            )
            quality_payload = root / "quality/payload/quality-report.json"
            quality = emit_handoff(
                artifact_kind="quality-report",
                producer_workflow="catalog-quality.yml",
                payload=quality_payload,
                payload_relative_path="payload/quality-report.json",
                record_count=1,
                **common,
            )
            exclusions = emit_handoff(
                artifact_kind="basic-exclusions",
                producer_workflow="normalize-and-diff.yml",
                payload=exclusions_payload,
                payload_relative_path="payload/basic-exclusions.json",
                record_count=1,
                **common,
            )

            for relative, handoff in (
                ("normalized/handoff.json", evidence),
                ("quality/handoff.json", quality),
                ("basic-exclusions/handoff.json", exclusions),
            ):
                path = root / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(handoff) + "\n", encoding="utf-8")

            request = {
                "schemaVersion": 1,
                "evidenceHandoffPath": "normalized/handoff.json",
                "qualityHandoffPath": "quality/handoff.json",
                "basicExclusionsHandoffPath": "basic-exclusions/handoff.json",
                "qualityPolicyPath": "quality/policy.json",
                "sourcePolicyPaths": ["policies/open-food-facts.json"],
                "databaseOutputPath": "output/catalog.sqlite3",
                "manifestOutputPath": "output/catalog-manifest.json",
                "catalogVersion": "1.0.0",
                "selectionPolicyVersion": "1.0.0",
                "generatedAt": "2026-08-30T10:45:00Z",
                "sourceCommit": "0123456789abcdef0123456789abcdef01234567",
                "workflowRun": "github-actions:33306796756",
                "maxDatabaseBytes": 262144000,
            }
            resolved = production_catalog_request.resolve_request_paths(request, root)
            with self.assertRaisesRegex(
                production_catalog_request.BuildRequestError,
                "recordCount does not match payload records",
            ):
                production_catalog_request.validate_build_handoffs(
                    resolved=resolved,
                    workflow_contract_path=ROOT / "Data/workflows/catalog-workflow-contract-v1.json",
                    selection_policy_version="1.0.0",
                )


if __name__ == "__main__":
    unittest.main()
