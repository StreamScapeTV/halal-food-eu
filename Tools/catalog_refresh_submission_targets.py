#!/usr/bin/env python3
"""Derive privacy-safe refresh targets from owner-admitted product submissions.

Only committed admitted proposal files are read. Raw mail packages, photos, local
scan history, and user identifiers are outside this boundary by design.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

GTIN = re.compile(r"^[0-9]{14}$")
MARKET = re.compile(r"^[A-Z]{2}$")


class SubmissionTargetError(ValueError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SubmissionTargetError(f"failed to read admitted submission {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SubmissionTargetError(f"admitted submission {path} must contain an object")
    return value


def derive(directory: Path) -> list[dict[str, Any]]:
    if not directory.exists():
        return []
    if not directory.is_dir():
        raise SubmissionTargetError("admitted submissions path must be a directory")
    targets: dict[tuple[str, str], dict[str, Any]] = {}
    for path in sorted(directory.glob("*.json")):
        proposal = load(path)
        if proposal.get("schemaVersion") != 1:
            raise SubmissionTargetError(f"{path.name} has unsupported schemaVersion")
        submission_id = proposal.get("submissionID")
        if not isinstance(submission_id, str) or not submission_id.strip():
            raise SubmissionTargetError(f"{path.name} lacks submissionID")
        output = proposal.get("outputEvidence")
        if not isinstance(output, dict):
            raise SubmissionTargetError(f"{path.name} lacks outputEvidence")
        observations: list[dict[str, Any]] = []
        for collection in ("identities", "ingredients", "certifications"):
            values = output.get(collection, [])
            if not isinstance(values, list):
                raise SubmissionTargetError(f"{path.name} outputEvidence.{collection} must be an array")
            observations.extend(item for item in values if isinstance(item, dict))
        for item in observations:
            gtin = item.get("gtin")
            market = item.get("market")
            if not isinstance(gtin, str) or not GTIN.fullmatch(gtin):
                continue
            if not isinstance(market, str) or not MARKET.fullmatch(market):
                continue
            key = (market, gtin)
            targets[key] = {
                "key": f"admitted-submission:{market}:{gtin}:-",
                "reason": "admitted-submission",
                "priority": "high",
                "gtin": gtin,
                "market": market,
                "evidenceID": None,
                "detail": "Owner-admitted non-personal product evidence requests source refresh/reconciliation.",
            }
    return [targets[key] for key in sorted(targets)]


def merge_queue(queue: dict[str, Any], extra_entries: list[dict[str, Any]], max_entries: int) -> dict[str, Any]:
    entries = queue.get("entries")
    if not isinstance(entries, list):
        raise SubmissionTargetError("refresh queue entries must be an array")
    if not isinstance(max_entries, int) or isinstance(max_entries, bool) or max_entries < 1:
        raise SubmissionTargetError("maxEntries must be positive")
    merged: dict[str, dict[str, Any]] = {}
    for item in [*entries, *extra_entries]:
        if not isinstance(item, dict):
            raise SubmissionTargetError("refresh queue entry must be an object")
        key = item.get("key")
        if not isinstance(key, str) or not key:
            raise SubmissionTargetError("refresh queue entry key is invalid")
        merged[key] = item
    priority = {"high": 0, "medium": 1, "low": 2}
    ordered = sorted(
        merged.values(),
        key=lambda item: (
            priority.get(item.get("priority"), 3),
            str(item.get("reason") or ""),
            str(item.get("market") or ""),
            str(item.get("gtin") or ""),
            str(item.get("key") or ""),
        ),
    )[:max_entries]
    result = json.loads(json.dumps(queue))
    result["entries"] = ordered
    result["queueSha256"] = hashlib.sha256(canonical({k: v for k, v in result.items() if k != "queueSha256"})).hexdigest()
    return result


def write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    derive_parser = sub.add_parser("derive")
    derive_parser.add_argument("--admitted-directory", type=Path, default=Path("Data/submissions/admitted"))
    derive_parser.add_argument("--output", type=Path, required=True)
    merge_parser = sub.add_parser("merge")
    merge_parser.add_argument("--queue", type=Path, required=True)
    merge_parser.add_argument("--targets", type=Path, required=True)
    merge_parser.add_argument("--max-entries", type=int, required=True)
    merge_parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.command == "derive":
            entries = derive(args.admitted_directory)
            write(args.output, entries)
            print(f"Admitted submission refresh targets: {len(entries)}")
            return
        queue = load(args.queue)
        try:
            targets = json.loads(args.targets.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SubmissionTargetError(f"failed to read targets: {exc}") from exc
        if not isinstance(targets, list):
            raise SubmissionTargetError("targets must be an array")
        result = merge_queue(queue, targets, args.max_entries)
        write(args.output, result)
        print(f"Refresh queue after admitted submissions: {len(result['entries'])}")
    except SubmissionTargetError as exc:
        raise SystemExit(f"admitted submission refresh targeting failed: {exc}") from exc


if __name__ == "__main__":
    main()
