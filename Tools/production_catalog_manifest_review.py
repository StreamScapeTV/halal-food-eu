#!/usr/bin/env python3
"""Bind bounded release-review metadata into a production catalog manifest.

The production SQLite compiler already binds the exact passing quality report by
self-digest and file digest. This finalizer re-verifies that binding and copies only
bounded, review-oriented summaries into the manifest. It deliberately represents
missing comparison evidence as unavailable instead of manufacturing zero changes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


RELEASE_REVIEW_SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ManifestReviewError(ValueError):
    """Raised when release-review metadata cannot be safely bound to a manifest."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestReviewError(f"failed to read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManifestReviewError(f"{label} must be a JSON object")
    return value


def _nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ManifestReviewError(f"{label} must be a non-negative integer")
    return value


def _distribution(value: Any, label: str) -> dict[str, int]:
    if not isinstance(value, dict):
        raise ManifestReviewError(f"{label} must be an object")
    result: dict[str, int] = {}
    for key, count in value.items():
        if not isinstance(key, str) or not key.strip():
            raise ManifestReviewError(f"{label} has an invalid key")
        result[key] = _nonnegative_int(count, f"{label}.{key}")
    return dict(sorted(result.items()))


def _validate_quality_self_digest(quality: dict[str, Any]) -> str:
    expected = quality.get("reportSha256")
    if not isinstance(expected, str) or not SHA256_RE.fullmatch(expected):
        raise ManifestReviewError("quality report has no valid reportSha256")
    subject = dict(quality)
    subject.pop("reportSha256", None)
    actual = hashlib.sha256(canonical_json(subject).encode("utf-8")).hexdigest()
    if actual != expected:
        raise ManifestReviewError("quality report self-digest mismatch")
    return expected


def _validate_quality_binding(
    *,
    manifest: dict[str, Any],
    quality: dict[str, Any],
    quality_report_path: Path,
) -> dict[str, Any]:
    quality_gate = manifest.get("qualityGate")
    if not isinstance(quality_gate, dict) or quality_gate.get("schemaVersion") != 1:
        raise ManifestReviewError("production manifest qualityGate binding is missing")
    expected_file_digest = quality_gate.get("reportFileSha256")
    if not isinstance(expected_file_digest, str) or not SHA256_RE.fullmatch(expected_file_digest):
        raise ManifestReviewError("production manifest quality report file digest is invalid")
    if file_sha256(quality_report_path) != expected_file_digest:
        raise ManifestReviewError("quality report file digest does not match production manifest")

    report_digest = _validate_quality_self_digest(quality)
    if quality_gate.get("reportSha256") != report_digest:
        raise ManifestReviewError("quality report self-digest does not match production manifest")
    for quality_key, manifest_key in (
        ("policyVersion", "policyVersion"),
        ("sourceKey", "sourceKey"),
        ("snapshotID", "snapshotID"),
        ("evaluatedAt", "evaluatedAt"),
    ):
        if quality.get(quality_key) != quality_gate.get(manifest_key):
            raise ManifestReviewError(f"quality report {quality_key} does not match production manifest")
    if quality.get("status") != "pass":
        raise ManifestReviewError("release-review metadata requires a passing quality decision")
    if quality.get("releaseBlockingFindings") != []:
        raise ManifestReviewError("release-review metadata cannot bind release-blocking findings")
    if quality.get("quarantineRequired") is True or quality.get("rollbackRequired") is True:
        raise ManifestReviewError("release-review metadata cannot bind quarantine/rollback-required quality state")
    return quality_gate


def _change_comparison(changes: Any) -> dict[str, Any]:
    if not isinstance(changes, dict):
        raise ManifestReviewError("quality change summary is missing")
    available = changes.get("available")
    if not isinstance(available, bool):
        raise ManifestReviewError("quality change comparison availability is invalid")
    baseline = changes.get("baseline")
    baseline_available = available and isinstance(baseline, str) and bool(baseline.strip()) and baseline != "none"
    if not baseline_available:
        return {
            "available": False,
            "reason": "no accepted comparison baseline was supplied to catalog quality evaluation",
            "baseline": None,
            "additions": None,
            "removals": None,
            "formulationChanges": None,
            "statusChangeCount": None,
            "reviewQueueCount": None,
        }
    status_changes = changes.get("statusChanges")
    if not isinstance(status_changes, list):
        raise ManifestReviewError("quality statusChanges must be an array when comparison is available")
    return {
        "available": True,
        "reason": None,
        "baseline": baseline,
        "additions": _nonnegative_int(changes.get("additions"), "quality additions"),
        "removals": _nonnegative_int(changes.get("removals"), "quality removals"),
        "formulationChanges": _nonnegative_int(changes.get("formulationChanges"), "quality formulationChanges"),
        "statusChangeCount": len(status_changes),
        "reviewQueueCount": _nonnegative_int(changes.get("reviewQueueCount"), "quality reviewQueueCount"),
    }


