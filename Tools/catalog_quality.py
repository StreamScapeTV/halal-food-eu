#!/usr/bin/env python3
"""Validate catalog quality policy and evaluate release-quality evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from catalog_health import build_health_report, human_summary as health_summary
from catalog_quality_core import CatalogQualityError, canonical_json, evaluate_quality, human_summary, parse_timestamp, validate_policy
from catalog_quality_reporting import augment_quality_report
from catalog_quality_source_review import SourceReviewError, enforce_source_review, validate_source_reviews
from catalog_workflow_handoff import health_key
from evidence_model_core import EvidenceValidationError, validate_envelope

DEFAULT_POLICY = Path("Data/quality/catalog-quality-policy-v1.json")
DEFAULT_WORKFLOW_CONTRACT = Path("Data/workflows/catalog-workflow-contract-v1.json")
DEFAULT_SOURCE_REVIEWS = Path("Data/quality/source-review-policy-v1.json")


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


def decorate_incident(report: dict[str, Any], source_key: str) -> dict[str, Any]:
    """Attach stable health identities and deterministic incident action to a report."""
    blocker_codes = sorted({
        item["code"]
        for item in report.get("releaseBlockingFindings", [])
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    })
    keys = [health_key(code, source_key) for code in blocker_codes]
    rollback = bool(report.get("rollbackRequired"))
    quarantine = bool(report.get("quarantineRequired"))
    action = (
        "rollback-and-quarantine" if rollback
        else "quarantine" if quarantine
        else "block-release" if blocker_codes
        else "none"
    )
    report["deduplicationKeys"] = keys
    report["incident"] = {"action": action, "deduplicationKeys": keys}
    report.pop("reportSha256", None)
    report["reportSha256"] = hashlib.sha256(canonical_json(report).encode("utf-8")).hexdigest()
    return report


def _human_coverage(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    audit = report["auditSample"]
    source_rights = report.get("sourceRights", {})
    terms = source_rights.get("termsReview", {}) if isinstance(source_rights, dict) else {}
    parser_rate = report.get("changes", {}).get("parserMalformedRate")
    lines = [
        "",
        "## Evidence coverage",
        f"- Current ingredients: {metrics.get('productsWithCurrentIngredients', 0)} / {metrics.get('products', 0)} ({metrics.get('currentIngredientCoverageFraction', 0):.2%})",
        f"- Current ingredient observations with explicit `observedAt`: {metrics.get('currentIngredientsWithObservedAt', 0)}",
        f"- Current ingredient observations with source revision: {metrics.get('currentIngredientsWithSourceRevision', 0)}",
        f"- Identity confidence: {json.dumps(metrics.get('identityConfidence', {}), sort_keys=True)}",
        f"- Ingredient languages: {json.dumps(metrics.get('ingredientLanguages', {}), sort_keys=True)}",
        f"- Ingredient verification: {json.dumps(metrics.get('ingredientVerificationState', {}), sort_keys=True)}",
        f"- Ingredient capture methods: {json.dumps(metrics.get('ingredientCaptureMethod', {}), sort_keys=True)}",
        f"- Retailer evidence by kind: {json.dumps(metrics.get('retailerEvidenceByKind', {}), sort_keys=True)}",
        f"- Retailer freshness by retailer/type: {json.dumps(metrics.get('retailerFreshnessByRetailerAndKind', {}), sort_keys=True)}",
        f"- Source terms review: `{terms.get('state', 'unknown')}`",
    ]
    if parser_rate is not None:
        lines.append(f"- Parser malformed rate: {parser_rate:.6%}")
    lines += [
        "",
        "## Review sampling",
        f"- Deterministic strata: {len(audit.get('stratified', {}))}",
        f"- Per-stratum sample size: {audit.get('perStratumSize', 0)}",
        f"- Mandatory high-risk review candidates: {audit.get('mandatoryReviewCount', 0)}",
        f"- Mandatory-review list truncated: {str(bool(audit.get('mandatoryReviewTruncated'))).lower()}",
    ]
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate-policy", help="validate the committed quality and source-review policies")
    validate.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    validate.add_argument("--source-reviews", type=Path, default=DEFAULT_SOURCE_REVIEWS)

    evaluate = sub.add_parser("evaluate", help="evaluate a normalized evidence envelope and change report")
    evaluate.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    evaluate.add_argument("--evidence", type=Path, required=True)
    evaluate.add_argument("--change-report", type=Path)
    evaluate.add_argument("--source-key", required=True)
    evaluate.add_argument("--snapshot-id", required=True)
    evaluate.add_argument("--workflow-contract", type=Path, default=DEFAULT_WORKFLOW_CONTRACT)
    evaluate.add_argument("--source-policy", type=Path)
    evaluate.add_argument("--source-reviews", type=Path, default=DEFAULT_SOURCE_REVIEWS)
    evaluate.add_argument("--as-of", help="explicit RFC3339 proposal evaluation time used for freshness and review expiry")
    evaluate.add_argument("--commit-sha", help="exact repository revision represented by generated health artifacts")
    evaluate.add_argument("--output", type=Path, required=True)
    evaluate.add_argument("--summary-output", type=Path, required=True)
    evaluate.add_argument("--defer-blocker-exit", action="store_true", help="write blocked reports successfully so a later workflow step can publish diagnostics before enforcing the gate")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        policy = load_json(args.policy)
        source_reviews = load_json(args.source_reviews)
        validate_policy(policy)
        validate_source_reviews(source_reviews)
        if args.command == "validate-policy":
            print(f"Validated catalog quality policy {policy['policyVersion']} and source-review policy {source_reviews['policyVersion']}")
            return

        evidence = load_json(args.evidence)
        validate_envelope(evidence)
        change = load_json(args.change_report) if args.change_report else None
        workflow_contract = load_json(args.workflow_contract) if args.workflow_contract else None
        source_policy_path = args.source_policy or _source_policy_path(args.source_key)
        source_policy = load_json(source_policy_path) if source_policy_path else None
        as_of = None
        if args.as_of is not None:
            as_of = parse_timestamp(args.as_of)
            if as_of is None:
                raise CatalogQualityError("--as-of must be a timezone-aware RFC3339 timestamp")
        report = evaluate_quality(
            policy=policy,
            envelope=evidence,
            source_key=args.source_key,
            snapshot_id=args.snapshot_id,
            change_report=change,
            workflow_contract=workflow_contract,
            source_policy=source_policy,
            as_of=as_of,
        )
        report = augment_quality_report(report, evidence, change, policy)
        report = enforce_source_review(report, source_reviews, args.source_key)
        report = decorate_incident(report, args.source_key)
        write_json(args.output, report)
        args.summary_output.parent.mkdir(parents=True, exist_ok=True)
        summary = human_summary(report)
        summary += _human_coverage(report)
        summary += "\n## Incident identity\n"
        summary += f"- Action: `{report['incident']['action']}`\n"
        summary += "- Deduplication keys: " + (", ".join(f"`{key}`" for key in report["deduplicationKeys"]) or "none") + "\n"
        args.summary_output.write_text(summary, encoding="utf-8")

        commit_sha = args.commit_sha or os.environ.get("GITHUB_SHA") or "local-fixture"
        health = build_health_report(
            envelope=evidence,
            quality=report,
            change=change,
            benchmark=None,
            evaluated_at=report["evaluatedAt"],
            commit_sha=commit_sha,
        )
        health_json = args.output.parent / "catalog-health.json"
        health_markdown = args.summary_output.parent / "catalog-health.md"
        write_json(health_json, health)
        health_markdown.write_text(health_summary(health), encoding="utf-8")

        print(f"Catalog quality status: {report['status']} ({len(report['releaseBlockingFindings'])} blockers)")
        if report["status"] != "pass" and not args.defer_blocker_exit:
            raise SystemExit(2)
    except (CatalogQualityError, EvidenceValidationError, SourceReviewError) as exc:
        raise SystemExit(f"catalog quality validation failed: {exc}") from exc


if __name__ == "__main__":
    main()
