"""Deterministic human-audit dimensions and stratified samples for quality reports."""
from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any

from catalog_quality_core import freshness_state

PER_STRATUM_SAMPLE = 3
CHANGE_SAMPLE_LIMIT = 25
MANDATORY_SAMPLE_LIMIT = 1000


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("quality report evaluatedAt must include timezone")
    return parsed.astimezone(timezone.utc)


def _map(envelope: dict[str, Any], name: str) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in envelope.get(name, []) if isinstance(item, dict) and isinstance(item.get("id"), str)}


def _rank(seed: str, stratum: str, record: dict[str, Any]) -> tuple[str, str, str]:
    gtin, market = str(record["gtin"]), str(record["market"])
    digest = hashlib.sha256(f"{seed}\0{stratum}\0{gtin}\0{market}".encode()).hexdigest()
    return digest, gtin, market


def _sample(records: list[dict[str, Any]], seed: str, stratum: str, limit: int) -> list[dict[str, Any]]:
    return sorted(records, key=lambda item: _rank(seed, stratum, item))[:limit]


def _reference(source_key: str | None, gtin: str, source: dict[str, Any] | None) -> str | None:
    if source_key == "open-food-facts":
        return f"https://world.openfoodfacts.org/product/{gtin}"
    reference = source.get("reference") if isinstance(source, dict) else None
    return reference if isinstance(reference, str) and reference.startswith("https://") else None


