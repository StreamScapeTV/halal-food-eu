"""Digest-bound artifact handoff validation and deterministic workflow identities."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from catalog_workflow_common import (
    ARTIFACT_CLASSES,
    COMPLETENESS,
    HANDOFF_SCHEMA_VERSION,
    REPOSITORY,
    RUN_ID,
    SAFE_KEY,
    SAFE_SNAPSHOT,
    SAFE_WORKFLOW,
    SHA40,
    SHA64,
    ContractError,
    canonical_json,
    exact_keys,
    parse_timestamp,
    positive_int,
    require_object,
    safe_relative_path,
)
from catalog_workflow_contract import WorkflowContract


def validate_handoff(
    contract: WorkflowContract,
    raw: Any,
    *,
    consumer_stage: str | None = None,
    payload_root: Path | None = None,
) -> dict[str, Any]:
    handoff = require_object(raw, "handoff")
    exact_keys(
        handoff,
        required={"schemaVersion", "artifactKind", "sourceKey", "snapshotId", "producer", "payload", "recordCount", "completeness", "redistributionClass", "createdAt"},
        optional={"contentSchemaVersion"},
        label="handoff",
    )
    if handoff["schemaVersion"] != HANDOFF_SCHEMA_VERSION:
        raise ContractError(f"unsupported handoff schemaVersion {handoff['schemaVersion']!r}")
    artifact_kind = handoff["artifactKind"]
    if artifact_kind not in contract.artifacts:
        raise ContractError(f"unknown artifact kind {artifact_kind!r}")
    source_key = handoff["sourceKey"]
    if source_key != "aggregate" and source_key not in contract.sources:
        raise ContractError(f"handoff source {source_key!r} is not registered")
    snapshot_id = handoff["snapshotId"]
    if not isinstance(snapshot_id, str) or not SAFE_SNAPSHOT.fullmatch(snapshot_id):
        raise ContractError("handoff snapshotId is unsafe")

    producer = require_object(handoff["producer"], "handoff.producer")
    exact_keys(producer, required={"repository", "commitSha", "workflow", "runId"}, optional=set(), label="handoff.producer")
    if producer["repository"] != REPOSITORY:
        raise ContractError("handoff producer repository is not authoritative")
    if not isinstance(producer["commitSha"], str) or not SHA40.fullmatch(producer["commitSha"]):
        raise ContractError("handoff producer commitSha must be a lowercase 40-character SHA")
    if not isinstance(producer["workflow"], str) or not SAFE_WORKFLOW.fullmatch(producer["workflow"]):
        raise ContractError("handoff producer workflow is unsafe")
    if not isinstance(producer["runId"], str) or not RUN_ID.fullmatch(producer["runId"]):
        raise ContractError("handoff producer runId is invalid")

    payload = require_object(handoff["payload"], "handoff.payload")
    exact_keys(payload, required={"relativePath", "sha256", "byteCount"}, optional=set(), label="handoff.payload")
    relative_path = safe_relative_path(payload["relativePath"], "handoff.payload.relativePath")
    if not isinstance(payload["sha256"], str) or not SHA64.fullmatch(payload["sha256"]):
        raise ContractError("handoff.payload.sha256 must be a lowercase SHA-256 hex digest")
    byte_count = positive_int(payload["byteCount"], "handoff.payload.byteCount", allow_zero=True)
    record_count = positive_int(handoff["recordCount"], "handoff.recordCount", allow_zero=True)
    if handoff["completeness"] not in COMPLETENESS:
        raise ContractError("handoff.completeness is invalid")
    redistribution_class = handoff["redistributionClass"]
    if redistribution_class not in ARTIFACT_CLASSES:
        raise ContractError("handoff.redistributionClass is invalid")
    parse_timestamp(handoff["createdAt"], "handoff.createdAt")
    content_schema = handoff.get("contentSchemaVersion")
    if content_schema is not None and (not isinstance(content_schema, str) or len(content_schema) > 64 or not content_schema):
        raise ContractError("handoff.contentSchemaVersion is invalid")

    artifact_policy = contract.artifacts[artifact_kind]
    if byte_count > artifact_policy["maxBytes"]:
        raise ContractError(f"handoff exceeds {artifact_kind} maxBytes")
    if record_count > artifact_policy["maxRecords"]:
        raise ContractError(f"handoff exceeds {artifact_kind} maxRecords")
    if redistribution_class not in artifact_policy["allowedRedistributionClasses"]:
        raise ContractError(f"{artifact_kind} does not permit redistribution class {redistribution_class}")
    if consumer_stage is not None:
        stage = contract.stages.get(consumer_stage)
        if stage is None:
            raise ContractError(f"unknown consumer stage {consumer_stage!r}")
        if artifact_kind not in stage["accepts"]:
            raise ContractError(f"stage {consumer_stage} does not accept {artifact_kind}")
        if stage["requiresCompleteInput"] and handoff["completeness"] != "complete":
            raise ContractError(f"stage {consumer_stage} rejects partial artifacts")

    if payload_root is not None:
        root = payload_root.resolve()
        candidate = (root / relative_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ContractError("handoff payload escapes payload root") from exc
        if not candidate.is_file():
            raise ContractError(f"handoff payload does not exist: {relative_path}")
        data = candidate.read_bytes()
        if len(data) != byte_count:
            raise ContractError("handoff payload byteCount does not match file")
        actual = hashlib.sha256(data).hexdigest()
        if actual != payload["sha256"]:
            raise ContractError("handoff payload SHA-256 does not match file")

    return handoff


def emit_handoff(
    *,
    contract: WorkflowContract,
    artifact_kind: str,
    source_key: str,
    snapshot_id: str,
    producer_commit: str,
    producer_workflow: str,
    run_id: str,
    payload: Path,
    payload_relative_path: str,
    record_count: int,
    completeness: str,
    redistribution_class: str,
    content_schema_version: str | None,
    created_at: str,
) -> dict[str, Any]:
    if artifact_kind not in contract.artifacts:
        raise ContractError(f"unknown artifact kind {artifact_kind!r}")
    if source_key != "aggregate" and source_key not in contract.sources:
        raise ContractError(f"source {source_key!r} is not registered")
    if not payload.is_file():
        raise ContractError(f"payload does not exist: {payload}")
    data = payload.read_bytes()
    raw: dict[str, Any] = {
        "schemaVersion": HANDOFF_SCHEMA_VERSION,
        "artifactKind": artifact_kind,
        "sourceKey": source_key,
        "snapshotId": snapshot_id,
        "producer": {
            "repository": REPOSITORY,
            "commitSha": producer_commit,
            "workflow": producer_workflow,
            "runId": run_id,
        },
        "payload": {
            "relativePath": safe_relative_path(payload_relative_path, "payload relative path"),
            "sha256": hashlib.sha256(data).hexdigest(),
            "byteCount": len(data),
        },
        "recordCount": record_count,
        "completeness": completeness,
        "redistributionClass": redistribution_class,
        "createdAt": created_at,
    }
    if content_schema_version is not None:
        raw["contentSchemaVersion"] = content_schema_version
    validate_handoff(contract, raw)
    return raw


def proposal_key(source_key: str, snapshot_id: str, catalog_digest: str) -> str:
    if source_key != "aggregate" and not SAFE_KEY.fullmatch(source_key):
        raise ContractError("proposal source key is invalid")
    if not SAFE_SNAPSHOT.fullmatch(snapshot_id):
        raise ContractError("proposal snapshot ID is invalid")
    if not SHA64.fullmatch(catalog_digest):
        raise ContractError("proposal catalog digest must be lowercase SHA-256 hex")
    digest = hashlib.sha256(canonical_json({"sourceKey": source_key, "snapshotId": snapshot_id, "catalogDigest": catalog_digest})).hexdigest()
    return f"catalog-update/{source_key}-{digest[:16]}"


def health_key(condition: str, source_key: str | None = None) -> str:
    if not SAFE_KEY.fullmatch(condition):
        raise ContractError("health condition is invalid")
    if source_key is not None and not SAFE_KEY.fullmatch(source_key):
        raise ContractError("health source key is invalid")
    digest = hashlib.sha256(canonical_json({"condition": condition, "sourceKey": source_key})).hexdigest()
    suffix = f"-{source_key}" if source_key else ""
    return f"catalog-health-{condition}{suffix}-{digest[:12]}"
