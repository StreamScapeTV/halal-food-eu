#!/usr/bin/env python3
"""Build the immutable app-bundled SQLite catalog from Git-tracked CSV shards."""
from __future__ import annotations
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import catalog_source_shards
import product_search_index
import production_catalog

ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_bundle(*, source_manifest_path: Path, database_path: Path, manifest_path: Path, source_commit: str, workflow_run: str) -> dict:
    source_set = catalog_source_shards.validate_source_set(source_manifest_path)
    manifest = source_set.manifest
    source_defs = {item["sourceKey"]: item for item in manifest["sources"]}
    primary_source = source_defs[manifest["primaryQualitySourceKey"]]
    with tempfile.TemporaryDirectory(prefix="hfeu-bundle-") as tmp:
        tmpdir = Path(tmp)
        evidence_path = tmpdir / "evidence.json"
        change_path = tmpdir / "change-report.json"
        quality_path = tmpdir / "quality-report.json"
        quality_summary_path = tmpdir / "quality-summary.md"
        catalog_source_shards.write_evidence(source_set, evidence_path)
        _write_json(change_path, {
            "schemaVersion": 1,
            "sourceKey": manifest["primaryQualitySourceKey"],
            "snapshotID": primary_source["snapshotID"],
            "baseline": "git-bundle-source",
            "additions": len(source_set.rows),
            "unchanged": 0,
            "formulationChanges": 0,
            "removals": 0,
            "removedSelections": [],
            "addedSelections": [],
            "reviewQueue": [],
            "noCompletenessClaim": True,
        })
        quality_command = [
            sys.executable, str(ROOT / "Tools/catalog_quality.py"), "evaluate",
            "--evidence", str(evidence_path), "--change-report", str(change_path),
            "--source-key", manifest["primaryQualitySourceKey"], "--snapshot-id", primary_source["snapshotID"],
            "--as-of", manifest["generatedAt"], "--output", str(quality_path), "--summary-output", str(quality_summary_path),
        ]
        result = subprocess.run(quality_command, cwd=ROOT, text=True, capture_output=True)
        if result.returncode != 0:
            summary = quality_summary_path.read_text(encoding="utf-8") if quality_summary_path.exists() else ""
            raise ValueError(f"catalog quality gate failed\n{result.stdout}\n{result.stderr}\n{summary}")
        production_catalog.build_catalog(
            evidence_path=evidence_path,
            database_path=database_path,
            manifest_path=manifest_path,
            policy_paths=[ROOT / item["policyPath"] for item in manifest["sources"]],
            basic_exclusions_path=None,
            quality_report_path=quality_path,
            quality_policy_path=ROOT / manifest["qualityPolicy"]["path"],
            catalog_version=manifest["catalogVersion"],
            selection_policy_version=manifest["selectionPolicyVersion"],
            generated_at=manifest["generatedAt"],
            source_commit=source_commit,
            workflow_run=workflow_run,
        )
        product_search_index.install_search_index(database_path=database_path, manifest_path=manifest_path)
    built = json.loads(manifest_path.read_text(encoding="utf-8"))
    built["sourceShardSet"] = {
        "schemaVersion": manifest["schemaVersion"],
        "datasetID": manifest["datasetID"],
        "manifestPath": source_manifest_path.resolve().relative_to(ROOT.resolve()).as_posix(),
        "manifestSha256": catalog_source_shards.file_sha256(source_manifest_path),
        "logicalSha256": manifest["logicalSha256"],
        "recordCount": manifest["recordCount"],
        "shardCount": len(manifest["shards"]),
    }
    _write_json(manifest_path, built)
    production_catalog.validate_catalog(database_path, manifest_path)
    product_search_index.validate_search_index(database_path=database_path, manifest_path=manifest_path)
    return built


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-manifest", type=Path, default=Path("Data/catalog/bundled/de/source-manifest-v1.json"))
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-commit", default=os.environ.get("GITHUB_SHA", "0" * 40))
    parser.add_argument("--workflow-run", default=os.environ.get("GITHUB_RUN_ID", "local-git-bundle"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.chdir(ROOT)
    built = build_bundle(source_manifest_path=args.source_manifest, database_path=args.database, manifest_path=args.manifest, source_commit=args.source_commit, workflow_run=args.workflow_run)
    print(f"Built Git-backed bundled catalog {built['catalogVersion']} with {built['recordCount']} products; sha256={built['sha256']}")


if __name__ == "__main__":
    main()
