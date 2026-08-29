from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import tempfile
import unittest
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "catalog_workflow.py"
sys.path.insert(0, str(MODULE_PATH.parent))
SPEC = importlib.util.spec_from_file_location("catalog_workflow", MODULE_PATH)
assert SPEC and SPEC.loader
catalog_workflow = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = catalog_workflow
SPEC.loader.exec_module(catalog_workflow)

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "Data/workflows/catalog-workflow-contract-v1.json"
HANDOFF_PATH = ROOT / "Data/workflows/sample-workflow-handoff-v1.json"
PAYLOAD_PATH = ROOT / "Data/workflows/synthetic-source-records.jsonl"


class WorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = catalog_workflow.WorkflowContract.load(CONTRACT_PATH)
        self.handoff = json.loads(HANDOFF_PATH.read_text(encoding="utf-8"))

    def test_contract_has_expected_stage_order_and_retry_cap(self) -> None:
        self.assertEqual(
            self.contract.stage_order,
            (
                "source-policy",
                "acquire",
                "normalize-diff",
                "quality",
                "build",
                "proposal",
                "ios-validation",
                "release",
                "health",
            ),
        )
        self.assertEqual(self.contract.retry_delays(), (5, 10, 20, 40))

    def test_only_fixture_source_is_initially_admitted_and_needs_no_secret(self) -> None:
        source = self.contract.validate_source("synthetic-fixture", "fixture-2026-08-29", "fixture")
        self.assertFalse(source.credentials_required)
        self.assertEqual(source.access_method, "committed-fixture")
        with self.assertRaisesRegex(catalog_workflow.ContractError, "not registered"):
            self.contract.validate_source("open-food-facts", "2026-08-29", "full")

    def test_fixture_source_cannot_be_promoted_to_full_mode(self) -> None:
        with self.assertRaisesRegex(catalog_workflow.ContractError, "fixture mode"):
            self.contract.validate_source("synthetic-fixture", "fixture-2026-08-29", "full")

    def test_handoff_matches_payload_digest_and_bounds(self) -> None:
        validated = catalog_workflow.validate_handoff(
            self.contract,
            self.handoff,
            consumer_stage="normalize-diff",
            payload_root=ROOT,
        )
        self.assertEqual(validated["recordCount"], 2)
        self.assertEqual(validated["completeness"], "complete")

    def test_partial_snapshot_cannot_enter_normalization(self) -> None:
        partial = copy.deepcopy(self.handoff)
        partial["completeness"] = "partial"
        with self.assertRaisesRegex(catalog_workflow.ContractError, "rejects partial"):
            catalog_workflow.validate_handoff(self.contract, partial, consumer_stage="normalize-diff")

    def test_wrong_artifact_kind_cannot_enter_build(self) -> None:
        with self.assertRaisesRegex(catalog_workflow.ContractError, "does not accept"):
            catalog_workflow.validate_handoff(self.contract, self.handoff, consumer_stage="build")

    def test_unsafe_payload_paths_fail_closed(self) -> None:
        for path in ("../secret", "/tmp/payload", "C:/payload", "a/../../payload", "a\\payload"):
            mutated = copy.deepcopy(self.handoff)
            mutated["payload"]["relativePath"] = path
            with self.subTest(path=path):
                with self.assertRaises(catalog_workflow.ContractError):
                    catalog_workflow.validate_handoff(self.contract, mutated)

    def test_digest_mismatch_fails_closed(self) -> None:
        mutated = copy.deepcopy(self.handoff)
        mutated["payload"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(catalog_workflow.ContractError, "SHA-256"):
            catalog_workflow.validate_handoff(self.contract, mutated, payload_root=ROOT)

    def test_catalog_database_must_be_redistributable(self) -> None:
        payload = copy.deepcopy(self.handoff)
        payload["artifactKind"] = "catalog-database"
        payload["sourceKey"] = "aggregate"
        payload["redistributionClass"] = "restricted"
        with self.assertRaisesRegex(catalog_workflow.ContractError, "does not permit"):
            catalog_workflow.validate_handoff(self.contract, payload)

    def test_size_and_record_bounds_are_enforced(self) -> None:
        too_large = copy.deepcopy(self.handoff)
        too_large["payload"]["byteCount"] = self.contract.artifacts["source-snapshot"]["maxBytes"] + 1
        with self.assertRaisesRegex(catalog_workflow.ContractError, "maxBytes"):
            catalog_workflow.validate_handoff(self.contract, too_large)
        too_many = copy.deepcopy(self.handoff)
        too_many["recordCount"] = self.contract.artifacts["source-snapshot"]["maxRecords"] + 1
        with self.assertRaisesRegex(catalog_workflow.ContractError, "maxRecords"):
            catalog_workflow.validate_handoff(self.contract, too_many)

    def test_unknown_fields_fail_closed(self) -> None:
        mutated = copy.deepcopy(self.handoff)
        mutated["downloadUrl"] = "https://example.invalid/secret"
        with self.assertRaisesRegex(catalog_workflow.ContractError, "unexpected fields"):
            catalog_workflow.validate_handoff(self.contract, mutated)

    def test_emit_handoff_uses_actual_payload_digest(self) -> None:
        raw = catalog_workflow.emit_handoff(
            contract=self.contract,
            artifact_kind="source-snapshot",
            source_key="synthetic-fixture",
            snapshot_id="fixture-local",
            producer_commit="1" * 40,
            producer_workflow="acquire-catalog.yml",
            run_id="123",
            payload=PAYLOAD_PATH,
            payload_relative_path="payload/source-records.jsonl",
            record_count=2,
            completeness="complete",
            redistribution_class="redistributable",
            content_schema_version="synthetic-source-v1",
            created_at="2026-08-29T12:00:00Z",
        )
        self.assertEqual(raw["payload"]["sha256"], hashlib.sha256(PAYLOAD_PATH.read_bytes()).hexdigest())
        self.assertEqual(raw["payload"]["byteCount"], PAYLOAD_PATH.stat().st_size)

    def test_proposal_key_is_stable_and_bounded(self) -> None:
        digest = hashlib.sha256(b"catalog").hexdigest()
        first = catalog_workflow.proposal_key("aggregate", "2026-08-29", digest)
        second = catalog_workflow.proposal_key("aggregate", "2026-08-29", digest)
        self.assertEqual(first, second)
        self.assertTrue(first.startswith("catalog-update/aggregate-"))
        self.assertLessEqual(len(first), 64)

    def test_health_key_is_stable_and_deduplicated_by_condition_source(self) -> None:
        key = catalog_workflow.health_key("stale-source", "synthetic-fixture")
        self.assertEqual(key, catalog_workflow.health_key("stale-source", "synthetic-fixture"))
        self.assertNotEqual(key, catalog_workflow.health_key("stale-source", None))

    def test_schema_shape_tracks_semantic_validator_required_fields(self) -> None:
        schema = json.loads((ROOT / "Data/workflows/workflow-handoff-v1.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(schema["properties"]["schemaVersion"]["const"], 1)
        self.assertEqual(
            set(schema["required"]),
            {
                "schemaVersion",
                "artifactKind",
                "sourceKey",
                "snapshotId",
                "producer",
                "payload",
                "recordCount",
                "completeness",
                "redistributionClass",
                "createdAt",
            },
        )
        self.assertFalse(schema["additionalProperties"])


class WorkflowYamlPolicyTests(unittest.TestCase):
    def test_issue_7_workflows_pass_policy_lint(self) -> None:
        checked = catalog_workflow.validate_workflows(ROOT / ".github/workflows")
        self.assertIn("source-policy.yml", checked)
        self.assertIn("scheduled-catalog-refresh.yml", checked)
        self.assertIn("catalog-release.yml", checked)

    def test_unpinned_action_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "bad.yml").write_text(
                "name: bad\non: workflow_dispatch\npermissions:\n  contents: read\njobs:\n  x:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@v6\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(catalog_workflow.ContractError, "unpinned"):
                catalog_workflow.validate_workflows(root)

    def test_trusted_workflow_cannot_be_pull_request_triggered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "acquire-catalog.yml").write_text(
                "name: bad\non:\n  pull_request:\npermissions:\n  contents: read\njobs:\n  x:\n    runs-on: ubuntu-latest\n    steps:\n      - run: true\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(catalog_workflow.ContractError, "must not trigger"):
                catalog_workflow.validate_workflows(root)

    def test_default_branch_only_workflow_requires_ref_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "scheduled-catalog-refresh.yml").write_text(
                "name: bad\non:\n  schedule:\n    - cron: '17 3 * * 3'\n  workflow_dispatch:\npermissions:\n  contents: read\njobs:\n  x:\n    runs-on: ubuntu-latest\n    steps:\n      - run: true\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(catalog_workflow.ContractError, "reviewed default branch"):
                catalog_workflow.validate_workflows(root)


if __name__ == "__main__":
    unittest.main()
