#!/usr/bin/env python3
"""Compose independently reviewed retailer observations into production evidence.

The production product-selection snapshot remains authoritative for identities,
formulations and assessments.  Retailer evidence is observational-only and is
attached only when its exact (GTIN, market) tuple already exists in the primary
snapshot.  Both component quality reports must independently pass before a merged
quality report is emitted.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable


class AggregateError(ValueError):
    """Raised when independently reviewed component evidence cannot be composed."""


OBSERVATIONAL_EMPTY_COLLECTIONS = (
    "assessments",
    "certifications",
    "currentSelections",
    "identities",
    "ingredients",
    "packageEvidence",
    "releases",
    "remoteImages",
    "reviews",
    "validityEvents",
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AggregateError(f"failed to read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise AggregateError(f"{label} must be a JSON object")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _evidence_model():
    import evidence_model

    return evidence_model


def _source(
    envelope: dict[str, Any], source_key: str, snapshot_id: str, label: str
) -> dict[str, Any]:
    matches = [
        item
        for item in envelope.get("sources", [])
        if isinstance(item, dict) and item.get("sourceKey") == source_key
    ]
    if len(matches) != 1:
        raise AggregateError(f"{label} must contain exactly one {source_key!r} source record")
    source = matches[0]
    if source.get("sourceSnapshotID") != snapshot_id:
        raise AggregateError(f"{label} source snapshot differs from expected {snapshot_id!r}")
    return source


def _require_observational_retailer_envelope(envelope: dict[str, Any]) -> None:
    for collection in OBSERVATIONAL_EMPTY_COLLECTIONS:
        value = envelope.get(collection)
        if value != []:
            raise AggregateError(
                f"retailer component must remain observational-only; {collection} must be empty"
            )
    if not isinstance(envelope.get("retailerEvidence"), list):
        raise AggregateError("retailer component retailerEvidence must be an array")


def merge_evidence(
    *,
    primary: dict[str, Any],
    retailer: dict[str, Any],
    primary_source_key: str,
    primary_snapshot_id: str,
    retailer_source_key: str,
    retailer_snapshot_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    model = _evidence_model()
    model.validate_envelope(primary)
    model.validate_envelope(retailer)
    _source(primary, primary_source_key, primary_snapshot_id, "primary evidence")
    retailer_source = _source(retailer, retailer_source_key, retailer_snapshot_id, "retailer evidence")
    _require_observational_retailer_envelope(retailer)

    merged = copy.deepcopy(primary)
    sources = {
        item["sourceKey"]: item
        for item in merged.get("sources", [])
        if isinstance(item, dict) and isinstance(item.get("sourceKey"), str)
    }
    existing_retailer_source = sources.get(retailer_source_key)
    if existing_retailer_source is not None and existing_retailer_source != retailer_source:
        raise AggregateError("retailer source identity conflicts with primary evidence")
    sources[retailer_source_key] = copy.deepcopy(retailer_source)
    merged["sources"] = [sources[key] for key in sorted(sources)]

    selections = [item for item in merged.get("currentSelections", []) if isinstance(item, dict)]
    selection_keys = {(str(item.get("gtin")), str(item.get("market"))) for item in selections}
    primary_retailer = {
        item["id"]: item
        for item in merged.get("retailerEvidence", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    matched_by_key: dict[tuple[str, str], list[str]] = {}
    matched_ids: list[str] = []
    unmatched_ids: list[str] = []
    for record in retailer.get("retailerEvidence", []):
        key = (str(record.get("gtin")), str(record.get("market")))
        record_id = record.get("id")
        if not isinstance(record_id, str):
            raise AggregateError("retailer evidence record is missing an immutable id")
        if key not in selection_keys:
            unmatched_ids.append(record_id)
            continue
        existing = primary_retailer.get(record_id)
        if existing is not None and existing != record:
            raise AggregateError(f"retailer evidence id collision for {record_id}")
        primary_retailer[record_id] = copy.deepcopy(record)
        matched_by_key.setdefault(key, []).append(record_id)
        matched_ids.append(record_id)

    updated_selections: list[dict[str, Any]] = []
    for selection in selections:
        key = (str(selection.get("gtin")), str(selection.get("market")))
        current_ids = selection.get("retailerEvidenceIDs", [])
        if not isinstance(current_ids, list) or any(not isinstance(item, str) for item in current_ids):
            raise AggregateError("primary selection retailerEvidenceIDs must be an array of ids")
        updated = copy.deepcopy(selection)
        updated["retailerEvidenceIDs"] = sorted(set(current_ids) | set(matched_by_key.get(key, [])))
        updated["id"] = model.derive_id("currentSelections", updated)
        updated_selections.append(updated)

    merged["retailerEvidence"] = [primary_retailer[key] for key in sorted(primary_retailer)]
    merged["currentSelections"] = sorted(
        updated_selections,
        key=lambda item: (str(item.get("gtin", "")), str(item.get("market", ""))),
    )
    model.validate_envelope(merged)
    summary = {
        "schemaVersion": 1,
        "primarySourceKey": primary_source_key,
        "primarySnapshotID": primary_snapshot_id,
        "retailerSourceKey": retailer_source_key,
        "retailerSnapshotID": retailer_snapshot_id,
        "availableRetailerEvidence": len(retailer.get("retailerEvidence", [])),
        "matchedRetailerEvidence": len(set(matched_ids)),
        "unmatchedRetailerEvidence": len(set(unmatched_ids)),
        "matchedProductSelections": len(matched_by_key),
        "retailerEvidenceAttachedOnlyToExistingSelections": True,
        "retailerEvidenceCanChangeAssessment": False,
    }
    return merged, summary


def _verify_report_digest(report: dict[str, Any], label: str) -> None:
    digest = report.get("reportSha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise AggregateError(f"{label} has no valid reportSha256")
    subject = copy.deepcopy(report)
    subject.pop("reportSha256", None)
    actual = hashlib.sha256(_canonical_json(subject).encode("utf-8")).hexdigest()
    if actual != digest:
        raise AggregateError(f"{label} reportSha256 mismatch")


def _verify_component_quality(
    report: dict[str, Any], *, source_key: str, snapshot_id: str, policy_version: str, label: str
) -> None:
    _verify_report_digest(report, label)
    if report.get("schemaVersion") != 1:
        raise AggregateError(f"{label} schemaVersion is unsupported")
    if report.get("sourceKey") != source_key or report.get("snapshotID") != snapshot_id:
        raise AggregateError(f"{label} lineage differs from its reviewed evidence")
    if report.get("policyVersion") != policy_version:
        raise AggregateError(f"{label} quality policy version differs from aggregation policy")
    if report.get("status") != "pass" or report.get("releaseBlockingFindings") != []:
        raise AggregateError(f"{label} is not independently release-passing")
    if report.get("quarantineRequired") is True or report.get("rollbackRequired") is True:
        raise AggregateError(f"{label} requires quarantine or rollback")
    rights = report.get("sourceRights")
    if not isinstance(rights, dict) or rights.get("approved") is not True or rights.get("fixtureOnly") is True:
        raise AggregateError(f"{label} source rights are not approved for production")
    if rights.get("attributionPresent") is not True:
        raise AggregateError(f"{label} source attribution is missing")
    terms = rights.get("termsReview")
    if not isinstance(terms, dict) or terms.get("state") != "approved":
        raise AggregateError(f"{label} source terms review is not approved")


def _component_descriptor(report: dict[str, Any], path: Path) -> dict[str, Any]:
    rights = report["sourceRights"]
    return {
        "sourceKey": report["sourceKey"],
        "snapshotID": report["snapshotID"],
        "evaluatedAt": report["evaluatedAt"],
        "reportSha256": report["reportSha256"],
        "reportFileSha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "warningCount": len(report.get("warnings", [])),
        "licenseIdentifier": rights.get("licenseIdentifier"),
        "attributionPresent": rights.get("attributionPresent") is True,
        "termsReview": copy.deepcopy(rights.get("termsReview")),
    }


def merge_quality(
    *,
    base_report: dict[str, Any],
    primary_report: dict[str, Any],
    primary_report_path: Path,
    retailer_report: dict[str, Any],
    retailer_report_path: Path,
    merged_evidence: dict[str, Any],
    retailer_evidence: dict[str, Any],
    quality_policy: dict[str, Any],
    primary_source_key: str,
    primary_snapshot_id: str,
    retailer_source_key: str,
    retailer_snapshot_id: str,
) -> dict[str, Any]:
    policy_version = quality_policy.get("policyVersion")
    if not isinstance(policy_version, str) or not policy_version:
        raise AggregateError("quality policy has no policyVersion")
    _verify_component_quality(
        base_report,
        source_key=primary_source_key,
        snapshot_id=primary_snapshot_id,
        policy_version=policy_version,
        label="aggregate base quality report",
    )
    _verify_component_quality(
        primary_report,
        source_key=primary_source_key,
        snapshot_id=primary_snapshot_id,
        policy_version=policy_version,
        label="primary quality report",
    )
    _verify_component_quality(
        retailer_report,
        source_key=retailer_source_key,
        snapshot_id=retailer_snapshot_id,
        policy_version=policy_version,
        label="retailer quality report",
    )
    _evidence_model().validate_envelope(merged_evidence)
    _evidence_model().validate_envelope(retailer_evidence)
    _source(merged_evidence, primary_source_key, primary_snapshot_id, "merged evidence")
    _source(merged_evidence, retailer_source_key, retailer_snapshot_id, "merged evidence")
    _require_observational_retailer_envelope(retailer_evidence)

    if base_report.get("metrics", {}).get("products") != len(merged_evidence.get("currentSelections", [])):
        raise AggregateError("aggregate base quality product count differs from merged evidence")
    primary_products = primary_report.get("metrics", {}).get("products")
    if primary_products != len(merged_evidence.get("currentSelections", [])):
        raise AggregateError("primary quality product count differs from merged evidence")
    retailer_metrics = retailer_report.get("metrics", {})
    expected_retailer_count = len(retailer_evidence.get("retailerEvidence", []))
    reported_retailer_count = sum(
        int(value)
        for value in retailer_metrics.get("retailerEvidenceByKind", {}).values()
        if isinstance(value, int) and not isinstance(value, bool)
    )
    if reported_retailer_count != expected_retailer_count:
        raise AggregateError("retailer quality metrics do not bind the exact retailer evidence")

    available_ids = {
        item["id"]
        for item in retailer_evidence.get("retailerEvidence", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    merged_ids = {
        item["id"]
        for item in merged_evidence.get("retailerEvidence", [])
        if isinstance(item, dict)
        and item.get("sourceKey") == retailer_source_key
        and isinstance(item.get("id"), str)
    }
    expected_matched = len(available_ids & merged_ids)
    reported_merged_count = sum(
        int(value)
        for value in base_report.get("metrics", {}).get("retailerEvidenceByKind", {}).values()
        if isinstance(value, int) and not isinstance(value, bool)
    )
    preexisting_other_retailer = len([
        item for item in merged_evidence.get("retailerEvidence", [])
        if isinstance(item, dict) and item.get("sourceKey") != retailer_source_key
    ])
    if reported_merged_count != expected_matched + preexisting_other_retailer:
        raise AggregateError("aggregate base quality retailer metrics do not match merged evidence")

    merged = copy.deepcopy(base_report)
    merged.pop("reportSha256", None)
    components = sorted(
        [
            _component_descriptor(primary_report, primary_report_path),
            _component_descriptor(retailer_report, retailer_report_path),
        ],
        key=lambda item: item["sourceKey"],
    )
    merged["componentQualityReports"] = components
    merged["aggregation"] = {
        "schemaVersion": 1,
        "primarySourceKey": primary_source_key,
        "primarySnapshotID": primary_snapshot_id,
        "retailerSourceKey": retailer_source_key,
        "retailerSnapshotID": retailer_snapshot_id,
        "availableRetailerEvidence": len(available_ids),
        "matchedRetailerEvidence": expected_matched,
        "unmatchedRetailerEvidence": len(available_ids - merged_ids),
        "retailerEvidenceAttachedOnlyToExistingSelections": True,
        "retailerEvidenceCanChangeAssessment": False,
    }
    component_warnings = []
    for report in (primary_report, retailer_report):
        for warning in report.get("warnings", []):
            component_warnings.append(
                {"sourceKey": report["sourceKey"], "warning": copy.deepcopy(warning)}
            )
    merged["componentWarnings"] = component_warnings
    terms_policy_versions = {
        item["termsReview"].get("policyVersion")
        for item in components
        if isinstance(item.get("termsReview"), dict)
    }
    merged["sourceRights"] = {
        "approved": True,
        "fixtureOnly": False,
        "licenseIdentifier": "multiple-reviewed-sources",
        "attributionPresent": all(item["attributionPresent"] for item in components),
        "termsReview": {
            "state": "approved",
            "sourceKey": "aggregate",
            "policyVersion": next(iter(terms_policy_versions)) if len(terms_policy_versions) == 1 else "multiple",
            "components": [copy.deepcopy(item["termsReview"]) for item in components],
        },
        "components": [
            {
                "sourceKey": item["sourceKey"],
                "snapshotID": item["snapshotID"],
                "licenseIdentifier": item["licenseIdentifier"],
                "attributionPresent": item["attributionPresent"],
            }
            for item in components
        ],
    }
    merged["reportSha256"] = hashlib.sha256(
        _canonical_json(merged).encode("utf-8")
    ).hexdigest()
    return merged


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    evidence = sub.add_parser("merge-evidence")
    evidence.add_argument("--primary-evidence", type=Path, required=True)
    evidence.add_argument("--retailer-evidence", type=Path, required=True)
    evidence.add_argument("--primary-source-key", required=True)
    evidence.add_argument("--primary-snapshot-id", required=True)
    evidence.add_argument("--retailer-source-key", required=True)
    evidence.add_argument("--retailer-snapshot-id", required=True)
    evidence.add_argument("--output", type=Path, required=True)
    evidence.add_argument("--summary-output", type=Path, required=True)

    quality = sub.add_parser("merge-quality")
    quality.add_argument("--base-quality", type=Path, required=True)
    quality.add_argument("--primary-quality", type=Path, required=True)
    quality.add_argument("--retailer-quality", type=Path, required=True)
    quality.add_argument("--merged-evidence", type=Path, required=True)
    quality.add_argument("--retailer-evidence", type=Path, required=True)
    quality.add_argument("--quality-policy", type=Path, required=True)
    quality.add_argument("--primary-source-key", required=True)
    quality.add_argument("--primary-snapshot-id", required=True)
    quality.add_argument("--retailer-source-key", required=True)
    quality.add_argument("--retailer-snapshot-id", required=True)
    quality.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "merge-evidence":
            merged, summary = merge_evidence(
                primary=_load_object(args.primary_evidence, "primary evidence"),
                retailer=_load_object(args.retailer_evidence, "retailer evidence"),
                primary_source_key=args.primary_source_key,
                primary_snapshot_id=args.primary_snapshot_id,
                retailer_source_key=args.retailer_source_key,
                retailer_snapshot_id=args.retailer_snapshot_id,
            )
            _write_json(args.output, merged)
            _write_json(args.summary_output, summary)
            print(json.dumps(summary, sort_keys=True))
        else:
            base = _load_object(args.base_quality, "aggregate base quality report")
            primary = _load_object(args.primary_quality, "primary quality report")
            retailer = _load_object(args.retailer_quality, "retailer quality report")
            merged = merge_quality(
                base_report=base,
                primary_report=primary,
                primary_report_path=args.primary_quality,
                retailer_report=retailer,
                retailer_report_path=args.retailer_quality,
                merged_evidence=_load_object(args.merged_evidence, "merged evidence"),
                retailer_evidence=_load_object(args.retailer_evidence, "retailer evidence"),
                quality_policy=_load_object(args.quality_policy, "quality policy"),
                primary_source_key=args.primary_source_key,
                primary_snapshot_id=args.primary_snapshot_id,
                retailer_source_key=args.retailer_source_key,
                retailer_snapshot_id=args.retailer_snapshot_id,
            )
            _write_json(args.output, merged)
            print(merged["reportSha256"])
    except AggregateError as exc:
        raise SystemExit(f"production catalog aggregation failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
