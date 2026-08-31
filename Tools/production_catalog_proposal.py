#!/usr/bin/env python3
"""Prepare a deterministic production catalog proposal report from immutable handoffs.

This adapter is deliberately local-file-only. GitHub Actions validates each handoff
against the repository workflow contract before invoking this tool; this tool then
re-verifies payload integrity and enforces cross-artifact lineage that is specific to
the production SQLite/manifest proposal boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

import production_catalog_logical

SAFE_KEY = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
SAFE_SNAPSHOT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?$")


class ProposalError(ValueError):
    pass


def load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProposalError(f"failed to read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProposalError(f"{label} must be a JSON object")
    return value


def _safe_payload(root: Path, handoff: dict[str, Any], expected_kind: str) -> tuple[Path, str, int]:
    if handoff.get("artifactKind") != expected_kind:
        raise ProposalError(f"expected {expected_kind} handoff")
    payload = handoff.get("payload")
    if not isinstance(payload, dict):
        raise ProposalError(f"{expected_kind} handoff payload is missing")
    relative = payload.get("relativePath")
    expected_sha = payload.get("sha256")
    expected_bytes = payload.get("byteCount")
    if not isinstance(relative, str) or not relative or relative.startswith(("/", "\\")) or "\\" in relative:
        raise ProposalError(f"{expected_kind} payload path is unsafe")
    parts = Path(relative).parts
    if any(part in ("", ".", "..") for part in parts):
        raise ProposalError(f"{expected_kind} payload path is unsafe")
    if not isinstance(expected_sha, str) or not SHA64.fullmatch(expected_sha):
        raise ProposalError(f"{expected_kind} payload SHA-256 is invalid")
    if not isinstance(expected_bytes, int) or isinstance(expected_bytes, bool) or expected_bytes < 0:
        raise ProposalError(f"{expected_kind} payload byte count is invalid")
    resolved_root = root.resolve()
    candidate = (resolved_root / relative).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ProposalError(f"{expected_kind} payload escapes its root") from exc
    if not candidate.is_file():
        raise ProposalError(f"{expected_kind} payload is missing")
    data = candidate.read_bytes()
    if len(data) != expected_bytes:
        raise ProposalError(f"{expected_kind} payload byte count mismatch")
    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != expected_sha:
        raise ProposalError(f"{expected_kind} payload SHA-256 mismatch")
    return candidate, actual_sha, len(data)


def _producer(handoff: dict[str, Any], label: str) -> tuple[str, str, str]:
    producer = handoff.get("producer")
    if not isinstance(producer, dict):
        raise ProposalError(f"{label} producer is missing")
    commit = producer.get("commitSha")
    workflow = producer.get("workflow")
    run_id = producer.get("runId")
    if not isinstance(commit, str) or not SHA40.fullmatch(commit):
        raise ProposalError(f"{label} producer commit is invalid")
    if not isinstance(workflow, str) or not workflow:
        raise ProposalError(f"{label} producer workflow is invalid")
    if not isinstance(run_id, str) or not run_id.isdigit():
        raise ProposalError(f"{label} producer run ID is invalid")
    return commit, workflow, run_id


def _nonnegative_int(value: Any, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProposalError(f"{label} must be a non-negative integer")
    return value


def _release_summary(*, manifest: dict[str, Any], quality: dict[str, Any], record_count: int) -> dict[str, Any]:
    schema_version = manifest.get("schemaVersion")
    if not isinstance(schema_version, int) or isinstance(schema_version, bool) or schema_version < 1:
        raise ProposalError("production manifest schemaVersion is invalid for release summary")
    methodology_version = manifest.get("methodologyVersion")
    if not isinstance(methodology_version, str) or not methodology_version.strip():
        raise ProposalError("production manifest methodologyVersion is missing for release summary")

    changes = quality.get("changes")
    metrics = quality.get("metrics")
    source_rights = quality.get("sourceRights")
    rights = manifest.get("rights")
    if not isinstance(changes, dict) or not isinstance(metrics, dict) or not isinstance(source_rights, dict):
        raise ProposalError("quality report release-review metrics are incomplete")
    if not isinstance(rights, dict):
        raise ProposalError("production manifest rights are missing for release summary")
    comparison_available = changes.get("available")
    if not isinstance(comparison_available, bool):
        raise ProposalError("quality change comparison availability is invalid")
    status_changes = changes.get("statusChanges", [])
    if not isinstance(status_changes, list):
        raise ProposalError("quality statusChanges must be an array")
    formulation_freshness = metrics.get("formulationFreshness")
    if not isinstance(formulation_freshness, dict):
        raise ProposalError("quality formulation freshness metrics are missing")

    licenses = rights.get("licenses")
    attributions = rights.get("attributions")
    if (
        not isinstance(licenses, list)
        or any(not isinstance(value, str) or not value.strip() for value in licenses)
        or not isinstance(attributions, list)
        or any(not isinstance(value, str) or not value.strip() for value in attributions)
    ):
        raise ProposalError("production manifest rights are invalid for release summary")
    current_license = source_rights.get("licenseIdentifier")
    attribution_present = source_rights.get("attributionPresent")
    if current_license is not None and (not isinstance(current_license, str) or not current_license.strip()):
        raise ProposalError("quality source license identifier is invalid")
    if not isinstance(attribution_present, bool):
        raise ProposalError("quality attribution-present state is invalid")

    return {
        "recordCount": record_count,
        "schemaVersion": schema_version,
        "methodologyVersion": methodology_version,
        "changeComparison": {
            "available": comparison_available,
            "baseline": changes.get("baseline"),
            "additions": _nonnegative_int(changes.get("additions", 0), "quality additions"),
            "formulationChanges": _nonnegative_int(changes.get("formulationChanges", 0), "quality formulationChanges"),
            "removals": _nonnegative_int(changes.get("removals", 0), "quality removals"),
            "statusChangeCount": len(status_changes),
            "reviewQueueCount": _nonnegative_int(changes.get("reviewQueueCount", 0), "quality reviewQueueCount"),
        },
        "staleRecords": _nonnegative_int(formulation_freshness.get("stale", 0), "quality stale formulation count"),
        "sourceLicenseChanges": {
            "comparisonAvailable": False,
            "reason": "previous accepted production source-rights baseline was not supplied to this proposal",
            "currentLicenses": sorted(set(licenses)),
            "currentAttributions": sorted(set(attributions)),
            "qualitySourceLicense": current_license,
            "attributionPresent": attribution_present,
        },
    }


def proposal_key(source_key: str, snapshot_id: str, catalog_digest: str) -> str:
    canonical = json.dumps(
        {"catalogDigest": catalog_digest, "snapshotId": snapshot_id, "sourceKey": source_key},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    return f"catalog-update/{source_key}-{digest[:16]}"


def prepare_report(
    *,
    source_key: str,
    snapshot_id: str,
    database_handoff_path: Path,
    database_root: Path,
    manifest_handoff_path: Path,
    manifest_root: Path,
    quality_handoff_path: Path,
    quality_root: Path,
) -> dict[str, Any]:
    if not SAFE_KEY.fullmatch(source_key) or source_key in {"aggregate", "synthetic-fixture"}:
        raise ProposalError("production proposal source key is invalid")
    if not SAFE_SNAPSHOT.fullmatch(snapshot_id):
        raise ProposalError("production proposal snapshot ID is invalid")

    database_handoff = load_object(database_handoff_path, "database handoff")
    manifest_handoff = load_object(manifest_handoff_path, "manifest handoff")
    quality_handoff = load_object(quality_handoff_path, "quality handoff")
    database_path, database_sha, _ = _safe_payload(database_root, database_handoff, "catalog-database")
    manifest_path, manifest_sha, _ = _safe_payload(manifest_root, manifest_handoff, "catalog-manifest")
    quality_path, quality_sha, _ = _safe_payload(quality_root, quality_handoff, "quality-report")

    if database_handoff.get("sourceKey") != "aggregate" or manifest_handoff.get("sourceKey") != "aggregate":
        raise ProposalError("catalog output handoffs must use aggregate source identity")
    if quality_handoff.get("sourceKey") != source_key:
        raise ProposalError("quality handoff source differs from production proposal source")
    for label, handoff in (("database", database_handoff), ("manifest", manifest_handoff), ("quality", quality_handoff)):
        if handoff.get("snapshotId") != snapshot_id:
            raise ProposalError(f"{label} handoff snapshot differs from production proposal snapshot")
        if handoff.get("completeness") != "complete":
            raise ProposalError(f"{label} handoff is not complete")

    database_producer = _producer(database_handoff, "database handoff")
    manifest_producer = _producer(manifest_handoff, "manifest handoff")
    quality_producer = _producer(quality_handoff, "quality handoff")
    if database_producer != manifest_producer:
        raise ProposalError("database and manifest were not produced by the same immutable build")
    if database_producer[1] != "build-catalog.yml":
        raise ProposalError("catalog output producer workflow is not build-catalog.yml")
    if quality_producer[0] != database_producer[0]:
        raise ProposalError("quality and catalog outputs use different reviewed source commits")
    if quality_producer[1] != "catalog-quality.yml":
        raise ProposalError("quality producer workflow is not catalog-quality.yml")

    manifest = load_object(manifest_path, "production catalog manifest")
    quality = load_object(quality_path, "quality report")
    if manifest.get("manifestSchemaVersion") != 3 or manifest.get("schemaVersion") != 2:
        raise ProposalError("production proposal requires catalog manifest v3 / SQLite schema v2")
    catalog_version = manifest.get("catalogVersion")
    if not isinstance(catalog_version, str) or not SEMVER.fullmatch(catalog_version) or "-demo." in catalog_version:
        raise ProposalError("production catalogVersion must be a non-demo semantic version")
    if manifest.get("sha256") != database_sha:
        raise ProposalError("manifest database digest differs from database handoff")
    record_count = manifest.get("recordCount")
    if not isinstance(record_count, int) or isinstance(record_count, bool) or record_count < 0:
        raise ProposalError("manifest recordCount is invalid")
    if database_handoff.get("recordCount") != record_count:
        raise ProposalError("database handoff record count differs from production manifest")
    if manifest_handoff.get("recordCount") != 1:
        raise ProposalError("manifest handoff must declare exactly one manifest record")
    if manifest.get("sourceCommit") != database_producer[0]:
        raise ProposalError("production manifest sourceCommit differs from immutable build producer")

    quality_gate = manifest.get("qualityGate")
    if not isinstance(quality_gate, dict):
        raise ProposalError("production manifest quality-gate binding is missing")
    if quality_gate.get("reportFileSha256") != quality_sha:
        raise ProposalError("production manifest quality report file digest differs from handoff")
    if quality_gate.get("sourceKey") != source_key or quality_gate.get("snapshotID") != snapshot_id:
        raise ProposalError("production manifest quality lineage differs from proposal source/snapshot")
    if quality.get("sourceKey") != source_key or quality.get("snapshotID") != snapshot_id:
        raise ProposalError("quality report lineage differs from proposal source/snapshot")
    if quality.get("status") != "pass":
        raise ProposalError("production proposal requires a passing quality report")
    if quality_gate.get("reportSha256") != quality.get("reportSha256"):
        raise ProposalError("production manifest does not bind the exact reviewed quality decision")

    bound_logical = manifest.get("logicalCatalog")
    if (
        not isinstance(bound_logical, dict)
        or set(bound_logical) != {"schemaVersion", "sha256"}
        or bound_logical.get("schemaVersion") != production_catalog_logical.LOGICAL_SCHEMA_VERSION
        or not isinstance(bound_logical.get("sha256"), str)
        or not SHA64.fullmatch(bound_logical["sha256"])
    ):
        raise ProposalError("production manifest logical-catalog identity is missing or invalid")
    try:
        actual_logical = production_catalog_logical.compute_identity(database_path)
    except production_catalog_logical.LogicalCatalogError as exc:
        raise ProposalError(f"failed to verify logical catalog identity: {exc}") from exc
    if actual_logical != bound_logical:
        raise ProposalError("production manifest logical-catalog identity differs from SQLite semantics")

    selection_policy = manifest.get("selectionPolicyVersion")
    if not isinstance(selection_policy, str) or not selection_policy:
        raise ProposalError("production manifest selection policy version is missing")
    counts = manifest.get("counts")
    statuses = manifest.get("statusDistribution")
    rights = manifest.get("rights")
    if not isinstance(counts, dict) or not isinstance(statuses, dict) or not isinstance(rights, dict):
        raise ProposalError("production manifest review metrics are incomplete")

    key = proposal_key(source_key, snapshot_id, database_sha)
    return {
        "schemaVersion": 1,
        "proposalKey": key,
        "sourceKey": source_key,
        "snapshotId": snapshot_id,
        "catalogVersion": catalog_version,
        "catalogSha256": database_sha,
        "manifestSha256": manifest_sha,
        "logicalCatalogSha256": bound_logical["sha256"],
        "recordCount": record_count,
        "selectionPolicyVersion": selection_policy,
        "qualityReportSha256": quality_sha,
        "qualityDecisionSha256": quality.get("reportSha256"),
        "qualityEvaluatedAt": quality_gate.get("evaluatedAt"),
        "counts": counts,
        "statusDistribution": statuses,
        "rights": rights,
        "releaseSummary": _release_summary(manifest=manifest, quality=quality, record_count=record_count),
        "materialChangeAutoMergeAllowed": False,
        "requiresHumanReview": True,
        "fixtureOnly": False,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-key", required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--database-handoff", type=Path, required=True)
    parser.add_argument("--database-root", type=Path, required=True)
    parser.add_argument("--manifest-handoff", type=Path, required=True)
    parser.add_argument("--manifest-root", type=Path, required=True)
    parser.add_argument("--quality-handoff", type=Path, required=True)
    parser.add_argument("--quality-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = prepare_report(
            source_key=args.source_key,
            snapshot_id=args.snapshot_id,
            database_handoff_path=args.database_handoff,
            database_root=args.database_root,
            manifest_handoff_path=args.manifest_handoff,
            manifest_root=args.manifest_root,
            quality_handoff_path=args.quality_handoff,
            quality_root=args.quality_root,
        )
    except ProposalError as exc:
        raise SystemExit(f"production proposal validation failed: {exc}") from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(report["proposalKey"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
