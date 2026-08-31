#!/usr/bin/env python3
"""Project catalog selection decisions into the bounded basic-exclusion runtime index.

The selection engine's full report is useful for audit, but the production compiler
needs only the policy-bound GTIN/market exclusions. This module creates the compact
payload that is digest-bound as its own workflow handoff before build admission.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
SELECTION_REPORT_SCHEMA_VERSION = 1
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
REASON_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


class BasicExclusionsError(ValueError):
    """Raised when a selection/basic-exclusion payload fails closed."""


def _load(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BasicExclusionsError(f"{label} cannot be read as strict UTF-8 JSON") from exc
    if not isinstance(raw, dict):
        raise BasicExclusionsError(f"{label} must be a JSON object")
    return raw


def _policy_version(value: Any, label: str) -> str:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise BasicExclusionsError(f"{label} is invalid")
    return value


def empty_payload(policy_version: str) -> dict[str, Any]:
    return {
        "schemaVersion": SCHEMA_VERSION,
        "selectionPolicyVersion": _policy_version(policy_version, "selection policy version"),
        "records": [],
    }


def project_selection_report(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise BasicExclusionsError("selection report must be a JSON object")
    required = {
        "schemaVersion",
        "policyVersion",
        "selected",
        "basicExclusions",
        "invalidExclusions",
        "report",
    }
    optional = {"comparison"}
    missing = sorted(required - set(raw))
    extra = sorted(set(raw) - required - optional)
    if missing:
        raise BasicExclusionsError(f"selection report missing required keys: {', '.join(missing)}")
    if extra:
        raise BasicExclusionsError(f"selection report has unexpected keys: {', '.join(extra)}")
    if raw["schemaVersion"] != SELECTION_REPORT_SCHEMA_VERSION:
        raise BasicExclusionsError(
            f"unsupported selection report schemaVersion {raw['schemaVersion']!r}"
        )
    policy_version = _policy_version(raw["policyVersion"], "selection report policyVersion")
    source_records = raw["basicExclusions"]
    if not isinstance(source_records, list):
        raise BasicExclusionsError("selection report basicExclusions must be an array")

    records: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, value in enumerate(source_records):
        if not isinstance(value, dict):
            raise BasicExclusionsError(f"basicExclusions[{index}] must be an object")
        if set(value) != {"gtin", "market", "policyVersion", "reasonCode"}:
            raise BasicExclusionsError(
                f"basicExclusions[{index}] must contain only gtin/market/policyVersion/reasonCode"
            )
        gtin = value["gtin"]
        market = value["market"]
        record_policy = value["policyVersion"]
        reason = value["reasonCode"]
        if not isinstance(gtin, str) or len(gtin) != 14 or not gtin.isdigit():
            raise BasicExclusionsError(f"basicExclusions[{index}].gtin is invalid")
        if not isinstance(market, str) or len(market) != 2 or market.upper() != market:
            raise BasicExclusionsError(f"basicExclusions[{index}].market is invalid")
        if record_policy != policy_version:
            raise BasicExclusionsError(
                f"basicExclusions[{index}].policyVersion differs from selection report"
            )
        if not isinstance(reason, str) or not REASON_RE.fullmatch(reason):
            raise BasicExclusionsError(f"basicExclusions[{index}].reasonCode is invalid")
        key = (gtin, market)
        if key in seen:
            raise BasicExclusionsError(f"duplicate basic exclusion {gtin}/{market}")
        seen.add(key)
        records.append({"gtin": gtin, "market": market, "reason": reason})

    return {
        "schemaVersion": SCHEMA_VERSION,
        "selectionPolicyVersion": policy_version,
        "records": sorted(records, key=lambda item: (item["gtin"], item["market"])),
    }


def write_payload(payload: dict[str, Any], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    project = subparsers.add_parser("project")
    project.add_argument("--selection-report", type=Path, required=True)
    project.add_argument("--output", type=Path, required=True)

    empty = subparsers.add_parser("empty")
    empty.add_argument("--policy-version", required=True)
    empty.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "project":
            payload = project_selection_report(_load(args.selection_report, "selection report"))
        else:
            payload = empty_payload(args.policy_version)
        write_payload(payload, args.output)
        print(json.dumps({"records": len(payload["records"]), "selectionPolicyVersion": payload["selectionPolicyVersion"]}, sort_keys=True))
    except BasicExclusionsError as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
