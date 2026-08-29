#!/usr/bin/env python3
"""Validate Halal Food EU catalog workflow contracts and bounded handoffs.

The CLI is deliberately standard-library-only and local-file-only. Non-trivial
validation lives in focused modules so workflow YAML remains thin orchestration.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from catalog_workflow_common import ARTIFACT_CLASSES, COMPLETENESS, CONTRACT_SCHEMA_VERSION, ContractError, load_json
from catalog_workflow_contract import WorkflowContract
from catalog_workflow_handoff import emit_handoff, health_key, proposal_key, validate_handoff
from catalog_workflow_policy import validate_workflows


def _command_validate_contract(args: argparse.Namespace) -> None:
    contract = WorkflowContract.load(Path(args.contract))
    print(json.dumps({
        "schemaVersion": CONTRACT_SCHEMA_VERSION,
        "contractVersion": contract.raw["contractVersion"],
        "stages": list(contract.stage_order),
        "sources": sorted(contract.sources),
        "retryDelaysSeconds": list(contract.retry_delays()),
    }, sort_keys=True))


def _command_validate_source(args: argparse.Namespace) -> None:
    contract = WorkflowContract.load(Path(args.contract))
    source = contract.validate_source(args.source_key, args.snapshot_id, args.mode)
    print(json.dumps({
        "sourceKey": source.key,
        "enabled": source.enabled,
        "credentialsRequired": source.credentials_required,
        "redistributionClass": source.redistribution_class,
        "adapterVersion": source.adapter_version,
    }, sort_keys=True))


def _command_validate_handoff(args: argparse.Namespace) -> None:
    contract = WorkflowContract.load(Path(args.contract))
    handoff = validate_handoff(
        contract,
        load_json(Path(args.input)),
        consumer_stage=args.consumer_stage,
        payload_root=Path(args.payload_root) if args.payload_root else None,
    )
    print(json.dumps({
        "artifactKind": handoff["artifactKind"],
        "sourceKey": handoff["sourceKey"],
        "snapshotId": handoff["snapshotId"],
        "completeness": handoff["completeness"],
    }, sort_keys=True))


def _command_emit_handoff(args: argparse.Namespace) -> None:
    contract = WorkflowContract.load(Path(args.contract))
    raw = emit_handoff(
        contract=contract,
        artifact_kind=args.artifact_kind,
        source_key=args.source_key,
        snapshot_id=args.snapshot_id,
        producer_commit=args.producer_commit,
        producer_workflow=args.producer_workflow,
        run_id=args.run_id,
        payload=Path(args.payload),
        payload_relative_path=args.payload_relative_path,
        record_count=args.record_count,
        completeness=args.completeness,
        redistribution_class=args.redistribution_class,
        content_schema_version=args.content_schema_version,
        created_at=args.created_at,
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(raw, indent=2) + "\n", encoding="utf-8")
    print(output)


def _command_validate_workflows(args: argparse.Namespace) -> None:
    checked = validate_workflows(Path(args.root))
    print(json.dumps({"checked": checked}, sort_keys=True))


def _command_proposal_key(args: argparse.Namespace) -> None:
    print(proposal_key(args.source_key, args.snapshot_id, args.catalog_digest))


def _command_health_key(args: argparse.Namespace) -> None:
    print(health_key(args.condition, args.source_key))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", default="Data/workflows/catalog-workflow-contract-v1.json")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_contract = subparsers.add_parser("validate-contract")
    validate_contract.set_defaults(func=_command_validate_contract)

    validate_source = subparsers.add_parser("validate-source")
    validate_source.add_argument("--source-key", required=True)
    validate_source.add_argument("--snapshot-id", required=True)
    validate_source.add_argument("--mode", required=True)
    validate_source.set_defaults(func=_command_validate_source)

    validate_handoff_cmd = subparsers.add_parser("validate-handoff")
    validate_handoff_cmd.add_argument("--input", required=True)
    validate_handoff_cmd.add_argument("--consumer-stage")
    validate_handoff_cmd.add_argument("--payload-root")
    validate_handoff_cmd.set_defaults(func=_command_validate_handoff)

    emit = subparsers.add_parser("emit-handoff")
    emit.add_argument("--artifact-kind", required=True)
    emit.add_argument("--source-key", required=True)
    emit.add_argument("--snapshot-id", required=True)
    emit.add_argument("--producer-commit", required=True)
    emit.add_argument("--producer-workflow", required=True)
    emit.add_argument("--run-id", required=True)
    emit.add_argument("--payload", required=True)
    emit.add_argument("--payload-relative-path", required=True)
    emit.add_argument("--record-count", type=int, required=True)
    emit.add_argument("--completeness", choices=sorted(COMPLETENESS), required=True)
    emit.add_argument("--redistribution-class", choices=sorted(ARTIFACT_CLASSES), required=True)
    emit.add_argument("--content-schema-version")
    emit.add_argument("--created-at", required=True)
    emit.add_argument("--output", required=True)
    emit.set_defaults(func=_command_emit_handoff)

    workflow_cmd = subparsers.add_parser("validate-workflows")
    workflow_cmd.add_argument("--root", default=".github/workflows")
    workflow_cmd.set_defaults(func=_command_validate_workflows)

    proposal = subparsers.add_parser("proposal-key")
    proposal.add_argument("--source-key", required=True)
    proposal.add_argument("--snapshot-id", required=True)
    proposal.add_argument("--catalog-digest", required=True)
    proposal.set_defaults(func=_command_proposal_key)

    health = subparsers.add_parser("health-key")
    health.add_argument("--condition", required=True)
    health.add_argument("--source-key")
    health.set_defaults(func=_command_health_key)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        args.func(args)
    except ContractError as exc:
        print(f"workflow contract error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
