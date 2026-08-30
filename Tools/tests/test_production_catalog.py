from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import production_catalog


class ProductionCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.evidence = self.root / "evidence.json"
        self.database = self.root / "catalog.sqlite3"
        self.manifest = self.root / "catalog-manifest.json"
        self.quality_report = self.root / "quality-report.json"
        self.quality_summary = self.root / "quality-summary.md"
        self.quality_policy = ROOT / "Data" / "quality" / "catalog-quality-policy-v1.json"

        sample = ROOT / "Data" / "evidence" / "sample-evidence-v1.json"
        self.evidence.write_bytes(sample.read_bytes())
        envelope = json.loads(self.evidence.read_text(encoding="utf-8"))

        self.policies: list[Path] = []
        for source in envelope["sources"]:
            source_key = source["sourceKey"]
            path = self.root / f"{source_key}-source-policy.json"
            path.write_text(
                json.dumps(
                    {
                        "schemaVersion": 1,
                        "sourceKey": source_key,
                        "databaseLicense": {"identifier": "synthetic-fixture"},
                        "attribution": f"Synthetic test attribution for {source_key}.",
                    },
                    sort_keys=True,
                )
                + "\n",
                encoding="utf-8",
            )
            self.policies.append(path)

        self.exclusions = self.root / "exclusions.json"
        self.exclusions.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "selectionPolicyVersion": "demo-selection-1",
                    "records": [
                        {
                            "gtin": "00200000000011",
                            "market": "DE",
                            "reason": "plain-basic-approved",
                        }
                    ],
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        self._evaluate_quality()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _evaluate_quality(self) -> None:
        changes = self.root / "change-report.json"
        changes.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "sourceKey": "synthetic-fixture",
                    "snapshotID": "production-catalog-test",
                    "baseline": "none",
                    "additions": 2,
                    "unchanged": 0,
                    "formulationChanges": 0,
                    "removals": 0,
                    "removedSelections": [],
                    "addedSelections": [
                        {"gtin": "00200000000004", "market": "DE"},
                        {"gtin": "00200000000028", "market": "DE"},
                    ],
                    "reviewQueue": [],
                    "noCompletenessClaim": True,
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        result = subprocess.run(
            [
                sys.executable,
                str(TOOLS / "catalog_quality.py"),
                "evaluate",
                "--evidence",
                str(self.evidence),
                "--change-report",
                str(changes),
                "--source-key",
                "synthetic-fixture",
                "--snapshot-id",
                "production-catalog-test",
                "--as-of",
                "2026-08-30T12:00:00Z",
                "--output",
                str(self.quality_report),
                "--summary-output",
                str(self.quality_summary),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr + result.stdout)

    def build(self, **overrides):
        args = dict(
            evidence_path=self.evidence,
            database_path=self.database,
            manifest_path=self.manifest,
            policy_paths=self.policies,
            basic_exclusions_path=self.exclusions,
            quality_report_path=self.quality_report,
            quality_policy_path=self.quality_policy,
            catalog_version="1.0.0",
            selection_policy_version="demo-selection-1",
            generated_at="2026-08-30T12:30:00Z",
            source_commit="40cd13e08ddc7d7f567b3577f3610b5991a512f6",
            workflow_run="unit-test",
            logical_dump_path=self.root / "logical.json",
            release_notes_path=self.root / "release.md",
        )
        args.update(overrides)
        return production_catalog.build_catalog(**args)

    def test_builds_canonical_evidence_and_persists_review_quality_state(self):
        manifest = self.build()
        production_catalog.validate_catalog(self.database, self.manifest)
        self.assertEqual(manifest["manifestSchemaVersion"], 3)
        self.assertEqual(manifest["schemaVersion"], 2)
        self.assertEqual(manifest["counts"]["products"], 2)
        self.assertEqual(manifest["counts"]["ingredientObservations"], 2)
        self.assertEqual(manifest["counts"]["certifications"], 1)
        self.assertEqual(manifest["counts"]["retailerEvidence"], 1)
        self.assertEqual(manifest["counts"]["remoteImageReferences"], 1)
        self.assertEqual(manifest["counts"]["basicExclusions"], 1)
        self.assertEqual(manifest["qualityGate"]["policyVersion"], "1.0.0")
        self.assertEqual(len(manifest["qualityGate"]["reportSha256"]), 64)
        self.assertEqual(len(manifest["qualityGate"]["policySha256"]), 64)

        with sqlite3.connect(self.database) as db:
            certified = db.execute(
                """SELECT a.assessed_at,a.reviewed_at,a.approved_reviewer_count,o.freshness_state
                   FROM products p
                   JOIN product_assessments a ON a.id=p.current_assessment_id
                   JOIN product_observations o ON o.id=p.current_observation_id
                   WHERE p.gtin=?""",
                ("00200000000004",),
            ).fetchone()
            self.assertEqual(
                certified,
                ("2026-08-29T01:00:00Z", "2026-08-29T02:00:00Z", 1, "fresh"),
            )
            self.assertEqual(db.execute("SELECT COUNT(*) FROM remote_image_references").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM basic_exclusions").fetchone()[0], 1)

    def test_rejects_non_passing_quality_report_even_with_valid_self_digest(self):
        import hashlib
        import production_catalog_gate

        report = json.loads(self.quality_report.read_text(encoding="utf-8"))
        report["status"] = "blocked"
        report["releaseBlockingFindings"] = [{"code": "test-blocker", "detail": "fixture"}]
        report.pop("reportSha256", None)
        report["reportSha256"] = hashlib.sha256(
            production_catalog_gate.canonical_json(report).encode("utf-8")
        ).hexdigest()
        self.quality_report.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not release-passing"):
            self.build()

    def test_rejects_quality_report_tampering(self):
        report = json.loads(self.quality_report.read_text(encoding="utf-8"))
        report["metrics"]["products"] = 99
        self.quality_report.write_text(json.dumps(report, sort_keys=True) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "self-digest mismatch"):
            self.build()

    def test_rejects_basic_exclusion_overlap(self):
        self.exclusions.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "selectionPolicyVersion": "demo-selection-1",
                    "records": [
                        {
                            "gtin": "00200000000004",
                            "market": "DE",
                            "reason": "bad-overlap",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaisesRegex(ValueError, "overlap detailed products"):
            self.build()

    def test_manifest_digest_tampering_fails(self):
        self.build()
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch"):
            production_catalog.validate_catalog(self.database, self.manifest)

    def test_budget_is_enforced(self):
        with self.assertRaisesRegex(ValueError, "exceeds reviewed budget"):
            self.build(max_database_bytes=1)

    def test_logical_dump_is_deterministic_for_reordered_canonical_arrays(self):
        first_dump = self.root / "logical-first.json"
        self.build(logical_dump_path=first_dump)

        envelope = json.loads(self.evidence.read_text(encoding="utf-8"))
        for value in envelope.values():
            if isinstance(value, list):
                value.reverse()
        reordered = self.root / "reordered-evidence.json"
        reordered.write_text(json.dumps(envelope, sort_keys=True) + "\n", encoding="utf-8")

        original_evidence = self.evidence
        original_report = self.quality_report
        original_summary = self.quality_summary
        self.evidence = reordered
        self.quality_report = self.root / "reordered-quality.json"
        self.quality_summary = self.root / "reordered-quality.md"
        self._evaluate_quality()
        second_dump = self.root / "logical-second.json"
        self.build(
            database_path=self.root / "reordered.sqlite3",
            manifest_path=self.root / "reordered-manifest.json",
            logical_dump_path=second_dump,
        )
        self.evidence = original_evidence
        self.quality_report = original_report
        self.quality_summary = original_summary

        first = json.loads(first_dump.read_text(encoding="utf-8"))
        second = json.loads(second_dump.read_text(encoding="utf-8"))
        self.assertEqual(first["projection"], second["projection"])
        self.assertEqual(first["basicExclusions"], second["basicExclusions"])


if __name__ == "__main__":
    unittest.main()
