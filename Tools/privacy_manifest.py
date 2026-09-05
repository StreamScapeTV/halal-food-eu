#!/usr/bin/env python3
"""Validate the shipping iOS privacy manifest against direct required-reason API use."""

from __future__ import annotations

import argparse
import json
import plistlib
import re
from pathlib import Path
from typing import Any

MANIFEST_PATH = Path("HalalFoodEU/Resources/PrivacyInfo.xcprivacy")
SOURCE_ROOT = Path("HalalFoodEU")
PROJECT_PATH = Path("project.yml")

APPROVED_DECLARATIONS: dict[str, tuple[str, ...]] = {
    "NSPrivacyAccessedAPICategoryUserDefaults": ("CA92.1",),
}

REQUIRED_REASON_PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "NSPrivacyAccessedAPICategoryUserDefaults": (
        re.compile(r"\bUserDefaults\b"),
    ),
    "NSPrivacyAccessedAPICategoryFileTimestamp": (
        re.compile(
            r"\.(?:creationDate|modificationDate|fileModificationDate|"
            r"contentModificationDateKey|creationDateKey)\b"
        ),
        re.compile(
            r"\b(?:getattrlist|getattrlistbulk|fgetattrlist|fstat|fstatat|lstat|"
            r"getattrlistat|stat)\s*\("
        ),
    ),
    "NSPrivacyAccessedAPICategorySystemBootTime": (
        re.compile(r"\.systemUptime\b"),
        re.compile(r"\bmach_absolute_time\s*\("),
    ),
    "NSPrivacyAccessedAPICategoryDiskSpace": (
        re.compile(
            r"\.(?:volumeAvailableCapacityKey|volumeAvailableCapacityForImportantUsageKey|"
            r"volumeAvailableCapacityForOpportunisticUsageKey|volumeTotalCapacityKey|"
            r"systemFreeSize|systemSize)\b"
        ),
        re.compile(r"\b(?:statfs|statvfs|fstatfs|fstatvfs)\s*\("),
    ),
    "NSPrivacyAccessedAPICategoryActiveKeyboards": (
        re.compile(r"\.activeInputModes\b"),
    ),
}

PROJECT_SOURCE_BINDING = re.compile(
    r"(?m)^\s*-\s+path:\s*[\"']?HalalFoodEU[\"']?\s*$"
)


class PrivacyManifestError(ValueError):
    """Raised when source use and privacy-manifest declarations disagree."""


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise PrivacyManifestError(f"missing privacy manifest: {path}") from exc
    if not raw or len(raw) > 256 * 1024:
        raise PrivacyManifestError("privacy manifest is empty or exceeds the bounded size")
    try:
        value = plistlib.loads(raw)
    except Exception as exc:
        raise PrivacyManifestError("privacy manifest is not a valid property list") from exc
    if not isinstance(value, dict):
        raise PrivacyManifestError("privacy manifest root must be a dictionary")
    return value


def scan_required_reason_apis(source_root: Path) -> dict[str, list[Path]]:
    if not source_root.is_dir():
        raise PrivacyManifestError(f"shipping source root is missing: {source_root}")

    observed: dict[str, list[Path]] = {}
    for path in sorted(source_root.rglob("*.swift")):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise PrivacyManifestError(f"Swift source is not strict UTF-8: {path}") from exc
        for category, patterns in REQUIRED_REASON_PATTERNS.items():
            if any(pattern.search(text) for pattern in patterns):
                observed.setdefault(category, []).append(path)
    return observed


def _manifest_declarations(manifest: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    expected_root_keys = {
        "NSPrivacyTracking",
        "NSPrivacyTrackingDomains",
        "NSPrivacyCollectedDataTypes",
        "NSPrivacyAccessedAPITypes",
    }
    if set(manifest) != expected_root_keys:
        raise PrivacyManifestError("privacy manifest root keys do not match the reviewed contract")
    if manifest["NSPrivacyTracking"] is not False:
        raise PrivacyManifestError("tracking must remain disabled")
    if manifest["NSPrivacyTrackingDomains"] != []:
        raise PrivacyManifestError("tracking domains must remain empty")
    if manifest["NSPrivacyCollectedDataTypes"] != []:
        raise PrivacyManifestError(
            "collected-data declarations changed outside the reviewed privacy contract"
        )

    entries = manifest["NSPrivacyAccessedAPITypes"]
    if not isinstance(entries, list):
        raise PrivacyManifestError("NSPrivacyAccessedAPITypes must be an array")

    declarations: dict[str, tuple[str, ...]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "NSPrivacyAccessedAPIType",
            "NSPrivacyAccessedAPITypeReasons",
        }:
            raise PrivacyManifestError("required-reason entry has unexpected shape")
        category = entry["NSPrivacyAccessedAPIType"]
        reasons = entry["NSPrivacyAccessedAPITypeReasons"]
        if not isinstance(category, str) or not category:
            raise PrivacyManifestError("required-reason category must be nonempty text")
        if category in declarations:
            raise PrivacyManifestError(f"duplicate required-reason category: {category}")
        if (
            not isinstance(reasons, list)
            or not reasons
            or any(not isinstance(reason, str) or not reason for reason in reasons)
            or len(set(reasons)) != len(reasons)
        ):
            raise PrivacyManifestError(
                f"required-reason category {category} must contain unique nonempty reasons"
            )
        declarations[category] = tuple(reasons)
    return declarations


def validate(root: Path) -> dict[str, Any]:
    root = root.resolve()
    manifest = _read_manifest(root / MANIFEST_PATH)
    declarations = _manifest_declarations(manifest)
    observed = scan_required_reason_apis(root / SOURCE_ROOT)

    observed_categories = set(observed)
    declared_categories = set(declarations)
    unreviewed = observed_categories - set(APPROVED_DECLARATIONS)
    if unreviewed:
        raise PrivacyManifestError(
            "shipping source uses required-reason categories without a reviewed mapping: "
            + ", ".join(sorted(unreviewed))
        )
    missing = observed_categories - declared_categories
    if missing:
        raise PrivacyManifestError(
            "privacy manifest is missing required-reason categories used by shipping source: "
            + ", ".join(sorted(missing))
        )
    unused = declared_categories - observed_categories
    if unused:
        raise PrivacyManifestError(
            "privacy manifest declares required-reason categories not used by shipping source: "
            + ", ".join(sorted(unused))
        )

    for category, reasons in declarations.items():
        approved = APPROVED_DECLARATIONS.get(category)
        if approved is None or reasons != approved:
            raise PrivacyManifestError(
                f"{category} must use the exact reviewed reason set "
                f"{list(approved) if approved is not None else '[]'}"
            )

    project_path = root / PROJECT_PATH
    try:
        project = project_path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError) as exc:
        raise PrivacyManifestError("project.yml is missing or not strict UTF-8") from exc
    if not PROJECT_SOURCE_BINDING.search(project):
        raise PrivacyManifestError(
            "project.yml must include HalalFoodEU so PrivacyInfo.xcprivacy is packaged"
        )

    return {
        "manifest": MANIFEST_PATH.as_posix(),
        "tracking": False,
        "collectedDataTypes": 0,
        "requiredReasonAPIs": [
            {
                "category": category,
                "reasons": list(declarations[category]),
                "sourceFiles": [
                    path.relative_to(root).as_posix() for path in observed[category]
                ],
            }
            for category in sorted(declarations)
        ],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = validate(args.root)
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
