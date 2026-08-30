from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "production_catalog_proposal.py"
SPEC = importlib.util.spec_from_file_location("production_catalog_proposal", MODULE_PATH)
assert SPEC and SPEC.loader
proposal = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = proposal
SPEC.loader.exec_module(proposal)

COMMIT = "a" * 40
SNAPSHOT = "off-2026-08-30"
SOURCE = "open-food-facts"


def write_payload(root: Path, name: str, data: bytes) -> tuple[Path, str]:
    payload = root / "payload" / name
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(data)
    return payload, hashlib.sha256(data).hexdigest()


def handoff(kind: str, source: str, digest: str, byte_count: int, record_count: int, workflow: str, path: str) -> dict:
    return {
        "schemaVersion": 1,
        "artifactKind": kind,
        "sourceKey": source,
        "snapshotId": SNAPSHOT,
        "producer": {"repository": "StreamScapeTV/halal-food-eu", "commitSha": COMMIT, "workflow": workflow, "runId": "12345"},
        "payload": {"relativePath": path, "sha256": digest, "byteCount": byte_count},
        "recordCount": record_count,
        "completeness": "complete",
        "redistributionClass": "redistributable" if kind != "quality-report" else "metadata-only",
        "createdAt": "2026-08-30T12:00:00Z",
    }


class ProductionProposalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database_root = root / "database"
        self.manifest_root = root / "manifest"
        self.quality_root = root / "quality"
        database = b"sqlite-production-catalog"
        _, database_sha = write_payload(self.database_root, "catalog.sqlite3", database)
        quality = {
            "sourceKey": SOURCE,
            "snapshotID": SNAPSHOT,
            "status": "pass",
            "reportSha256": "b" * 64,
            "changes": {
                "available": True,
                "baseline": "none",
                "additions": 42,
                "formulationChanges": 2,
                "removals": 1,
                "statusChanges": [
                    {"gtin": "0000000000001", "from": "unknown", "to": "questionable"}
                ],
                "reviewQueueCount": 3,
            },
            "metrics": {
                "formulationFreshness": {
                    "fresh": 39,
                    "refresh-recommended": 1,
                    "stale": 2,
                    "date-unknown": 0,
                    "changed-unreviewed": 0,
                }
            },
            "sourceRights": {
                "approved": True,
                "fixtureOnly": False,
                "licenseIdentifier": "ODbL-1.0",
                "attributionPresent": True,
            },
        }
        quality_bytes = (json.dumps(quality, indent=2, sort_keys=True) + "\n").encode()
        _, quality_sha = write_payload(self.quality_root, "quality-report.json", quality_bytes)
        manifest = {
            "manifestSchemaVersion": 3,
            "schemaVersion": 2,
            "catalogVersion": "1.4.0",
            "methodologyVersion": "1.0.0",
            "selectionPolicyVersion": "1.0.0",
            "sourceCommit": COMMIT,
            "recordCount": 42,
            "sha256": database_sha,
            "qualityGate": {
                "sourceKey": SOURCE,
                "snapshotID": SNAPSHOT,
                "reportFileSha256": quality_sha,
                "reportSha256": quality["reportSha256"],
                "evaluatedAt": "2026-08-30T12:00:00Z",
            },
            "counts": {"products": 42, "basicExclusions": 7, "unreviewedProducts": 3},
            "statusDistribution": {"unknown": 42},
            "rights": {"licenses": ["ODbL-1.0"], "attributions": ["Open Food Facts"]},
        }
        manifest_bytes = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        _, manifest_sha = write_payload(self.manifest_root, "catalog-manifest.json", manifest_bytes)
        self.database_handoff = handoff("catalog-database", "aggregate", database_sha, len(database), 42, "build-catalog.yml", "payload/catalog.sqlite3")
        self.manifest_handoff = handoff("catalog-manifest", "aggregate", manifest_sha, len(manifest_bytes), 1, "build-catalog.yml", "payload/catalog-manifest.json")
        self.quality_handoff = handoff("quality-report", SOURCE, quality_sha, len(quality_bytes), 1, "catalog-quality.yml", "payload/quality-report.json")
        for name, value, root_path in (
            ("database-handoff.json", self.database_handoff, self.database_root),
            ("manifest-handoff.json", self.manifest_handoff, self.manifest_root),
            ("quality-handoff.json", self.quality_handoff, self.quality_root),
        ):
            (root_path / name).write_text(json.dumps(value) + "\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def build(self) -> dict:
        return proposal.prepare_report(
            source_key=SOURCE,
            snapshot_id=SNAPSHOT,
            database_handoff_path=self.database_root / "database-handoff.json",
            database_root=self.database_root,
            manifest_handoff_path=self.manifest_root / "manifest-handoff.json",
            manifest_root=self.manifest_root,
            quality_handoff_path=self.quality_root / "quality-handoff.json",
            quality_root=self.quality_root,
        )

    def rewrite(self, root: Path, name: str, value: dict) -> None:
        (root / name).write_text(json.dumps(value) + "\n", encoding="utf-8")

    def test_production_report_binds_source_snapshot_and_reviewed_catalog(self) -> None:
        report = self.build()
        self.assertEqual(report["sourceKey"], SOURCE)
        self.assertEqual(report["snapshotId"], SNAPSHOT)
        self.assertEqual(report["catalogVersion"], "1.4.0")
        self.assertEqual(report["recordCount"], 42)
        self.assertFalse(report["fixtureOnly"])
        self.assertTrue(report["requiresHumanReview"])
        self.assertFalse(report["materialChangeAutoMergeAllowed"])
        self.assertTrue(report["proposalKey"].startswith("catalog-update/open-food-facts-"))
        self.assertEqual(report["proposalKey"], self.build()["proposalKey"])
        summary = report["releaseSummary"]
        self.assertEqual(summary["recordCount"], 42)
        self.assertEqual(summary["schemaVersion"], 2)
        self.assertEqual(summary["methodologyVersion"], "1.0.0")
        self.assertEqual(summary["changeComparison"]["additions"], 42)
        self.assertEqual(summary["changeComparison"]["formulationChanges"], 2)
        self.assertEqual(summary["changeComparison"]["removals"], 1)
        self.assertEqual(summary["changeComparison"]["statusChangeCount"], 1)
        self.assertEqual(summary["changeComparison"]["reviewQueueCount"], 3)
        self.assertEqual(summary["staleRecords"], 2)
        self.assertFalse(summary["sourceLicenseChanges"]["comparisonAvailable"])

    def test_quality_source_mismatch_fails_closed(self) -> None:
        bad = copy.deepcopy(self.quality_handoff)
        bad["sourceKey"] = "open-prices"
        self.rewrite(self.quality_root, "quality-handoff.json", bad)
        with self.assertRaisesRegex(proposal.ProposalError, "source differs"):
            self.build()

    def test_manifest_database_digest_mismatch_fails_closed(self) -> None:
        path = self.manifest_root / "payload/catalog-manifest.json"
        manifest = json.loads(path.read_text())
        manifest["sha256"] = "0" * 64
        data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        path.write_bytes(data)
        changed = copy.deepcopy(self.manifest_handoff)
        changed["payload"]["sha256"] = hashlib.sha256(data).hexdigest()
        changed["payload"]["byteCount"] = len(data)
        self.rewrite(self.manifest_root, "manifest-handoff.json", changed)
        with self.assertRaisesRegex(proposal.ProposalError, "database digest differs"):
            self.build()

    def test_quality_decision_mismatch_fails_closed(self) -> None:
        path = self.quality_root / "payload/quality-report.json"
        quality = json.loads(path.read_text())
        quality["status"] = "block"
        data = (json.dumps(quality, indent=2, sort_keys=True) + "\n").encode()
        path.write_bytes(data)
        changed = copy.deepcopy(self.quality_handoff)
        changed["payload"]["sha256"] = hashlib.sha256(data).hexdigest()
        changed["payload"]["byteCount"] = len(data)
        self.rewrite(self.quality_root, "quality-handoff.json", changed)
        manifest_path = self.manifest_root / "payload/catalog-manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["qualityGate"]["reportFileSha256"] = changed["payload"]["sha256"]
        manifest_data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        manifest_path.write_bytes(manifest_data)
        manifest_handoff = copy.deepcopy(self.manifest_handoff)
        manifest_handoff["payload"]["sha256"] = hashlib.sha256(manifest_data).hexdigest()
        manifest_handoff["payload"]["byteCount"] = len(manifest_data)
        self.rewrite(self.manifest_root, "manifest-handoff.json", manifest_handoff)
        with self.assertRaisesRegex(proposal.ProposalError, "passing quality"):
            self.build()

    def test_proposal_workflow_routes_production_through_semantic_gate(self) -> None:
        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github/workflows/propose-catalog-update.yml").read_text(encoding="utf-8")
        self.assertIn("source_key:", workflow)
        self.assertIn("Tools/production_catalog_proposal.py", workflow)
        self.assertIn('--source-key "$SOURCE_KEY"', workflow)
        self.assertNotIn("--source-key aggregate", workflow)

    def test_demo_version_fails_closed_for_production(self) -> None:
        path = self.manifest_root / "payload/catalog-manifest.json"
        manifest = json.loads(path.read_text())
        manifest["catalogVersion"] = "0.2.0-demo.1"
        data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
        path.write_bytes(data)
        changed = copy.deepcopy(self.manifest_handoff)
        changed["payload"]["sha256"] = hashlib.sha256(data).hexdigest()
        changed["payload"]["byteCount"] = len(data)
        self.rewrite(self.manifest_root, "manifest-handoff.json", changed)
        with self.assertRaisesRegex(proposal.ProposalError, "non-demo semantic"):
            self.build()


if __name__ == "__main__":
    unittest.main()