def augment_quality_report(
    report: dict[str, Any],
    envelope: dict[str, Any],
    change: dict[str, Any] | None,
    policy: dict[str, Any],
) -> dict[str, Any]:
    """Add deterministic coverage dimensions and reviewer-oriented samples."""
    identities = _map(envelope, "identities")
    ingredients = _map(envelope, "ingredients")
    assessments = _map(envelope, "assessments")
    retailers = _map(envelope, "retailerEvidence")
    sources = {item["sourceKey"]: item for item in envelope.get("sources", []) if isinstance(item, dict) and isinstance(item.get("sourceKey"), str)}
    selections = [item for item in envelope.get("currentSelections", []) if isinstance(item, dict)]
    as_of = _timestamp(report["evaluatedAt"])

    identity_confidence = Counter()
    languages = Counter()
    verification = Counter()
    capture = Counter()
    transformation = Counter()
    methodology = Counter()
    with_ingredients = with_observed = with_revision = 0
    candidates: list[dict[str, Any]] = []

    added_keys = {
        (str(item.get("gtin")), str(item.get("market", "DE")))
        for item in (change or {}).get("addedSelections", [])
        if isinstance(item, dict) and item.get("gtin")
    }
    changed_keys = {
        (str(item.get("gtin")), str(item.get("market", "DE")))
        for item in (change or {}).get("reviewQueue", [])
        if isinstance(item, dict) and item.get("gtin")
    }

    for selection in sorted(selections, key=lambda item: (str(item.get("gtin", "")), str(item.get("market", "")))):
        gtin, market = str(selection.get("gtin", "")), str(selection.get("market", ""))
        identity = identities.get(selection.get("identityObservationID"), {})
        ingredient = ingredients.get(selection.get("ingredientObservationID"), {})
        assessment = assessments.get(selection.get("assessmentID"), {})
        identity_confidence[str(identity.get("confidence", "missing"))] += 1
        if ingredient:
            with_ingredients += 1
            languages[str(ingredient.get("languageCode", "missing"))] += 1
            verification[str(ingredient.get("verificationState", "missing"))] += 1
            capture[str(ingredient.get("captureMethod", "missing"))] += 1
            if ingredient.get("observedAt"):
                with_observed += 1
            if ingredient.get("sourceRevision"):
                with_revision += 1
            transformed = ingredient.get("transformation")
            if isinstance(transformed, dict):
                confidence = transformed.get("confidence")
                if isinstance(confidence, (int, float)) and not isinstance(confidence, bool):
                    transformation["verified-confidence" if confidence >= 0.8 else "low-confidence"] += 1
                else:
                    transformation["confidence-unknown"] += 1
            else:
                transformation["none"] += 1
        status = str(assessment.get("status", "unassessed"))
        if assessment:
            methodology[str(assessment.get("methodologyVersion", "missing"))] += 1
        source_key = ingredient.get("sourceKey") or identity.get("sourceKey")
        record = {
            "gtin": gtin,
            "market": market,
            "sourceKey": source_key,
            "sourceRecordID": ingredient.get("sourceRecordID") or identity.get("sourceRecordID"),
            "identityObservationID": selection.get("identityObservationID"),
            "ingredientObservationID": selection.get("ingredientObservationID"),
            "assessmentID": selection.get("assessmentID"),
            "status": status,
            "categories": sorted(identity.get("categories", [])) if isinstance(identity.get("categories"), list) else [],
            "conflictFlags": sorted(selection.get("conflictFlags", [])) if isinstance(selection.get("conflictFlags"), list) else [],
            "certificationIDs": sorted(selection.get("certificationIDs", [])) if isinstance(selection.get("certificationIDs"), list) else [],
            "recordReference": _reference(source_key, gtin, sources.get(source_key)),
            "changeKinds": (["new"] if (gtin, market) in added_keys else []) + (["changed"] if (gtin, market) in changed_keys else []),
        }
        candidates.append(record)

    retailer_by_kind = Counter()
    retailer_by_retailer = Counter()
    retailer_age = defaultdict(Counter)
    retail_policy = policy["freshness"]["retailer"]
    for item in retailers.values():
        kind = str(item.get("kind", "missing"))
        retailer = str(item.get("retailerKey", "missing"))
        retailer_by_kind[kind] += 1
        retailer_by_retailer[retailer] += 1
        anchor = next((item.get(field) for field in retail_policy["anchorFields"] if item.get(field) is not None), None)
        state = freshness_state(anchor, as_of, refresh_months=retail_policy["refreshRecommendedMonths"], stale_months=retail_policy["staleMonths"])
        retailer_age[f"{retailer}|{kind}"][state] += 1

    metrics = report.setdefault("metrics", {})
    metrics["productsWithCurrentIngredients"] = with_ingredients
    metrics["currentIngredientCoverageFraction"] = round(with_ingredients / len(selections), 6) if selections else 0.0
    metrics["currentIngredientsWithObservedAt"] = with_observed
    metrics["currentIngredientsWithSourceRevision"] = with_revision
    metrics["identityConfidence"] = dict(sorted(identity_confidence.items()))
    metrics["ingredientLanguages"] = dict(sorted(languages.items()))
    metrics["ingredientVerificationState"] = dict(sorted(verification.items()))
    metrics["ingredientCaptureMethod"] = dict(sorted(capture.items()))
    metrics["ingredientTransformationConfidence"] = dict(sorted(transformation.items()))
    metrics["assessmentMethodologyVersions"] = dict(sorted(methodology.items()))
    metrics["retailerEvidenceByKind"] = dict(sorted(retailer_by_kind.items()))
    metrics["retailerEvidenceByRetailer"] = dict(sorted(retailer_by_retailer.items()))
    metrics["retailerFreshnessByRetailerAndKind"] = {key: dict(sorted(value.items())) for key, value in sorted(retailer_age.items())}

    if change is not None:
        samples = {}
        for key in ("addedSelections", "removedSelections", "ingredientFieldDeletions", "upstreamMergeSignals", "barcodeChanges", "reviewQueue"):
            values = [item for item in change.get(key, []) if isinstance(item, dict)]
            samples[key] = sorted(values, key=_canonical)[:CHANGE_SAMPLE_LIMIT]
        report.setdefault("changes", {})["samples"] = samples

    strata: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in candidates:
        if record["sourceKey"]:
            strata[f"source:{record['sourceKey']}"] .append(record)
        strata[f"status:{record['status']}"] .append(record)
        for category in record["categories"]:
            strata[f"category:{category}"] .append(record)
        for change_kind in record["changeKinds"]:
            strata[f"change:{change_kind}"] .append(record)
    seed = policy["sampling"]["seed"]
    sample_limit = min(PER_STRATUM_SAMPLE, policy["sampling"]["baseSize"])
    stratified = {
        name: _sample(records, seed, name, sample_limit)
        for name, records in sorted(strata.items())
    }
    mandatory = [
        record for record in candidates
        if record["status"] in {"halal-certified", "halal-reviewed"}
        or record["certificationIDs"]
        or record["conflictFlags"]
        or "changed" in record["changeKinds"]
    ]
    mandatory = sorted(mandatory, key=lambda item: (item["gtin"], item["market"]))
    audit = report.setdefault("auditSample", {})
    audit["perStratumSize"] = sample_limit
    audit["stratified"] = stratified
    audit["mandatoryReviewCount"] = len(mandatory)
    audit["mandatoryReview"] = mandatory[:MANDATORY_SAMPLE_LIMIT]
    audit["mandatoryReviewTruncated"] = len(mandatory) > MANDATORY_SAMPLE_LIMIT
    return report