def build_release_review(
    *,
    manifest: dict[str, Any],
    quality: dict[str, Any],
    quality_report_path: Path,
) -> dict[str, Any]:
    quality_gate = _validate_quality_binding(
        manifest=manifest,
        quality=quality,
        quality_report_path=quality_report_path,
    )
    metrics = quality.get("metrics")
    audit = quality.get("auditSample")
    rights = quality.get("sourceRights")
    if not isinstance(metrics, dict) or not isinstance(audit, dict) or not isinstance(rights, dict):
        raise ManifestReviewError("quality metrics, audit sample, or source-rights review is missing")

    formulation_freshness = _distribution(metrics.get("formulationFreshness"), "quality formulationFreshness")
    retailer_freshness = _distribution(metrics.get("retailerFreshness"), "quality retailerFreshness")
    assessment_status = _distribution(metrics.get("assessmentStatus"), "quality assessmentStatus")
    certification_state = _distribution(metrics.get("certificationState"), "quality certificationState")
    review_state = _distribution(metrics.get("reviewState"), "quality reviewState")
    comparison = _change_comparison(quality.get("changes"))

    mandatory_review_count = _nonnegative_int(audit.get("mandatoryReviewCount"), "quality mandatoryReviewCount")
    mandatory_review_truncated = audit.get("mandatoryReviewTruncated")
    if not isinstance(mandatory_review_truncated, bool):
        raise ManifestReviewError("quality mandatoryReviewTruncated must be a boolean")
    current_retailer_records = _nonnegative_int(metrics.get("retailerEvidenceRecords"), "quality retailerEvidenceRecords")
    current_certification_records = _nonnegative_int(metrics.get("certificationRecords"), "quality certificationRecords")
    validity_events = _nonnegative_int(metrics.get("assessmentValidityEvents"), "quality assessmentValidityEvents")

    current_license = rights.get("licenseIdentifier")
    if current_license is not None and (not isinstance(current_license, str) or not current_license.strip()):
        raise ManifestReviewError("quality source license identifier is invalid")
    attribution_present = rights.get("attributionPresent")
    if not isinstance(attribution_present, bool):
        raise ManifestReviewError("quality attribution-present state is invalid")
    terms_review = rights.get("termsReview")
    terms_review_state = None
    if terms_review is not None:
        if not isinstance(terms_review, dict):
            raise ManifestReviewError("quality source terms review is invalid")
        value = terms_review.get("state")
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ManifestReviewError("quality source terms review state is invalid")
        terms_review_state = value

    return {
        "schemaVersion": RELEASE_REVIEW_SCHEMA_VERSION,
        "qualityReport": {
            "reportSha256": quality_gate["reportSha256"],
            "fileSha256": quality_gate["reportFileSha256"],
            "evaluatedAt": quality_gate["evaluatedAt"],
        },
        "changeComparison": comparison,
        "retailerChangeComparison": {
            "available": False,
            "reason": "the current accepted change-report contract does not provide retailer-evidence deltas",
            "currentEvidenceRecords": current_retailer_records,
        },
        "certificationChangeComparison": {
            "available": False,
            "reason": "the current accepted change-report contract does not provide certification-evidence deltas",
            "currentEvidenceRecords": current_certification_records,
        },
        "freshnessDistributions": {
            "formulation": formulation_freshness,
            "retailer": retailer_freshness,
        },
        "qualityDistributions": {
            "assessmentStatus": assessment_status,
            "certificationState": certification_state,
            "reviewState": review_state,
        },
        "reviewQueue": {
            "changeReviewQueueCount": comparison["reviewQueueCount"],
            "mandatoryHighRiskReviewCount": mandatory_review_count,
            "mandatoryReviewListTruncated": mandatory_review_truncated,
        },
        "invalidations": {
            "assessmentValidityEvents": validity_events,
            "changedUnreviewedFormulations": formulation_freshness.get("changed-unreviewed", 0),
        },
        "sourceRightsReview": {
            "licenseIdentifier": current_license,
            "attributionPresent": attribution_present,
            "termsReviewState": terms_review_state,
        },
    }


def finalize_manifest_review(*, manifest_path: Path, quality_report_path: Path) -> dict[str, Any]:
    manifest = _load_object(manifest_path, "production catalog manifest")
    quality = _load_object(quality_report_path, "quality report")
    if "releaseReview" in manifest:
        raise ManifestReviewError("production manifest already contains releaseReview metadata")
    manifest["releaseReview"] = build_release_review(
        manifest=manifest,
        quality=quality,
        quality_report_path=quality_report_path,
    )
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--quality-report", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        finalize_manifest_review(
            manifest_path=args.manifest,
            quality_report_path=args.quality_report,
        )
    except ManifestReviewError as exc:
        raise SystemExit(f"production manifest release-review finalization failed: {exc}") from exc
    print(args.manifest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
