#!/usr/bin/env python3
"""Build the deterministic synthetic fixture through the production compiler path."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import production_catalog

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "Data" / "evidence" / "sample-evidence-v1.json"
QUALITY_POLICY = ROOT / "Data" / "quality" / "catalog-quality-policy-v1.json"
SOURCE_POLICIES = [
    Path("Data/evidence/fixtures/source-policies/synthetic-core.json"),
    Path("Data/evidence/fixtures/source-policies/synthetic-retailer.json"),
    Path("Data/evidence/fixtures/source-policies/synthetic-certifier.json"),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-commit", default=os.environ.get("GITHUB_SHA", "0" * 40))
    parser.add_argument("--workflow-run", default=os.environ.get("GITHUB_RUN_ID", "local-fixture"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    os.chdir(ROOT)
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    selections = sorted(
        ({"gtin": item["gtin"], "market": item["market"]} for item in evidence["currentSelections"]),
        key=lambda item: (item["gtin"], item["market"]),
    )

    with tempfile.TemporaryDirectory(prefix="halal-food-eu-production-fixture-") as temp_dir:
        temp = Path(temp_dir)
        changes = temp / "change-report.json"
        quality_report = temp / "quality-report.json"
        quality_summary = temp / "quality-summary.md"
        changes.write_text(
            json.dumps(
                {
                    "schemaVersion": 1,
                    "sourceKey": "synthetic-fixture",
                    "snapshotID": "production-runtime-fixture-v1",
                    "baseline": "none",
                    "additions": len(selections),
                    "unchanged": 0,
                    "formulationChanges": 0,
                    "removals": 0,
                    "removedSelections": [],
                    "addedSelections": selections,
                    "reviewQueue": [],
                    "noCompletenessClaim": True,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        evaluation = subprocess.run(
            [
                sys.executable,
                str(ROOT / "Tools" / "catalog_quality.py"),
                "evaluate",
                "--evidence",
                str(EVIDENCE),
                "--change-report",
                str(changes),
                "--source-key",
                "synthetic-fixture",
                "--snapshot-id",
                "production-runtime-fixture-v1",
                "--as-of",
                "2026-08-30T12:00:00Z",
                "--output",
                str(quality_report),
                "--summary-output",
                str(quality_summary),
            ],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if evaluation.returncode != 0:
            raise SystemExit(evaluation.stderr + evaluation.stdout)

        args.database.parent.mkdir(parents=True, exist_ok=True)
        args.manifest.parent.mkdir(parents=True, exist_ok=True)
        production_catalog.build_catalog(
            evidence_path=EVIDENCE,
            database_path=args.database,
            manifest_path=args.manifest,
            policy_paths=SOURCE_POLICIES,
            basic_exclusions_path=None,
            quality_report_path=quality_report,
            quality_policy_path=QUALITY_POLICY,
            catalog_version="0.2.0-demo.1",
            selection_policy_version="demo-selection-1",
            generated_at="2026-08-30T12:30:00Z",
            source_commit=args.source_commit,
            workflow_run=str(args.workflow_run),
        )
        production_catalog.validate_catalog(args.database, args.manifest)

    print(f"Built production fixture: {args.database}")


if __name__ == "__main__":
    main()
