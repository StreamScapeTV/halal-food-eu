#!/usr/bin/env python3
"""Open Food Facts adapter command line for trusted catalog workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import open_food_facts_common as common
from open_food_facts_acquire import (
    MAX_DEFAULT_COMPRESSED_BYTES,
    MAX_DEFAULT_MALFORMED_RATE,
    acquire,
)
from open_food_facts_normalize import normalize_snapshot


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    acquire_parser = subparsers.add_parser("acquire", help="Acquire a bounded OFF snapshot")
    acquire_parser.add_argument("--output", type=Path, required=True)
    acquire_parser.add_argument("--metadata-output", type=Path, required=True)
    acquire_parser.add_argument("--snapshot-id", required=True)
    acquire_parser.add_argument("--mode", choices=("fixture", "sample", "full"), required=True)
    acquire_parser.add_argument("--fixture", type=Path, default=common.DEFAULT_FIXTURE)
    acquire_parser.add_argument("--sample-records", type=int, default=10_000)
    acquire_parser.add_argument("--max-compressed-bytes", type=int, default=MAX_DEFAULT_COMPRESSED_BYTES)
    acquire_parser.add_argument("--max-malformed-rate", type=float, default=MAX_DEFAULT_MALFORMED_RATE)
    acquire_parser.add_argument("--retrieved-at")
    acquire_parser.add_argument("--source-policy", type=Path, default=common.DEFAULT_SOURCE_POLICY)

    normalize_parser = subparsers.add_parser("normalize", help="Normalize/select an admitted OFF snapshot")
    normalize_parser.add_argument("--snapshot", type=Path, required=True)
    normalize_parser.add_argument("--evidence-output", type=Path, required=True)
    normalize_parser.add_argument("--selection-output", type=Path, required=True)
    normalize_parser.add_argument("--quality-output", type=Path, required=True)
    normalize_parser.add_argument("--change-output", type=Path, required=True)
    normalize_parser.add_argument("--source-policy", type=Path, default=common.DEFAULT_SOURCE_POLICY)
    normalize_parser.add_argument("--selection-policy", type=Path, default=common.DEFAULT_SELECTION_POLICY)
    normalize_parser.add_argument("--previous-evidence", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    # Acquisition projections must retain the unlocalized exact ingredient field too.
    # The source contract intentionally keeps this narrow; mutating the reviewed set
    # here avoids broadening raw payloads while preserving schema-1004 records that
    # expose only ``ingredients_text``.
    common.PROJECTED_FIXED_FIELDS.add("ingredients_text")

    if args.command == "acquire":
        policy = common.load_source_policy(args.source_policy)
        metadata = acquire(
            output=args.output,
            snapshot_id=args.snapshot_id,
            mode=args.mode,
            policy=policy,
            fixture=args.fixture,
            sample_records=args.sample_records,
            max_compressed_bytes=args.max_compressed_bytes,
            max_malformed_rate=args.max_malformed_rate,
            retrieved_at=args.retrieved_at,
        )
        _write_json(args.metadata_output, metadata)
        print(json.dumps(metadata, sort_keys=True, separators=(",", ":")))
        return

    policy = common.load_source_policy(args.source_policy)
    selection_policy = common.load_json(args.selection_policy)
    previous = common.load_json(args.previous_evidence) if args.previous_evidence else None
    evidence, reports, changes = normalize_snapshot(
        snapshot=args.snapshot,
        policy=policy,
        selection_policy=selection_policy,
        previous_evidence=previous,
    )
    _write_json(args.evidence_output, evidence)
    _write_json(args.selection_output, reports["selection"])
    _write_json(args.quality_output, reports["quality"])
    _write_json(args.change_output, changes)
    print(
        json.dumps(
            {
                "sourceKey": common.SOURCE_KEY,
                "snapshotID": changes["snapshotID"],
                "selected": reports["selection"]["report"]["includedProducts"],
                "basicExcluded": reports["selection"]["report"]["excludedBasicProducts"],
                "invalidExcluded": reports["selection"]["report"]["excludedInvalidRecords"],
                "formulationChanges": changes["formulationChanges"],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()
