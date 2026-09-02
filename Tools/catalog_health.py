#!/usr/bin/env python3
"""Build deterministic, evidence-labelled catalog health reports.

This is a reporting layer. It does not make halal decisions, approve sources,
admit certifiers, or infer retailer completeness. Strong retailer completeness
claims require a separate reviewed coverage gate supplied by the quality layer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CLAIM_STATES = {
    "no-evidence",
    "community-only",
    "observational-partial",
    "official-partial",
    "official-complete-snapshot",
    "degraded",
}
ASSESSMENT_STATUSES = {
    "halal-certified", "halal-reviewed", "not-halal", "questionable", "unknown", "unassessed",
}
DEFAULT_RETAILERS = ("rewe", "lidl")


class CatalogHealthError(ValueError):
    pass


def _load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogHealthError(f"failed to load JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogHealthError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: dict[str, Any]) -> str:
    copy = dict(value)
    copy.pop("reportSha256", None)
    return hashlib.sha256(_canonical(copy).encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _map(envelope: dict[str, Any], name: str) -> dict[str, dict[str, Any]]:
    return {
        item["id"]: item
        for item in envelope.get(name, [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _retailer_bucket(record: dict[str, Any]) -> str:
    kind = str(record.get("kind", "")).strip().lower()
    evidence_class = str(record.get("evidenceClass", "")).strip().lower()
    if evidence_class == "official" or kind in {"official-feed", "official-listing", "official-product-listing"}:
        return "official"
    if "community" in kind or evidence_class == "community":
        return "community"
    if "observation" in kind or evidence_class in {"observational", "observation"}:
        return "observational"
    return "other"


def _coverage_gate(quality: dict[str, Any] | None, retailer: str) -> dict[str, Any] | None:
    gates = (quality or {}).get("retailerCoverageGates")
    if not isinstance(gates, dict):
        return None
    gate = gates.get(retailer)
    if not isinstance(gate, dict):
        return None
    denominator = gate.get("denominator")
    valid_denominator = isinstance(denominator, int) and not isinstance(denominator, bool) and denominator >= 0
    if (
        gate.get("state") == "pass"
        and gate.get("claimState") == "official-complete-snapshot"
        and gate.get("denominatorReconciled") is True
        and valid_denominator
        and isinstance(gate.get("snapshotID"), str)
        and bool(gate["snapshotID"].strip())
    ):
        return gate
    return None


def _retailer_claim(
    records: list[dict[str, Any]],
    *,
    degraded: bool,
    coverage_gate: dict[str, Any] | None,
) -> tuple[str, int | None, str | None]:
    buckets = Counter(_retailer_bucket(item) for item in records)
    if degraded and records:
        return "degraded", None, None
    if buckets["official"] and coverage_gate is not None:
        return "official-complete-snapshot", int(coverage_gate["denominator"]), str(coverage_gate["snapshotID"])
    if buckets["official"]:
        return "official-partial", None, None
    if buckets["observational"]:
        return "observational-partial", None, None
    if buckets["community"]:
        return "community-only", None, None
    return "no-evidence", None, None


def _certificate_state(cert: dict[str, Any], evaluated_at: datetime) -> str:
    for field, state in (("revokedAt", "revoked"), ("suspendedAt", "suspended")):
        value = _timestamp(cert.get(field))
        if value is not None and value <= evaluated_at:
            return state
    effective = _timestamp(cert.get("effectiveAt"))
    if effective is not None and effective > evaluated_at:
        return "not-effective"
    expiry = _timestamp(cert.get("expiryAt"))
    if expiry is not None and expiry < evaluated_at:
        return "expired"
    if _timestamp(cert.get("lastCheckedAt")) is None:
        return "check-date-unknown"
    return "active"


def _quality_codes(quality: dict[str, Any] | None, key: str) -> list[str]:
    return sorted({
        str(item.get("code"))
        for item in (quality or {}).get(key, [])
        if isinstance(item, dict) and isinstance(item.get("code"), str)
    })


def build_health_report(
    *,
    envelope: dict[str, Any],
    quality: dict[str, Any] | None,
    change: dict[str, Any] | None,
    benchmark: dict[str, Any] | None,
    evaluated_at: str,
    commit_sha: str,
) -> dict[str, Any]:
    instant = _timestamp(evaluated_at)
    if instant is None:
        raise CatalogHealthError("evaluatedAt must be a timezone-aware RFC3339 timestamp")
    if not isinstance(commit_sha, str) or len(commit_sha) < 7:
        raise CatalogHealthError("commitSha must identify the evaluated repository revision")

    identities = _map(envelope, "identities")
    ingredients = _map(envelope, "ingredients")
    assessments = _map(envelope, "assessments")
    certifications = _map(envelope, "certifications")
    retailer_evidence = _map(envelope, "retailerEvidence")
    selections = [item for item in envelope.get("currentSelections", []) if isinstance(item, dict)]

    markets: Counter[str] = Counter()
    brands: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    source_products: defaultdict[str, set[tuple[str, str]]] = defaultdict(set)
    languages: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    linked_retailer_ids: set[str] = set()
    linked_certificate_ids: set[str] = set()
    conflicts = current_ingredients = 0

    for selection in selections:
        gtin = str(selection.get("gtin", ""))
        market = str(selection.get("market", "unknown"))
        markets[market] += 1
        identity = identities.get(selection.get("identityObservationID"), {})
        ingredient = ingredients.get(selection.get("ingredientObservationID"), {})
        assessment = assessments.get(selection.get("assessmentID"), {})
        if identity.get("brand"):
            brands[str(identity["brand"])] += 1
        for category in identity.get("categories", []) if isinstance(identity.get("categories"), list) else []:
            categories[str(category)] += 1
        source_key = ingredient.get("sourceKey") or identity.get("sourceKey")
        if isinstance(source_key, str) and source_key:
            source_products[source_key].add((gtin, market))
        if ingredient:
            current_ingredients += 1
            languages[str(ingredient.get("languageCode", "missing"))] += 1
        status = str(assessment.get("status", "unassessed"))
        statuses[status if status in ASSESSMENT_STATUSES else "unknown"] += 1
        if selection.get("conflictFlags"):
            conflicts += 1
        linked_retailer_ids.update(item for item in selection.get("retailerEvidenceIDs", []) if isinstance(item, str))
        linked_certificate_ids.update(item for item in selection.get("certificationIDs", []) if isinstance(item, str))

    retailer_records: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for evidence_id in sorted(linked_retailer_ids):
        item = retailer_evidence.get(evidence_id)
        if item:
            retailer = str(item.get("retailerKey", "unknown")).strip().lower() or "unknown"
            retailer_records[retailer].append(item)

    quality_metrics = (quality or {}).get("metrics", {}) if isinstance((quality or {}).get("metrics", {}), dict) else {}
    quality_retailer_freshness = quality_metrics.get("retailerFreshnessByRetailerAndKind", {})
    retailer_health: dict[str, Any] = {}
    for retailer in sorted(set(DEFAULT_RETAILERS) | set(retailer_records)):
        records = retailer_records.get(retailer, [])
        degraded = False
        if isinstance(quality_retailer_freshness, dict):
            for key, states in quality_retailer_freshness.items():
                if isinstance(key, str) and key.split("|", 1)[0].lower() == retailer and isinstance(states, dict):
                    if states.get("stale", 0) or states.get("date-unknown", 0):
                        degraded = any(_retailer_bucket(item) == "official" for item in records)
        claim, denominator, snapshot_id = _retailer_claim(
            records,
            degraded=degraded,
            coverage_gate=_coverage_gate(quality, retailer),
        )
        buckets = Counter(_retailer_bucket(item) for item in records)
        retailer_health[retailer] = {
            "claimState": claim,
            "denominator": denominator,
            "completeSnapshotID": snapshot_id,
            "coverageGatePresent": _coverage_gate(quality, retailer) is not None,
            "evidenceCounts": {key: buckets[key] for key in ("official", "observational", "community", "other")},
            "latestEvidenceAt": max(
                (value for item in records for value in (item.get("observedAt"), item.get("retrievedAt")) if isinstance(value, str)),
                default=None,
            ),
        }

    certificate_states = Counter()
    for cert_id in sorted(linked_certificate_ids):
        cert = certifications.get(cert_id)
        if cert:
            certificate_states[_certificate_state(cert, instant)] += 1
    unmatched_certificates = len(set(certifications) - linked_certificate_ids)

    source_health = []
    for source in sorted(
        (item for item in envelope.get("sources", []) if isinstance(item, dict)),
        key=lambda item: str(item.get("sourceKey", "")),
    ):
        key = str(source.get("sourceKey", "unknown"))
        source_health.append({
            "sourceKey": key,
            "sourceClass": source.get("sourceClass"),
            "retrievedAt": source.get("retrievedAt"),
            "currentProductCount": len(source_products.get(key, set())),
            "licenseIdentifier": source.get("licenseIdentifier"),
            "attributionPresent": bool(source.get("attribution") or source.get("attributionText")),
        })

    quality_changes = (quality or {}).get("changes", {}) if isinstance((quality or {}).get("changes"), dict) else {}
    change_summary = {
        "available": change is not None,
        "baseline": (change or {}).get("baseline"),
        "additions": int((change or {}).get("additions", 0) or 0),
        "formulationChanges": int((change or {}).get("formulationChanges", 0) or 0),
        "removals": int((change or {}).get("removals", 0) or 0),
        "reviewQueueCount": len((change or {}).get("reviewQueue", []) or []),
        "noCompletenessClaim": (change or {}).get("noCompletenessClaim") is True if change is not None else True,
        "previousSourceRecordCount": quality_changes.get("previousSourceRecordCount"),
        "currentSourceRecordCount": quality_changes.get("currentSourceRecordCount"),
    }

    runtime = {
        "available": benchmark is not None,
        "sqliteBytes": None,
        "queryLatencyP95Ms": None,
        "buildDurationSeconds": None,
        "manifestDigest": None,
    }
    if benchmark:
        runtime["sqliteBytes"] = benchmark.get("sqliteBytes") or benchmark.get("databaseBytes")
        latency = benchmark.get("queryLatencyMs")
        if isinstance(latency, dict):
            runtime["queryLatencyP95Ms"] = latency.get("p95")
        runtime["buildDurationSeconds"] = benchmark.get("buildDurationSeconds")
        runtime["manifestDigest"] = benchmark.get("manifestDigest") or benchmark.get("manifestSha256")

    report: dict[str, Any] = {
        "schemaVersion": 1,
        "evaluatedAt": evaluated_at,
        "commitSha": commit_sha,
        "scope": {
            "markets": sorted(markets),
            "retailerCompletenessRule": "reviewed-coverage-gate-and-denominator-reconciliation",
            "claimsAreEvidenceLabels": True,
        },
        "products": {
            "uniqueCurrentSelections": len(selections),
            "byMarket": dict(sorted(markets.items())),
            "byBrand": dict(sorted(brands.items())),
            "byCategory": dict(sorted(categories.items())),
            "bySource": {key: len(value) for key, value in sorted(source_products.items())},
            "withCurrentIngredients": current_ingredients,
            "missingCurrentIngredients": max(0, len(selections) - current_ingredients),
            "ingredientLanguages": dict(sorted(languages.items())),
            "conflictedSelections": conflicts,
        },
        "freshness": {
            "formulation": quality_metrics.get("formulationFreshness", {}),
            "retailer": quality_metrics.get("retailerFreshness", {}),
            "certification": quality_metrics.get("certificationFreshness", {}),
        },
        "assessments": {
            "currentStatusCounts": dict(sorted(statuses.items())),
            "methodologyVersions": quality_metrics.get("assessmentMethodologyVersions", {}),
            "invalidatedOrBlockingCodes": _quality_codes(quality, "releaseBlockingFindings"),
            "warningCodes": _quality_codes(quality, "warnings"),
        },
        "certifications": {
            "linkedCurrentCertificateCount": len(linked_certificate_ids),
            "states": dict(sorted(certificate_states.items())),
            "unmatchedStoredCertificateCount": unmatched_certificates,
        },
        "retailers": retailer_health,
        "sources": source_health,
        "changes": change_summary,
        "buildRuntime": runtime,
        "qualityGate": {
            "available": quality is not None,
            "status": (quality or {}).get("status", "unknown"),
            "releaseBlockingFindingCount": len((quality or {}).get("releaseBlockingFindings", []) or []),
            "incident": (quality or {}).get("incident"),
            "deduplicationKeys": sorted((quality or {}).get("deduplicationKeys", []) or []),
        },
    }
    report["reportSha256"] = _digest(report)
    validate_health_report(report)
    return report


def validate_health_report(report: dict[str, Any]) -> None:
    required = {
        "schemaVersion", "evaluatedAt", "commitSha", "scope", "products", "freshness",
        "assessments", "certifications", "retailers", "sources", "changes", "buildRuntime",
        "qualityGate", "reportSha256",
    }
    if set(report) != required or report.get("schemaVersion") != 1:
        raise CatalogHealthError("catalog health report has unsupported schema or fields")
    if _timestamp(report.get("evaluatedAt")) is None:
        raise CatalogHealthError("catalog health evaluatedAt is invalid")
    if report.get("reportSha256") != _digest(report):
        raise CatalogHealthError("catalog health report digest mismatch")
    retailers = report.get("retailers")
    if not isinstance(retailers, dict):
        raise CatalogHealthError("retailers must be an object")
    for retailer, value in retailers.items():
        if not isinstance(retailer, str) or not isinstance(value, dict) or value.get("claimState") not in CLAIM_STATES:
            raise CatalogHealthError("retailer health contains an invalid claim state")
        if value.get("claimState") == "official-complete-snapshot":
            if value.get("denominator") is None or value.get("coverageGatePresent") is not True or not value.get("completeSnapshotID"):
                raise CatalogHealthError("complete retailer coverage requires a passing reviewed coverage gate")


def human_summary(report: dict[str, Any]) -> str:
    products = report["products"]
    lines = [
        "# Catalog health",
        "",
        f"- Evaluated commit: `{report['commitSha']}`",
        f"- Evaluated at: `{report['evaluatedAt']}`",
        f"- Current products: {products['uniqueCurrentSelections']}",
        f"- Current exact ingredient coverage: {products['withCurrentIngredients']} / {products['uniqueCurrentSelections']}",
        f"- Conflicted selections: {products['conflictedSelections']}",
        f"- Quality gate: `{report['qualityGate']['status']}`",
        "",
        "## Halal review health",
        f"- Current statuses: `{json.dumps(report['assessments']['currentStatusCounts'], sort_keys=True)}`",
        f"- Certification states: `{json.dumps(report['certifications']['states'], sort_keys=True)}`",
        f"- Blocking findings: `{json.dumps(report['assessments']['invalidatedOrBlockingCodes'])}`",
        "",
        "## Retailer evidence claims",
    ]
    for retailer, value in sorted(report["retailers"].items()):
        denominator = "unknown" if value["denominator"] is None else str(value["denominator"])
        lines.append(
            f"- {retailer}: `{value['claimState']}`; denominator={denominator}; "
            f"evidence={json.dumps(value['evidenceCounts'], sort_keys=True)}"
        )
    lines += [
        "",
        "Retailer claim states describe the evidence corpus only. They do not imply nationwide/current stock unless a separate reviewed official coverage gate passes with a reconciled denominator.",
        "",
        "## Change and runtime health",
        f"- Change summary: `{json.dumps(report['changes'], sort_keys=True)}`",
        f"- Runtime metrics available: `{str(report['buildRuntime']['available']).lower()}`",
        f"- Report SHA-256: `{report['reportSha256']}`",
        "",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build", help="build catalog-health JSON and Markdown")
    build.add_argument("--evidence", type=Path, required=True)
    build.add_argument("--quality-report", type=Path)
    build.add_argument("--change-report", type=Path)
    build.add_argument("--benchmark-report", type=Path)
    build.add_argument("--evaluated-at", required=True)
    build.add_argument("--commit-sha", required=True)
    build.add_argument("--output", type=Path, required=True)
    build.add_argument("--markdown-output", type=Path, required=True)
    validate = sub.add_parser("validate", help="validate an existing catalog-health JSON report")
    validate.add_argument("--input", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.command == "validate":
            report = _load_json(args.input)
            assert report is not None
            validate_health_report(report)
            print(f"Validated catalog health report {report['reportSha256']}")
            return
        envelope = _load_json(args.evidence)
        assert envelope is not None
        report = build_health_report(
            envelope=envelope,
            quality=_load_json(args.quality_report),
            change=_load_json(args.change_report),
            benchmark=_load_json(args.benchmark_report),
            evaluated_at=args.evaluated_at,
            commit_sha=args.commit_sha,
        )
        _write_json(args.output, report)
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(human_summary(report), encoding="utf-8")
        print(f"Catalog health: {report['products']['uniqueCurrentSelections']} products, quality={report['qualityGate']['status']}")
    except CatalogHealthError as exc:
        raise SystemExit(f"catalog health failed: {exc}") from exc


if __name__ == "__main__":
    main()
