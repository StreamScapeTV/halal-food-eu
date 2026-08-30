#!/usr/bin/env python3
"""Finalize deterministic HF-PIPELINE-010 production catalog release notes.

The production compiler emits the base Markdown from immutable local inputs. This
module appends the accepted release-review summary from the exact manifest and
quality decision so the human-readable artifact cannot silently omit change,
freshness, schema/methodology, or source-rights state.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SECTION_HEADING = "## HF-PIPELINE-010 release summary"


def _nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReleaseNotesError(f"{label} must be a non-negative integer")
    return value


def _release_summary(*, manifest: dict[str, Any], quality: dict[str, Any], record_count: int) -> dict[str, Any]:
    schema_version = manifest.get("schemaVersion")
    methodology_version = manifest.get("methodologyVersion")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
        raise ReleaseNotesError("production manifest schemaVersion is invalid for release notes")
    if not isinstance(methodology_version, str) or not methodology_version.strip():
        raise ReleaseNotesError("production manifest methodologyVersion is missing for release notes")
    changes = quality.get("changes")
    metrics = quality.get("metrics")
    source_rights = quality.get("sourceRights")
    rights = manifest.get("rights")
    if not all(isinstance(value, dict) for value in (changes, metrics, source_rights, rights)):
        raise ReleaseNotesError("release-note review metrics are incomplete")
    available = changes.get("available")
    if not isinstance(available, bool):
        raise ReleaseNotesError("quality change comparison availability is invalid")
    status_changes = changes.get("statusChanges", [])
    freshness = metrics.get("formulationFreshness")
    if not isinstance(status_changes, list) or not isinstance(freshness, dict):
        raise ReleaseNotesError("quality change/freshness metrics are invalid")
    licenses = rights.get("licenses")
    attributions = rights.get("attributions")
    if (not isinstance(licenses, list) or not isinstance(attributions, list) or
        any(not isinstance(v, str) or not v.strip() for v in [*licenses, *attributions])):
        raise ReleaseNotesError("production manifest rights are invalid for release notes")
    current_license = source_rights.get("licenseIdentifier")
    attribution_present = source_rights.get("attributionPresent")
    if current_license is not None and (not isinstance(current_license, str) or not current_license.strip()):
        raise ReleaseNotesError("quality source license identifier is invalid")
    if not isinstance(attribution_present, bool):
        raise ReleaseNotesError("quality attribution-present state is invalid")
    return {
        "recordCount": record_count,
        "schemaVersion": schema_version,
        "methodologyVersion": methodology_version,
        "changeComparison": {
            "available": available,
            "baseline": changes.get("baseline"),
            "additions": _nonnegative_int(changes.get("additions", 0), "quality additions"),
            "formulationChanges": _nonnegative_int(changes.get("formulationChanges", 0), "quality formulationChanges"),
            "removals": _nonnegative_int(changes.get("removals", 0), "quality removals"),
            "statusChangeCount": len(status_changes),
            "reviewQueueCount": _nonnegative_int(changes.get("reviewQueueCount", 0), "quality reviewQueueCount"),
        },
        "staleRecords": _nonnegative_int(freshness.get("stale", 0), "quality stale formulation count"),
        "sourceLicenseChanges": {
            "comparisonAvailable": False,
            "reason": "previous accepted production source-rights baseline was not supplied to this build",
            "currentLicenses": sorted(set(licenses)),
            "currentAttributions": sorted(set(attributions)),
            "qualitySourceLicense": current_license,
            "attributionPresent": attribution_present,
        },
    }


class ReleaseNotesError(ValueError):
    """Raised when release notes cannot be finalized from reviewed artifacts."""


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReleaseNotesError(f"failed to read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseNotesError(f"{label} must be a JSON object")
    return value


def _display_change(value: int, available: bool) -> str:
    return f"{value:,}" if available else "unavailable (no accepted comparison baseline)"


def _markdown_list(values: list[str]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "none"


def finalize_release_notes(
    *,
    release_notes_path: Path,
    manifest: dict[str, Any],
    quality: dict[str, Any],
) -> str:
    try:
        current = release_notes_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ReleaseNotesError(f"compiler release notes are missing or unreadable: {exc}") from exc
    if not current.strip():
        raise ReleaseNotesError("compiler release notes are empty")
    catalog_version = manifest.get("catalogVersion")
    if not isinstance(catalog_version, str) or f"# Catalog {catalog_version}" not in current:
        raise ReleaseNotesError("compiler release notes do not match the production manifest catalogVersion")
    if SECTION_HEADING in current:
        raise ReleaseNotesError("compiler release notes already contain the formal release summary")
    if quality.get("status") != "pass":
        raise ReleaseNotesError("formal production release notes require a passing quality decision")

    record_count = manifest.get("recordCount")
    if not isinstance(record_count, int) or isinstance(record_count, bool) or record_count < 0:
        raise ReleaseNotesError("production manifest recordCount is invalid")
    summary = _release_summary(manifest=manifest, quality=quality, record_count=record_count)

    changes = summary["changeComparison"]
    available = changes["available"]
    rights = summary["sourceLicenseChanges"]
    status_count = changes["statusChangeCount"]
    baseline = changes.get("baseline")
    baseline_text = f"`{baseline}`" if available and baseline not in (None, "none") else "none / unavailable"

    lines = [
        "",
        SECTION_HEADING,
        "",
        f"- Record count: {summary['recordCount']:,}",
        f"- SQLite schema version: `{summary['schemaVersion']}`",
        f"- Methodology version: `{summary['methodologyVersion']}`",
        f"- Change comparison available: {'yes' if available else 'no'}",
        f"- Comparison baseline: {baseline_text}",
        f"- Additions: {_display_change(changes['additions'], available)}",
        f"- Formulation changes: {_display_change(changes['formulationChanges'], available)}",
        f"- Removals: {_display_change(changes['removals'], available)}",
        f"- Status changes: {_display_change(status_count, available)}",
        f"- Review queue: {_display_change(changes['reviewQueueCount'], available)}",
        f"- Stale formulation records: {summary['staleRecords']:,}",
        "",
        "## Source and license review",
        "",
        f"- Change comparison available: {'yes' if rights['comparisonAvailable'] else 'no'}",
        f"- Comparison note: {rights['reason']}",
        f"- Current licenses: {_markdown_list(rights['currentLicenses'])}",
        f"- Current attributions: {_markdown_list(rights['currentAttributions'])}",
        f"- Quality source license: `{rights['qualitySourceLicense']}`" if rights["qualitySourceLicense"] else "- Quality source license: unavailable",
        f"- Attribution present in reviewed source policy: {'yes' if rights['attributionPresent'] else 'no'}",
        "",
    ]
    finalized = current.rstrip() + "\n" + "\n".join(lines)
    release_notes_path.write_text(finalized, encoding="utf-8")
    return finalized


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release-notes", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--quality-report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        manifest = _load_object(args.manifest, "production catalog manifest")
        quality = _load_object(args.quality_report, "quality report")
        finalize_release_notes(
            release_notes_path=args.release_notes,
            manifest=manifest,
            quality=quality,
        )
    except ReleaseNotesError as exc:
        raise SystemExit(f"production release-note finalization failed: {exc}") from exc
    print(args.release_notes)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
