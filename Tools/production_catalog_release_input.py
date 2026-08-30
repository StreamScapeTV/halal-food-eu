#!/usr/bin/env python3
"""Create and verify the production catalog post-merge rematerialization receipt.

The generated receipt is intentionally metadata-only. It records the exact immutable
normalized-evidence, quality-report, and basic-exclusions artifacts that were reviewed
for a production catalog proposal so protected ``main`` can later download those same
inputs and rebuild the SQLite/manifest pair with the integrated source revision.

This module never performs acquisition and never talks to GitHub or another network
service. Workflow code remains responsible for downloading artifacts; this module
only validates local handoffs/payloads and emits a deterministic build request.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Iterable

RECEIPT_SCHEMA_VERSION = 1
MAX_DATABASE_BYTES = 250 * 1024 * 1024
SAFE_SOURCE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")
SAFE_SNAPSHOT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_ARTIFACT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")
SAFE_PROPOSAL = re.compile(r"^catalog-update/[a-z0-9][a-z0-9.-]{0,63}-[0-9a-f]{16}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z.-]+))?$")
WORKFLOW_RUN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._:/-]{0,127}$")


class ReleaseInputError(ValueError):
    """Raised when a release-input receipt or its immutable inputs fail closed."""


_RECEIPT_KEYS = {
    "schemaVersion",
    "sourceKey",
    "snapshotId",
    "catalogVersion",
    "proposalKey",
    "reviewedSourceCommit",
    "sourceRunId",
    "proposedCatalogSha256",
    "proposedManifestSha256",
    "logicalCatalogSha256",
    "selectionPolicyVersion",
    "qualityEvaluatedAt",
    "inputs",
}
_INPUT_KEYS = {
    "artifactName",
    "artifactKind",
    "producerWorkflow",
    "payloadSha256",
    "payloadByteCount",
    "recordCount",
    "contentSchemaVersion",
}
_EXPECTED_INPUTS = {
    "normalizedEvidence": ("normalized-evidence", "normalize-and-diff.yml"),
    "qualityReport": ("quality-report", "catalog-quality.yml"),
    "basicExclusions": ("basic-exclusions", "normalize-and-diff.yml"),
}


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseInputError(f"failed to read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReleaseInputError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: dict[str, Any], expected: set[str], label: str) -> None:
    missing = sorted(expected - set(value))
    extra = sorted(set(value) - expected)
    if missing:
        raise ReleaseInputError(f"{label} is missing required fields: {', '.join(missing)}")
    if extra:
        raise ReleaseInputError(f"{label} has unexpected fields: {', '.join(extra)}")


def _safe_payload(root: Path, handoff: dict[str, Any], expected_kind: str) -> tuple[Path, str, int]:
    if handoff.get("artifactKind") != expected_kind:
        raise ReleaseInputError(f"expected {expected_kind} handoff")
    payload = handoff.get("payload")
    if not isinstance(payload, dict):
        raise ReleaseInputError(f"{expected_kind} handoff payload is missing")
    relative = payload.get("relativePath")
    expected_sha = payload.get("sha256")
    expected_bytes = payload.get("byteCount")
    if not isinstance(relative, str) or not relative or relative.startswith(("/", "\\")) or "\\" in relative:
        raise ReleaseInputError(f"{expected_kind} payload path is unsafe")
    if any(part in ("", ".", "..") for part in Path(relative).parts):
        raise ReleaseInputError(f"{expected_kind} payload path is unsafe")
    if not isinstance(expected_sha, str) or not SHA64.fullmatch(expected_sha):
        raise ReleaseInputError(f"{expected_kind} payload SHA-256 is invalid")
    if not isinstance(expected_bytes, int) or isinstance(expected_bytes, bool) or expected_bytes < 0:
        raise ReleaseInputError(f"{expected_kind} payload byte count is invalid")
    resolved_root = root.resolve()
    candidate = (resolved_root / relative).resolve()
    try:
        candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ReleaseInputError(f"{expected_kind} payload escapes its root") from exc
    if not candidate.is_file():
        raise ReleaseInputError(f"{expected_kind} payload is missing")
    data = candidate.read_bytes()
    if len(data) != expected_bytes:
        raise ReleaseInputError(f"{expected_kind} payload byte count mismatch")
    actual_sha = hashlib.sha256(data).hexdigest()
    if actual_sha != expected_sha:
        raise ReleaseInputError(f"{expected_kind} payload SHA-256 mismatch")
    return candidate, actual_sha, len(data)


def _producer(handoff: dict[str, Any], label: str) -> tuple[str, str, str]:
    producer = handoff.get("producer")
    if not isinstance(producer, dict):
        raise ReleaseInputError(f"{label} producer is missing")
    commit = producer.get("commitSha")
    workflow = producer.get("workflow")
    run_id = producer.get("runId")
    if not isinstance(commit, str) or not SHA40.fullmatch(commit):
        raise ReleaseInputError(f"{label} producer commit is invalid")
    if not isinstance(workflow, str) or not workflow:
        raise ReleaseInputError(f"{label} producer workflow is invalid")
    if not isinstance(run_id, str) or not run_id.isdigit():
        raise ReleaseInputError(f"{label} producer run ID is invalid")
    return commit, workflow, run_id


def _artifact_name(value: str, label: str) -> str:
    if not SAFE_ARTIFACT.fullmatch(value):
        raise ReleaseInputError(f"{label} artifact name is invalid")
    return value


def _handoff_receipt(
    *,
    label: str,
    handoff: dict[str, Any],
    root: Path,
    artifact_name: str,
    expected_kind: str,
    expected_workflow: str,
    source_key: str,
    snapshot_id: str,
) -> tuple[dict[str, Any], tuple[str, str, str]]:
    _, payload_sha, payload_bytes = _safe_payload(root, handoff, expected_kind)
    if handoff.get("sourceKey") != source_key:
        raise ReleaseInputError(f"{label} sourceKey differs from production source")
    if handoff.get("snapshotId") != snapshot_id:
        raise ReleaseInputError(f"{label} snapshotId differs from production snapshot")
    if handoff.get("completeness") != "complete":
        raise ReleaseInputError(f"{label} handoff is not complete")
    if expected_kind in {"normalized-evidence", "basic-exclusions"}:
        if handoff.get("redistributionClass") != "redistributable":
            raise ReleaseInputError(f"{label} must be redistributable for post-merge rematerialization")
    elif handoff.get("redistributionClass") not in {"metadata-only", "redistributable"}:
        raise ReleaseInputError(f"{label} redistribution class is not admissible")
    producer = _producer(handoff, label)
    if producer[1] != expected_workflow:
        raise ReleaseInputError(f"{label} producer workflow is not {expected_workflow}")
    record_count = handoff.get("recordCount")
    if not isinstance(record_count, int) or isinstance(record_count, bool) or record_count < 0:
        raise ReleaseInputError(f"{label} recordCount is invalid")
    content_schema = handoff.get("contentSchemaVersion")
    if not isinstance(content_schema, str) or not content_schema:
        raise ReleaseInputError(f"{label} contentSchemaVersion is missing")
    return (
        {
            "artifactName": _artifact_name(artifact_name, label),
            "artifactKind": expected_kind,
            "producerWorkflow": expected_workflow,
            "payloadSha256": payload_sha,
            "payloadByteCount": payload_bytes,
            "recordCount": record_count,
            "contentSchemaVersion": content_schema,
        },
        producer,
    )


def validate_release_input(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ReleaseInputError("release input must be a JSON object")
    _exact_keys(raw, _RECEIPT_KEYS, "release input")
    if raw["schemaVersion"] != RECEIPT_SCHEMA_VERSION:
        raise ReleaseInputError(f"unsupported release input schemaVersion {raw['schemaVersion']!r}")
    source_key = raw["sourceKey"]
    if not isinstance(source_key, str) or not SAFE_SOURCE.fullmatch(source_key) or source_key != "open-food-facts":
        raise ReleaseInputError("release input currently admits only open-food-facts")
    snapshot_id = raw["snapshotId"]
    if not isinstance(snapshot_id, str) or not SAFE_SNAPSHOT.fullmatch(snapshot_id):
        raise ReleaseInputError("release input snapshotId is invalid")
    catalog_version = raw["catalogVersion"]
    match = SEMVER.fullmatch(catalog_version) if isinstance(catalog_version, str) else None
    if not match or match.group(4) is not None:
        raise ReleaseInputError("release input catalogVersion must be a production semantic version")
    proposal_key = raw["proposalKey"]
    if not isinstance(proposal_key, str) or not SAFE_PROPOSAL.fullmatch(proposal_key):
        raise ReleaseInputError("release input proposalKey is invalid")
    if not proposal_key.startswith(f"catalog-update/{source_key}-"):
        raise ReleaseInputError("release input proposalKey does not match sourceKey")
    if not isinstance(raw["reviewedSourceCommit"], str) or not SHA40.fullmatch(raw["reviewedSourceCommit"]):
        raise ReleaseInputError("release input reviewedSourceCommit is invalid")
    if not isinstance(raw["sourceRunId"], str) or not raw["sourceRunId"].isdigit():
        raise ReleaseInputError("release input sourceRunId is invalid")
    for key in ("proposedCatalogSha256", "proposedManifestSha256", "logicalCatalogSha256"):
        if not isinstance(raw[key], str) or not SHA64.fullmatch(raw[key]):
            raise ReleaseInputError(f"release input {key} is invalid")
    if not isinstance(raw["selectionPolicyVersion"], str) or not raw["selectionPolicyVersion"]:
        raise ReleaseInputError("release input selectionPolicyVersion is missing")
    if not isinstance(raw["qualityEvaluatedAt"], str) or not raw["qualityEvaluatedAt"].endswith("Z"):
        raise ReleaseInputError("release input qualityEvaluatedAt is invalid")

    inputs = raw["inputs"]
    if not isinstance(inputs, dict):
        raise ReleaseInputError("release input inputs must be an object")
    _exact_keys(inputs, set(_EXPECTED_INPUTS), "release input inputs")
    for label, (expected_kind, expected_workflow) in _EXPECTED_INPUTS.items():
        entry = inputs[label]
        if not isinstance(entry, dict):
            raise ReleaseInputError(f"release input {label} must be an object")
        _exact_keys(entry, _INPUT_KEYS, f"release input {label}")
        _artifact_name(entry["artifactName"], label)
        if entry["artifactKind"] != expected_kind:
            raise ReleaseInputError(f"release input {label} artifactKind is invalid")
        if entry["producerWorkflow"] != expected_workflow:
            raise ReleaseInputError(f"release input {label} producerWorkflow is invalid")
        if not isinstance(entry["payloadSha256"], str) or not SHA64.fullmatch(entry["payloadSha256"]):
            raise ReleaseInputError(f"release input {label} payloadSha256 is invalid")
        if not isinstance(entry["payloadByteCount"], int) or isinstance(entry["payloadByteCount"], bool) or entry["payloadByteCount"] < 0:
            raise ReleaseInputError(f"release input {label} payloadByteCount is invalid")
        if not isinstance(entry["recordCount"], int) or isinstance(entry["recordCount"], bool) or entry["recordCount"] < 0:
            raise ReleaseInputError(f"release input {label} recordCount is invalid")
        if not isinstance(entry["contentSchemaVersion"], str) or not entry["contentSchemaVersion"]:
            raise ReleaseInputError(f"release input {label} contentSchemaVersion is invalid")
    return raw


def prepare_release_input(
    *,
    source_key: str,
    snapshot_id: str,
    proposal_report_path: Path,
    normalized_handoff_path: Path,
    normalized_root: Path,
    normalized_artifact_name: str,
    quality_handoff_path: Path,
    quality_root: Path,
    quality_artifact_name: str,
    basic_exclusions_handoff_path: Path,
    basic_exclusions_root: Path,
    basic_exclusions_artifact_name: str,
) -> dict[str, Any]:
    if not SAFE_SOURCE.fullmatch(source_key) or source_key != "open-food-facts":
        raise ReleaseInputError("production release input currently admits only open-food-facts")
    if not SAFE_SNAPSHOT.fullmatch(snapshot_id):
        raise ReleaseInputError("production release input snapshotId is invalid")

    proposal = _load_object(proposal_report_path, "production proposal report")
    if proposal.get("schemaVersion") != 1 or proposal.get("fixtureOnly") is not False:
        raise ReleaseInputError("production proposal report is not a production v1 report")
    if proposal.get("sourceKey") != source_key or proposal.get("snapshotId") != snapshot_id:
        raise ReleaseInputError("production proposal report lineage differs from release input")
    if proposal.get("requiresHumanReview") is not True or proposal.get("materialChangeAutoMergeAllowed") is not False:
        raise ReleaseInputError("production proposal review policy is not fail-closed")

    proposal_key = proposal.get("proposalKey")
    catalog_version = proposal.get("catalogVersion")
    catalog_sha = proposal.get("catalogSha256")
    manifest_sha = proposal.get("manifestSha256")
    logical_sha = proposal.get("logicalCatalogSha256")
    selection_policy = proposal.get("selectionPolicyVersion")
    quality_evaluated_at = proposal.get("qualityEvaluatedAt")
    if not isinstance(logical_sha, str) or not SHA64.fullmatch(logical_sha):
        raise ReleaseInputError("production proposal logical catalog SHA-256 is missing or invalid")

    normalized = _load_object(normalized_handoff_path, "normalized-evidence handoff")
    quality = _load_object(quality_handoff_path, "quality-report handoff")
    exclusions = _load_object(basic_exclusions_handoff_path, "basic-exclusions handoff")
    normalized_entry, normalized_producer = _handoff_receipt(
        label="normalizedEvidence", handoff=normalized, root=normalized_root,
        artifact_name=normalized_artifact_name, expected_kind="normalized-evidence",
        expected_workflow="normalize-and-diff.yml", source_key=source_key, snapshot_id=snapshot_id,
    )
    quality_entry, quality_producer = _handoff_receipt(
        label="qualityReport", handoff=quality, root=quality_root,
        artifact_name=quality_artifact_name, expected_kind="quality-report",
        expected_workflow="catalog-quality.yml", source_key=source_key, snapshot_id=snapshot_id,
    )
    exclusions_entry, exclusions_producer = _handoff_receipt(
        label="basicExclusions", handoff=exclusions, root=basic_exclusions_root,
        artifact_name=basic_exclusions_artifact_name, expected_kind="basic-exclusions",
        expected_workflow="normalize-and-diff.yml", source_key=source_key, snapshot_id=snapshot_id,
    )
    producers = (normalized_producer, quality_producer, exclusions_producer)
    if len({producer[0] for producer in producers}) != 1:
        raise ReleaseInputError("release inputs were produced from different reviewed source commits")
    if len({producer[2] for producer in producers}) != 1:
        raise ReleaseInputError("release inputs were produced by different workflow runs")

    receipt = {
        "schemaVersion": RECEIPT_SCHEMA_VERSION,
        "sourceKey": source_key,
        "snapshotId": snapshot_id,
        "catalogVersion": catalog_version,
        "proposalKey": proposal_key,
        "reviewedSourceCommit": producers[0][0],
        "sourceRunId": producers[0][2],
        "proposedCatalogSha256": catalog_sha,
        "proposedManifestSha256": manifest_sha,
        "logicalCatalogSha256": logical_sha,
        "selectionPolicyVersion": selection_policy,
        "qualityEvaluatedAt": quality_evaluated_at,
        "inputs": {
            "normalizedEvidence": normalized_entry,
            "qualityReport": quality_entry,
            "basicExclusions": exclusions_entry,
        },
    }
    return validate_release_input(receipt)


def verify_downloaded_inputs(
    *,
    receipt: dict[str, Any],
    normalized_handoff_path: Path,
    normalized_root: Path,
    quality_handoff_path: Path,
    quality_root: Path,
    basic_exclusions_handoff_path: Path,
    basic_exclusions_root: Path,
) -> None:
    validated = validate_release_input(receipt)
    mappings = (
        ("normalizedEvidence", normalized_handoff_path, normalized_root),
        ("qualityReport", quality_handoff_path, quality_root),
        ("basicExclusions", basic_exclusions_handoff_path, basic_exclusions_root),
    )
    observed_producers: list[tuple[str, str, str]] = []
    for label, handoff_path, root in mappings:
        expected = validated["inputs"][label]
        handoff = _load_object(handoff_path, f"{label} handoff")
        entry, producer = _handoff_receipt(
            label=label,
            handoff=handoff,
            root=root,
            artifact_name=expected["artifactName"],
            expected_kind=expected["artifactKind"],
            expected_workflow=expected["producerWorkflow"],
            source_key=validated["sourceKey"],
            snapshot_id=validated["snapshotId"],
        )
        if entry != expected:
            raise ReleaseInputError(f"downloaded {label} differs from reviewed release receipt")
        observed_producers.append(producer)
    if any(producer[0] != validated["reviewedSourceCommit"] for producer in observed_producers):
        raise ReleaseInputError("downloaded release input commit differs from reviewed receipt")
    if any(producer[2] != validated["sourceRunId"] for producer in observed_producers):
        raise ReleaseInputError("downloaded release input run differs from reviewed receipt")


def build_request_from_release_input(
    receipt: dict[str, Any],
    *,
    integrated_source_commit: str,
    workflow_run: str,
) -> dict[str, Any]:
    validated = validate_release_input(receipt)
    if not SHA40.fullmatch(integrated_source_commit):
        raise ReleaseInputError("integrated source commit must be an exact lowercase Git SHA")
    if not WORKFLOW_RUN.fullmatch(workflow_run):
        raise ReleaseInputError("release workflow run identity is invalid")
    return {
        "schemaVersion": 1,
        "evidenceHandoffPath": "normalized/handoff.json",
        "qualityHandoffPath": "quality/handoff.json",
        "basicExclusionsHandoffPath": "basic-exclusions/handoff.json",
        "qualityPolicyPath": "repository/Data/quality/catalog-quality-policy-v1.json",
        "sourcePolicyPaths": ["repository/Data/sources/open-food-facts/source-policy-v1.json"],
        "databaseOutputPath": "database/payload/catalog.sqlite3",
        "manifestOutputPath": "manifest/payload/catalog-manifest.json",
        "catalogVersion": validated["catalogVersion"],
        "selectionPolicyVersion": validated["selectionPolicyVersion"],
        "generatedAt": validated["qualityEvaluatedAt"],
        "sourceCommit": integrated_source_commit,
        "workflowRun": workflow_run,
        "maxDatabaseBytes": MAX_DATABASE_BYTES,
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--source-key", required=True)
    create.add_argument("--snapshot-id", required=True)
    create.add_argument("--proposal-report", type=Path, required=True)
    create.add_argument("--normalized-handoff", type=Path, required=True)
    create.add_argument("--normalized-root", type=Path, required=True)
    create.add_argument("--normalized-artifact-name", required=True)
    create.add_argument("--quality-handoff", type=Path, required=True)
    create.add_argument("--quality-root", type=Path, required=True)
    create.add_argument("--quality-artifact-name", required=True)
    create.add_argument("--basic-exclusions-handoff", type=Path, required=True)
    create.add_argument("--basic-exclusions-root", type=Path, required=True)
    create.add_argument("--basic-exclusions-artifact-name", required=True)
    create.add_argument("--output", type=Path, required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--input", type=Path, required=True)
    request = subparsers.add_parser("materialize-request")
    request.add_argument("--input", type=Path, required=True)
    request.add_argument("--normalized-handoff", type=Path, required=True)
    request.add_argument("--normalized-root", type=Path, required=True)
    request.add_argument("--quality-handoff", type=Path, required=True)
    request.add_argument("--quality-root", type=Path, required=True)
    request.add_argument("--basic-exclusions-handoff", type=Path, required=True)
    request.add_argument("--basic-exclusions-root", type=Path, required=True)
    request.add_argument("--integrated-source-commit", required=True)
    request.add_argument("--workflow-run", required=True)
    request.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "create":
            receipt = prepare_release_input(
                source_key=args.source_key,
                snapshot_id=args.snapshot_id,
                proposal_report_path=args.proposal_report,
                normalized_handoff_path=args.normalized_handoff,
                normalized_root=args.normalized_root,
                normalized_artifact_name=args.normalized_artifact_name,
                quality_handoff_path=args.quality_handoff,
                quality_root=args.quality_root,
                quality_artifact_name=args.quality_artifact_name,
                basic_exclusions_handoff_path=args.basic_exclusions_handoff,
                basic_exclusions_root=args.basic_exclusions_root,
                basic_exclusions_artifact_name=args.basic_exclusions_artifact_name,
            )
            _write_json(args.output, receipt)
            print(receipt["proposalKey"])
        elif args.command == "validate":
            receipt = validate_release_input(_load_object(args.input, "release input"))
            print(json.dumps({"schemaVersion": receipt["schemaVersion"], "valid": True}, sort_keys=True))
        else:
            receipt = validate_release_input(_load_object(args.input, "release input"))
            verify_downloaded_inputs(
                receipt=receipt,
                normalized_handoff_path=args.normalized_handoff,
                normalized_root=args.normalized_root,
                quality_handoff_path=args.quality_handoff,
                quality_root=args.quality_root,
                basic_exclusions_handoff_path=args.basic_exclusions_handoff,
                basic_exclusions_root=args.basic_exclusions_root,
            )
            request = build_request_from_release_input(
                receipt,
                integrated_source_commit=args.integrated_source_commit,
                workflow_run=args.workflow_run,
            )
            _write_json(args.output, request)
            print(request["catalogVersion"])
    except ReleaseInputError as exc:
        raise SystemExit(f"production release input validation failed: {exc}") from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
