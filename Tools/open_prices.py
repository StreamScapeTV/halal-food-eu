#!/usr/bin/env python3
"""Open Prices retailer-observation adapter for trusted catalog workflows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import open_prices_common as common
from open_prices_acquire import (
    MAX_DEFAULT_COMPRESSED_BYTES,
    MAX_DEFAULT_EXPANDED_BYTES,
    MAX_DEFAULT_RECORDS,
    acquire,
)
from open_prices_normalize import normalize_snapshot


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    acquire_parser = sub.add_parser("acquire", help="Acquire a bounded Open Prices snapshot")
    acquire_parser.add_argument("--output", type=Path, required=True)
    acquire_parser.add_argument("--metadata-output", type=Path, required=True)
    acquire_parser.add_argument("--snapshot-id", required=True)
    acquire_parser.add_argument("--mode", choices=("fixture", "sample", "full"), required=True)
    acquire_parser.add_argument("--source-policy", type=Path, default=common.DEFAULT_SOURCE_POLICY)
    acquire_parser.add_argument("--sample-records", type=int, default=10_000)
    acquire_parser.add_argument("--max-compressed-bytes", type=int, default=MAX_DEFAULT_COMPRESSED_BYTES)
    acquire_parser.add_argument("--max-expanded-bytes", type=int, default=MAX_DEFAULT_EXPANDED_BYTES)
    acquire_parser.add_argument("--max-records", type=int, default=MAX_DEFAULT_RECORDS)
    acquire_parser.add_argument("--retrieved-at")

    normalize_parser = sub.add_parser("normalize", help="Normalize Open Prices into retailer-observation evidence")
    normalize_parser.add_argument("--snapshot", type=Path, required=True)
    normalize_parser.add_argument("--metadata", type=Path, required=True)
    normalize_parser.add_argument("--evidence-output", type=Path, required=True)
    normalize_parser.add_argument("--quality-output", type=Path, required=True)
    normalize_parser.add_argument("--change-output", type=Path, required=True)
    normalize_parser.add_argument("--source-policy", type=Path, default=common.DEFAULT_SOURCE_POLICY)
    normalize_parser.add_argument("--retailer-aliases", type=Path, default=common.DEFAULT_ALIAS_REGISTRY)
    normalize_parser.add_argument("--previous-evidence", type=Path)
    return parser


def main() -> None:
    args = _parser().parse_args()
    policy = common.load_source_policy(args.source_policy)
    if args.command == "acquire":
        metadata = acquire(
            output=args.output,
            metadata_output=args.metadata_output,
            snapshot_id=args.snapshot_id,
            mode=args.mode,
            policy=policy,
            sample_records=args.sample_records,
            max_compressed_bytes=args.max_compressed_bytes,
            max_expanded_bytes=args.max_expanded_bytes,
            max_records=args.max_records,
            retrieved_at=args.retrieved_at,
        )
        print(json.dumps(metadata, sort_keys=True, separators=(",", ":")))
        return

    aliases = common.load_alias_registry(args.retailer_aliases)
    evidence, quality, changes = normalize_snapshot(
        snapshot=args.snapshot,
        metadata_path=args.metadata,
        policy=policy,
        aliases=aliases,
        previous_evidence_path=args.previous_evidence,
    )
    metadata = common.load_json(args.metadata)
    malformed = metadata.get("malformedRecords", {})
    malformed_count = sum(value for value in malformed.values() if isinstance(value, int) and not isinstance(value, bool)) if isinstance(malformed, dict) else 0
    emitted = metadata.get("recordsEmitted", 0)
    emitted_count = emitted if isinstance(emitted, int) and not isinstance(emitted, bool) else 0
    examined = emitted_count + malformed_count
    changes["parserQuality"] = {
        "recordsExamined": examined,
        "recordsEmitted": emitted_count,
        "malformedRecords": malformed_count,
        "malformedRate": (malformed_count / examined) if examined else 0.0,
        "schemaErrors": 0,
    }
    _write_json(args.evidence_output, evidence)
    _write_json(args.quality_output, quality)
    _write_json(args.change_output, changes)
    print(json.dumps({
        "sourceKey": common.SOURCE_KEY,
        "snapshotID": changes["snapshotID"],
        "retailerObservations": len(evidence["retailerEvidence"]),
        "reviewQueue": len(changes["reviewQueue"]),
        "observationalOnly": True,
    }, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
