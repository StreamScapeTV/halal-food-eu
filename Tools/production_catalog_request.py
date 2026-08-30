#!/usr/bin/env python3
"""Validate and execute the immutable production catalog build-request contract.

The request is deliberately local-file-only. Cross-stage payloads remain digest-bound
through the existing workflow handoff contract; this adapter turns an already reviewed
set of complete handoffs into exact arguments for ``production_catalog``. It never
acquires or refreshes source data, and it never admits an unbound basic-exclusion path.
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

REQUEST_SCHEMA_VERSION = 1
MAX_DATABASE_BYTES = 250 * 1024 * 1024
SHA40_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$")
WORKFLOW_RUN_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._:/-]{0,127}$")

_REQUIRED_KEYS = {
    "schemaVersion",
    "evidenceHandoffPath",
    "qualityHandoffPath",
    "basicExclusionsHandoffPath",
    "qualityPolicyPath",
    "sourcePolicyPaths",
    "databaseOutputPath",
    "manifestOutputPath",
    "catalogVersion",
    "selectionPolicyVersion",
    "generatedAt",
    "sourceCommit",
    "workflowRun",
    "maxDatabaseBytes",
}
_OPTIONAL_KEYS = {
    "logicalDumpOutputPath",
    "releaseNotesOutputPath",
    "previousManifestPath",
}


class BuildRequestError(ValueError):
    """Raised when a production build request fails closed."""


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise BuildRequestError(f"{label} must be a JSON object")
    return value


def _exact_keys(value: dict[str, Any]) -> None:
    keys = set(value)
    missing = sorted(_REQUIRED_KEYS - keys)
    extra = sorted(keys - _REQUIRED_KEYS - _OPTIONAL_KEYS)
    if missing:
        raise BuildRequestError(f"build request missing required keys: {', '.join(missing)}")
    if extra:
        raise BuildRequestError(f"build request has unexpected keys: {', '.join(extra)}")


def _relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512:
        raise BuildRequestError(f"{label} must be a bounded relative path")
    if "\\" in value or "\x00" in value:
        raise BuildRequestError(f"{label} must use safe POSIX path syntax")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise BuildRequestError(f"{label} must be traversal-free and relative")
    return value


def _timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BuildRequestError(f"{label} must be an ISO-8601 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise BuildRequestError(f"{label} must be a valid ISO-8601 UTC timestamp") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        raise BuildRequestError(f"{label} must be UTC")
    return value


def _version(value: Any, label: str) -> str:
    if not isinstance(value, str) or not VERSION_RE.fullmatch(value):
        raise BuildRequestError(f"{label} is invalid")
    return value


def validate_request(raw: Any) -> dict[str, Any]:
    request = _object(raw, "build request")
    _exact_keys(request)
    if request["schemaVersion"] != REQUEST_SCHEMA_VERSION:
        raise BuildRequestError(f"unsupported build request schemaVersion {request['schemaVersion']!r}")

    for key in (
        "evidenceHandoffPath",
        "qualityHandoffPath",
        "basicExclusionsHandoffPath",
        "qualityPolicyPath",
        "databaseOutputPath",
        "manifestOutputPath",
    ):
        _relative_path(request[key], key)
    for key in _OPTIONAL_KEYS:
        if key in request:
            _relative_path(request[key], key)

    source_policies = request["sourcePolicyPaths"]
    if not isinstance(source_policies, list) or not source_policies:
        raise BuildRequestError("sourcePolicyPaths must contain at least one reviewed source policy")
    if len(source_policies) > 32:
        raise BuildRequestError("sourcePolicyPaths exceeds the reviewed source-policy bound")
    normalized_policies = [_relative_path(value, "sourcePolicyPaths[]") for value in source_policies]
    if len(set(normalized_policies)) != len(normalized_policies):
        raise BuildRequestError("sourcePolicyPaths contains duplicates")

    _version(request["catalogVersion"], "catalogVersion")
    _version(request["selectionPolicyVersion"], "selectionPolicyVersion")
    _timestamp(request["generatedAt"], "generatedAt")
    if not isinstance(request["sourceCommit"], str) or not SHA40_RE.fullmatch(request["sourceCommit"]):
        raise BuildRequestError("sourceCommit must be an exact lowercase 40-character Git SHA")
    if not isinstance(request["workflowRun"], str) or not WORKFLOW_RUN_RE.fullmatch(request["workflowRun"]):
        raise BuildRequestError("workflowRun is invalid")
    max_bytes = request["maxDatabaseBytes"]
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or not (1 <= max_bytes <= MAX_DATABASE_BYTES):
        raise BuildRequestError(f"maxDatabaseBytes must be between 1 and {MAX_DATABASE_BYTES}")

    outputs = [request["databaseOutputPath"], request["manifestOutputPath"]]
    outputs.extend(request[key] for key in ("logicalDumpOutputPath", "releaseNotesOutputPath") if key in request)
    if len(set(outputs)) != len(outputs):
        raise BuildRequestError("build output paths must be distinct")
    inputs = {
        request["evidenceHandoffPath"],
        request["qualityHandoffPath"],
        request["basicExclusionsHandoffPath"],
        request["qualityPolicyPath"],
        *normalized_policies,
    }
    if "previousManifestPath" in request:
        inputs.add(request["previousManifestPath"])
    overlap = sorted(inputs.intersection(outputs))
    if overlap:
        raise BuildRequestError(f"build outputs overlap immutable inputs: {', '.join(overlap)}")
    return request


def load_request(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildRequestError(f"build request cannot be read as strict UTF-8 JSON: {path}") from exc
    return validate_request(raw)


def _under_root(root: Path, relative: str, *, must_exist: bool) -> Path:
    base = root.resolve()
    candidate = (base / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise BuildRequestError(f"resolved path escapes build root: {relative}") from exc
    if must_exist and not candidate.is_file():
        raise BuildRequestError(f"required build input does not exist: {relative}")
    return candidate


def resolve_request_paths(request: dict[str, Any], root: Path) -> dict[str, Any]:
    validated = validate_request(request)
    resolved: dict[str, Any] = dict(validated)
    for key in (
        "evidenceHandoffPath",
        "qualityHandoffPath",
        "basicExclusionsHandoffPath",
        "qualityPolicyPath",
    ):
        resolved[key] = _under_root(root, validated[key], must_exist=True)
    resolved["sourcePolicyPaths"] = [
        _under_root(root, relative, must_exist=True) for relative in validated["sourcePolicyPaths"]
    ]
    if "previousManifestPath" in validated:
        resolved["previousManifestPath"] = _under_root(
            root, validated["previousManifestPath"], must_exist=True
        )
    for key in ("databaseOutputPath", "manifestOutputPath", "logicalDumpOutputPath", "releaseNotesOutputPath"):
        if key in validated:
            resolved[key] = _under_root(root, validated[key], must_exist=False)
    return resolved


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BuildRequestError(f"{label} cannot be read as strict UTF-8 JSON") from exc
    return _object(value, label)


def _validate_basic_exclusions_payload(path: Path, expected_policy_version: str) -> None:
    payload = _load_json(path, "basic-exclusions payload")
    if payload.get("schemaVersion") != 1:
        raise BuildRequestError("basic-exclusions payload schemaVersion is unsupported")
    if payload.get("selectionPolicyVersion") != expected_policy_version:
        raise BuildRequestError("basic-exclusions payload selection-policy version differs from build request")
    records = payload.get("records")
    if not isinstance(records, list):
        raise BuildRequestError("basic-exclusions payload records must be an array")


def validate_build_handoffs(
    *,
    resolved: dict[str, Any],
    workflow_contract_path: Path,
    selection_policy_version: str | None = None,
) -> tuple[Path, Path, Path]:
    # Imported lazily so request-shape validation remains independently testable and
    # the adapter does not create a second workflow-contract implementation.
    from catalog_workflow_contract import WorkflowContract
    from catalog_workflow_handoff import validate_handoff

    contract = WorkflowContract.load(workflow_contract_path)
    evidence_path = resolved["evidenceHandoffPath"]
    quality_path = resolved["qualityHandoffPath"]
    exclusions_path = resolved["basicExclusionsHandoffPath"]
    evidence = validate_handoff(
        contract,
        _load_json(evidence_path, "evidence handoff"),
        consumer_stage="build",
        payload_root=evidence_path.parent,
    )
    quality = validate_handoff(
        contract,
        _load_json(quality_path, "quality handoff"),
        consumer_stage="build",
        payload_root=quality_path.parent,
    )
    exclusions = validate_handoff(
        contract,
        _load_json(exclusions_path, "basic-exclusions handoff"),
        consumer_stage="build",
        payload_root=exclusions_path.parent,
    )
    if evidence["artifactKind"] != "normalized-evidence":
        raise BuildRequestError("evidence handoff must contain normalized-evidence")
    if quality["artifactKind"] != "quality-report":
        raise BuildRequestError("quality handoff must contain quality-report")
    if exclusions["artifactKind"] != "basic-exclusions":
        raise BuildRequestError("basic-exclusions handoff must contain basic-exclusions")

    handoffs = (evidence, quality, exclusions)
    if len({handoff["sourceKey"] for handoff in handoffs}) != 1:
        raise BuildRequestError("evidence, quality, and basic-exclusions handoffs have different sourceKey values")
    if len({handoff["snapshotId"] for handoff in handoffs}) != 1:
        raise BuildRequestError("evidence, quality, and basic-exclusions handoffs have different snapshotId values")

    evidence_payload = _under_root(evidence_path.parent, evidence["payload"]["relativePath"], must_exist=True)
    quality_payload = _under_root(quality_path.parent, quality["payload"]["relativePath"], must_exist=True)
    exclusions_payload = _under_root(
        exclusions_path.parent,
        exclusions["payload"]["relativePath"],
        must_exist=True,
    )
    if selection_policy_version is not None:
        _validate_basic_exclusions_payload(exclusions_payload, selection_policy_version)
    return evidence_payload, quality_payload, exclusions_payload


def build_from_request(*, request_path: Path, root: Path, workflow_contract_path: Path) -> dict[str, Any]:
    request = load_request(request_path)
    resolved = resolve_request_paths(request, root)
    evidence_payload, quality_payload, exclusions_payload = validate_build_handoffs(
        resolved=resolved,
        workflow_contract_path=workflow_contract_path,
        selection_policy_version=request["selectionPolicyVersion"],
    )

    import production_catalog

    return production_catalog.build_catalog(
        evidence_path=evidence_payload,
        database_path=resolved["databaseOutputPath"],
        manifest_path=resolved["manifestOutputPath"],
        policy_paths=resolved["sourcePolicyPaths"],
        basic_exclusions_path=exclusions_payload,
        quality_report_path=quality_payload,
        quality_policy_path=resolved["qualityPolicyPath"],
        catalog_version=request["catalogVersion"],
        selection_policy_version=request["selectionPolicyVersion"],
        generated_at=request["generatedAt"],
        source_commit=request["sourceCommit"],
        workflow_run=request["workflowRun"],
        logical_dump_path=resolved.get("logicalDumpOutputPath"),
        release_notes_path=resolved.get("releaseNotesOutputPath"),
        previous_manifest_path=resolved.get("previousManifestPath"),
        max_database_bytes=request["maxDatabaseBytes"],
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--request", type=Path, required=True)
    validate.add_argument("--root", type=Path, required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--request", type=Path, required=True)
    build.add_argument("--root", type=Path, required=True)
    build.add_argument(
        "--workflow-contract",
        type=Path,
        default=Path("Data/workflows/catalog-workflow-contract-v1.json"),
    )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "validate":
            request = load_request(args.request)
            resolve_request_paths(request, args.root)
            print(json.dumps({"schemaVersion": request["schemaVersion"], "valid": True}, sort_keys=True))
        else:
            manifest = build_from_request(
                request_path=args.request,
                root=args.root,
                workflow_contract_path=args.workflow_contract,
            )
            print(json.dumps({"catalogVersion": manifest["catalogVersion"], "sha256": manifest["sha256"]}, sort_keys=True))
    except (BuildRequestError, ValueError) as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
