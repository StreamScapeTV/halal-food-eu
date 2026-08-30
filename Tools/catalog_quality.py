#!/usr/bin/env python3
"""Validate catalog quality policy and evaluate release-quality evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from catalog_quality_core import CatalogQualityError, evaluate_quality, human_summary, validate_policy
from evidence_model_core import EvidenceValidationError, validate_envelope

DEFAULT_POLICY = Path("Data/quality/catalog-quality-policy-v1.json")
DEFAULT_WORKFLOW_CONTRACT = Path("Data/workflows/catalog-workflow-contract-v1.json")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogQualityError(f"failed to load JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogQualityError(f"{path} must contain a JSON object")
    return value


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _source_policy_path(source_key: str) -> Path | None:
    if source_key == "synthetic-fixture":
        return None
    candidate = Path("Data/sources") / source_key / "source-policy-v1.json"
    return candidate if candidate.exists() else None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-policy", help="validate the committed quality policy")
    validate.add_argument("--policy", type=Path, default=DEFAULT_POLICY)

    evaluate = sub.add_parser("evaluate", help="evaluate a normalized evidence envelope and change report")
    evaluate.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    evaluate.add_argument("--evidence", type=Path, required=True)
    evaluate.add_argument("--change-report", type=Path)
    evaluate.add_argument("--source-key", required=True)
    evaluate.add_argument("--snapshot-id", required=True)
    evaluate.add_argument("--workflow-contract", type=Path, default=DEFAULT_WORKFLOW_CONTRACT)
    evaluate.add_argument("--source-policy", type=Path)
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--summary-output", type=Path, required=True)
    evaluate.add_argument(
        "--defer-blocker-exit",
        action="store_true",
        help="write blocked reports successfully so a later workflow step can publish diagnostics before enforcing the gate",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        policy = load_json(args.policy)
        validate_policy(policy)
        if args.command == "validate-policy":
            print(f"Validated catalog quality policy {policy['policyVersion']}")
            return

        evidence = load_json(args.evidence)
        validate_envelope(evidence)
        change = load_json(args.change_report) if args.change_report else None
        workflow_contract = load_json(args.workflow_contract) if args.workflow_contract else None
        source_policy_path = args.source_policy or _source_policy_path(args.source_key)
        source_policy = load_json(source_policy_path) if source_policy_path else None
        report = evaluate_quality(
            policy=policy,
            envelope=evidence,
            source_key=args.source_key,
            snapshot_id=args.snapshot_id,
            change_report=change,
            workflow_contract=workflow_contract,
            source_policy=source_policy,
        )
        write_json(args.output, report)
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        args.summary_output.write_text(human_summary(report), encoding="utf-8")
        print(f"Catalog quality status: {report['status']} ({len(report['releaseBlockingFindings'])} blockers)")
        if report["status"] != "pass" and not args.defer_blocker_exit:
            raise SystemExit(2)
    except (CatalogQualityError, EvidenceValidationError) as exc:
        raise SystemExit(f"catalog quality validation failed: {exc}") from exc


if __name__ == "__main__":
    main()
