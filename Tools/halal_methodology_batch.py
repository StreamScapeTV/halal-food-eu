"""Batch methodology analysis for immutable catalog proposal evidence."""
from __future__ import annotations

from collections import Counter
from typing import Any

from halal_methodology_core import MethodologyError, analyze_ingredient, digest, validate_methodology

FRESHNESS_PRECEDENCE = {
    "fresh": 0,
    "refresh-recommended": 1,
    "date-unknown": 2,
    "stale": 3,
    "changed-unreviewed": 4,
}


def _quality_freshness(quality_report: dict[str, Any] | None) -> dict[tuple[str, str], str]:
    states: dict[tuple[str, str], str] = {}
    if quality_report is None:
        return states
    for finding in quality_report.get("warnings", []):
        if not isinstance(finding, dict):
            continue
        code = finding.get("code")
        gtin, market = finding.get("gtin"), finding.get("market")
        if not isinstance(code, str) or not code.startswith("formulation-") or not isinstance(gtin, str) or not isinstance(market, str):
            continue
        state = code.removeprefix("formulation-")
        if state not in FRESHNESS_PRECEDENCE:
            continue
        key = (gtin, market)
        current = states.get(key, "fresh")
        if FRESHNESS_PRECEDENCE[state] > FRESHNESS_PRECEDENCE[current]:
            states[key] = state
    return states


def analyze_envelope(
    *,
    envelope: dict[str, Any],
    methodology: dict[str, Any],
    quality_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Analyze every explicit current selection without inventing a final verdict."""
    validate_methodology(methodology)
    ingredients = {
        item["id"]: item
        for item in envelope.get("ingredients", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    selections = [item for item in envelope.get("currentSelections", []) if isinstance(item, dict)]
    freshness = _quality_freshness(quality_report)
    product_reports: list[dict[str, Any]] = []
    queue_counts: Counter[str] = Counter()
    candidate_counts: Counter[str] = Counter()
    parser_status: Counter[str] = Counter()
    safety_counts: Counter[str] = Counter()

    for selection in sorted(selections, key=lambda item: (str(item.get("gtin", "")), str(item.get("market", "")))):
        gtin, market = selection.get("gtin"), selection.get("market")
        if not isinstance(gtin, str) or not isinstance(market, str):
            raise MethodologyError("current selection lacks GTIN/market")
        ingredient_id = selection.get("ingredientObservationID")
        ingredient = ingredients.get(ingredient_id) if isinstance(ingredient_id, str) else None
        report = analyze_ingredient(
            ingredient,
            methodology,
            gtin=gtin,
            market=market,
            freshness_state=freshness.get((gtin, market), "fresh"),
            conflict_flags=[item for item in selection.get("conflictFlags", []) if isinstance(item, str)],
        )
        product_reports.append(report)
        parser_status[report["parserStatus"]] += 1
        for item in report["reviewQueues"]:
            queue_counts[item["id"]] += 1
        for item in report["candidateFindings"]:
            candidate_counts[item["reasonCode"]] += 1
        for flag in report["safetyFlags"]:
            safety_counts[flag] += 1

    if quality_report is not None:
        quality_products = quality_report.get("metrics", {}).get("products")
        if isinstance(quality_products, int) and quality_products != len(product_reports):
            raise MethodologyError(
                f"quality/product selection count mismatch: quality={quality_products}, methodology={len(product_reports)}"
            )
    report = {
        "schemaVersion": 1,
        "methodologyVersion": methodology["methodologyVersion"],
        "qualityReportSha256": None if quality_report is None else quality_report.get("reportSha256"),
        "counts": {
            "products": len(product_reports),
            "parserStatus": dict(sorted(parser_status.items())),
            "reviewQueues": dict(sorted(queue_counts.items())),
            "candidateReasons": dict(sorted(candidate_counts.items())),
            "safetyFlags": dict(sorted(safety_counts.items())),
        },
        "products": product_reports,
    }
    report["methodologyReportSha256"] = digest(report)
    return report
