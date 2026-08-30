#!/usr/bin/env python3
"""Deterministic release-quality policy for immutable catalog evidence."""
from __future__ import annotations

import calendar
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

STATUSES = {"halal-certified", "halal-reviewed", "not-halal", "questionable", "unknown"}
FRESHNESS = {"fresh", "refresh-recommended", "stale", "date-unknown", "changed-unreviewed"}
CERT_STATES = {"current", "refresh-recommended", "stale-check", "not-effective", "expired", "suspended", "revoked", "date-unknown"}
BLOCKERS = {
    "source-unapproved", "source-policy-missing", "source-license-missing", "source-attribution-missing",
    "source-snapshot-mismatch", "unsafe-positive-inheritance", "positive-with-formulation-conflict",
    "positive-second-review-missing", "certification-invalid", "certification-scope-mismatch",
    "unexpected-count-decrease", "unexpected-count-increase", "parser-error-rate-exceeded", "adapter-contract-invalid",
}
QUARANTINE = {"unsafe-positive-inheritance", "positive-with-formulation-conflict", "certification-invalid", "certification-scope-mismatch", "parser-error-rate-exceeded", "adapter-contract-invalid"}
ROLLBACK = {"unsafe-positive-inheritance", "positive-with-formulation-conflict", "certification-invalid", "certification-scope-mismatch"}


class CatalogQualityError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def validate_policy(p: dict[str, Any]) -> None:
    required = {"schemaVersion", "policyVersion", "market", "freshness", "sourceTrust", "releaseThresholds", "review", "sampling"}
    if set(p) != required or p.get("schemaVersion") != 1:
        raise CatalogQualityError("quality policy has unsupported schema or fields")
    if not isinstance(p["policyVersion"], str) or not p["policyVersion"].strip():
        raise CatalogQualityError("policyVersion must be non-blank")
    if not isinstance(p["market"], str) or len(p["market"]) != 2 or p["market"] != p["market"].upper():
        raise CatalogQualityError("market must be uppercase alpha-2")
    clocks = p["freshness"]
    if not isinstance(clocks, dict) or set(clocks) != {"formulation", "retailer", "certification"}:
        raise CatalogQualityError("freshness clocks are incomplete")
    for name in ("formulation", "certification"):
        c = clocks[name]
        if not isinstance(c, dict) or set(c) != {"anchorField", "refreshRecommendedMonths", "staleMonths"}:
            raise CatalogQualityError(f"invalid {name} freshness clock")
        _validate_months(c, name)
    retail = clocks["retailer"]
    if not isinstance(retail, dict) or set(retail) != {"anchorFields", "refreshRecommendedMonths", "staleMonths"}:
        raise CatalogQualityError("invalid retailer freshness clock")
    if not isinstance(retail["anchorFields"], list) or not retail["anchorFields"] or len(set(retail["anchorFields"])) != len(retail["anchorFields"]):
        raise CatalogQualityError("retailer anchorFields must be a non-empty unique list")
    _validate_months(retail, "retailer")
    if not isinstance(p["sourceTrust"], dict) or not p["sourceTrust"] or any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in p["sourceTrust"].values()):
        raise CatalogQualityError("sourceTrust must contain non-negative integer ranks")
    t = p["releaseThresholds"]
    if not isinstance(t, dict) or set(t) != {"minimumBaselineRecords", "maximumCountDecreaseFraction", "maximumCountIncreaseFraction", "maximumParserErrorFraction"}:
        raise CatalogQualityError("invalid releaseThresholds")
    if not isinstance(t["minimumBaselineRecords"], int) or isinstance(t["minimumBaselineRecords"], bool) or t["minimumBaselineRecords"] < 1:
        raise CatalogQualityError("minimumBaselineRecords must be >= 1")
    for key in ("maximumCountDecreaseFraction", "maximumParserErrorFraction"):
        if not _number_between(t[key], 0, 1):
            raise CatalogQualityError(f"{key} must be within [0,1]")
    if not isinstance(t["maximumCountIncreaseFraction"], (int, float)) or isinstance(t["maximumCountIncreaseFraction"], bool) or t["maximumCountIncreaseFraction"] < 0:
        raise CatalogQualityError("maximumCountIncreaseFraction must be non-negative")
    r = p["review"]
    if not isinstance(r, dict) or set(r) != {"positiveStatusesRequiringIndependentSecondReview", "minimumIndependentReviewers"}:
        raise CatalogQualityError("invalid review policy")
    if not isinstance(r["positiveStatusesRequiringIndependentSecondReview"], list) or any(x not in STATUSES for x in r["positiveStatusesRequiringIndependentSecondReview"]):
        raise CatalogQualityError("review policy contains unknown status")
    if not isinstance(r["minimumIndependentReviewers"], int) or r["minimumIndependentReviewers"] < 2:
        raise CatalogQualityError("minimumIndependentReviewers must be >= 2")
    s = p["sampling"]
    if not isinstance(s, dict) or set(s) != {"seed", "baseSize", "widenedSize", "defectRateThreshold"}:
        raise CatalogQualityError("invalid sampling policy")
    if not isinstance(s["seed"], str) or not s["seed"] or not isinstance(s["baseSize"], int) or not isinstance(s["widenedSize"], int) or s["baseSize"] < 1 or s["widenedSize"] < s["baseSize"] or not _number_between(s["defectRateThreshold"], 0, 1):
        raise CatalogQualityError("invalid sampling values")


def _validate_months(c: dict[str, Any], name: str) -> None:
    a, b = c["refreshRecommendedMonths"], c["staleMonths"]
    if not isinstance(a, int) or not isinstance(b, int) or isinstance(a, bool) or isinstance(b, bool) or a < 1 or b <= a:
        raise CatalogQualityError(f"{name} refresh/stale month thresholds are invalid")


def _number_between(value: Any, low: float, high: float) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and low <= value <= high


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def add_months(value: datetime, months: int) -> datetime:
    index = value.month - 1 + months
    year, month = value.year + index // 12, index % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def freshness_state(anchor: Any, as_of: datetime, *, refresh_months: int, stale_months: int) -> str:
    observed = parse_timestamp(anchor)
    if observed is None:
        return "date-unknown"
    if as_of >= add_months(observed, stale_months):
        return "stale"
    if as_of >= add_months(observed, refresh_months):
        return "refresh-recommended"
    return "fresh"


def deterministic_as_of(e: dict[str, Any], change: dict[str, Any] | None) -> datetime:
    values = [parse_timestamp(x.get("retrievedAt")) for x in e.get("sources", []) if isinstance(x, dict)]
    if change:
        values += [parse_timestamp(change.get(k)) for k in ("retrievedAt", "generatedAt", "createdAt")]
    values = [x for x in values if x is not None]
    if not values:
        raise CatalogQualityError("quality evaluation requires a source retrieval timestamp")
    return max(values)


def _map(e: dict[str, Any], collection: str) -> dict[str, dict[str, Any]]:
    return {x["id"]: x for x in e.get(collection, []) if isinstance(x, dict) and isinstance(x.get("id"), str)}


def _finding(code: str, detail: str, gtin: str | None = None, market: str | None = None, evidence: str | None = None) -> dict[str, Any]:
    x: dict[str, Any] = {"code": code, "detail": detail}
    if gtin is not None:
        x["gtin"] = gtin
    if market is not None:
        x["market"] = market
    if evidence is not None:
        x["evidenceID"] = evidence
    return x


def _approved_reviewers(reviews: list[dict[str, Any]], targets: set[str]) -> set[str]:
    return {str(x["reviewerID"]) for x in reviews if x.get("targetID") in targets and x.get("state") == "approved" and isinstance(x.get("reviewerID"), str) and x["reviewerID"].strip()}


def _active_formulations(e: dict[str, Any], gtin: str, market: str) -> list[dict[str, Any]]:
    items = [x for x in e.get("ingredients", []) if isinstance(x, dict) and x.get("gtin") == gtin and x.get("market") == market]
    superseded = {x.get("supersedesID") for x in items if isinstance(x.get("supersedesID"), str)}
    return [x for x in items if x.get("id") not in superseded]


def _source_rights(source_key: str, contract: dict[str, Any] | None, source_policy: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if source_key == "synthetic-fixture":
        return [], {"approved": True, "fixtureOnly": True, "licenseIdentifier": "synthetic-fixture", "attributionPresent": True}
    findings: list[dict[str, Any]] = []
    registry = next((x for x in (contract or {}).get("sourceRegistry", []) if isinstance(x, dict) and x.get("key") == source_key), None)
    if registry is None or registry.get("enabled") is not True:
        findings.append(_finding("source-unapproved", f"{source_key!r} is not enabled in the reviewed source registry"))
    if source_policy is None:
        findings.append(_finding("source-policy-missing", f"{source_key!r} has no reviewed source policy"))
        return findings, {"approved": False, "fixtureOnly": False, "licenseIdentifier": None, "attributionPresent": False}
    if source_policy.get("sourceKey") != source_key:
        findings.append(_finding("adapter-contract-invalid", "source policy key does not match quality source key"))
    license_info = source_policy.get("databaseLicense")
    license_id = license_info.get("identifier") if isinstance(license_info, dict) else None
    attribution = source_policy.get("attribution")
    if not isinstance(license_id, str) or not license_id.strip():
        findings.append(_finding("source-license-missing", f"{source_key!r} has no database license identifier"))
    if not isinstance(attribution, str) or not attribution.strip():
        findings.append(_finding("source-attribution-missing", f"{source_key!r} has no attribution text"))
    return findings, {"approved": not any(x["code"] in {"source-unapproved", "source-policy-missing"} for x in findings), "fixtureOnly": False, "licenseIdentifier": license_id, "attributionPresent": isinstance(attribution, str) and bool(attribution.strip())}


def _cert_state(cert: dict[str, Any], as_of: datetime, p: dict[str, Any]) -> str:
    for field, state in (("revokedAt", "revoked"), ("suspendedAt", "suspended")):
        when = parse_timestamp(cert.get(field))
        if when is not None and when <= as_of:
            return state
    effective, expiry = parse_timestamp(cert.get("effectiveAt")), parse_timestamp(cert.get("expiryAt"))
    if effective is not None and effective > as_of:
        return "not-effective"
    if expiry is not None and expiry < as_of:
        return "expired"
    c = p["freshness"]["certification"]
    state = freshness_state(cert.get(c["anchorField"]), as_of, refresh_months=c["refreshRecommendedMonths"], stale_months=c["staleMonths"])
    return "current" if state == "fresh" else "stale-check" if state == "stale" else state


def _retailer_state(record: dict[str, Any], as_of: datetime, p: dict[str, Any]) -> str:
    c = p["freshness"]["retailer"]
    anchor = next((record.get(k) for k in c["anchorFields"] if record.get(k) is not None), None)
    return freshness_state(anchor, as_of, refresh_months=c["refreshRecommendedMonths"], stale_months=c["staleMonths"])


def _change_findings(change: dict[str, Any] | None, p: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if change is None:
        return [], {"available": False}
    summary = {"available": True, "baseline": change.get("baseline"), "additions": int(change.get("additions", 0) or 0), "formulationChanges": int(change.get("formulationChanges", 0) or 0), "removals": int(change.get("removals", 0) or 0), "statusChanges": change.get("statusChanges", []), "ingredientFieldDeletions": len(change.get("ingredientFieldDeletions", []) or []), "upstreamMergeSignals": len(change.get("upstreamMergeSignals", []) or []), "barcodeChanges": len(change.get("barcodeChanges", []) or []), "reviewQueueCount": len(change.get("reviewQueue", []) or [])}
    findings: list[dict[str, Any]] = []
    if change.get("noCompletenessClaim") is not True or not isinstance(change.get("reviewQueue"), list):
        findings.append(_finding("adapter-contract-invalid", "change report must prohibit completeness claims and expose a reviewQueue array"))
    parser = change.get("parserQuality")
    if isinstance(parser, dict):
        rate = parser.get("malformedRate")
        if isinstance(rate, (int, float)) and not isinstance(rate, bool):
            summary["parserMalformedRate"] = float(rate)
            if rate > p["releaseThresholds"]["maximumParserErrorFraction"]:
                findings.append(_finding("parser-error-rate-exceeded", f"malformed input rate {rate:.6f} exceeds policy threshold"))
        if int(parser.get("schemaErrors", 0) or 0) > 0:
            findings.append(_finding("adapter-contract-invalid", "source adapter reported schema errors"))
    if change.get("baseline") not in {None, "none"}:
        current = summary["additions"] + int(change.get("unchanged", 0) or 0) + summary["formulationChanges"]
        previous = current - summary["additions"] + summary["removals"]
        summary.update({"currentSourceRecordCount": current, "previousSourceRecordCount": previous})
        t = p["releaseThresholds"]
        if previous >= t["minimumBaselineRecords"]:
            delta = current - previous
            if delta < 0 and -delta / previous > t["maximumCountDecreaseFraction"]:
                findings.append(_finding("unexpected-count-decrease", f"source record count decreased from {previous} to {current}"))
            if delta > 0 and delta / previous > t["maximumCountIncreaseFraction"]:
                findings.append(_finding("unexpected-count-increase", f"source record count increased from {previous} to {current}"))
    return findings, summary


def _sample(selections: list[dict[str, Any]], seed: str, size: int) -> list[dict[str, str]]:
    def rank(x: dict[str, Any]) -> tuple[str, str, str]:
        gtin, market = str(x.get("gtin", "")), str(x.get("market", ""))
        return hashlib.sha256(f"{seed}\0{gtin}\0{market}".encode()).hexdigest(), gtin, market
    return [{"gtin": str(x.get("gtin", "")), "market": str(x.get("market", ""))} for x in sorted(selections, key=rank)[:size]]


def evaluate_quality(*, policy: dict[str, Any], envelope: dict[str, Any], source_key: str, snapshot_id: str, change_report: dict[str, Any] | None = None, workflow_contract: dict[str, Any] | None = None, source_policy: dict[str, Any] | None = None, as_of: datetime | None = None) -> dict[str, Any]:
    """Evaluate an envelope after the canonical evidence validator has accepted it."""
    validate_policy(policy)
    if change_report is not None and (change_report.get("sourceKey") != source_key or change_report.get("snapshotID") != snapshot_id):
        raise CatalogQualityError("change report source/snapshot does not match quality inputs")
    as_of = (as_of or deterministic_as_of(envelope, change_report)).astimezone(timezone.utc)
    sources = {x["sourceKey"]: x for x in envelope.get("sources", []) if isinstance(x, dict) and isinstance(x.get("sourceKey"), str)}
    identities, ingredients, certs, assessments, retailers = (_map(envelope, x) for x in ("identities", "ingredients", "certifications", "assessments", "retailerEvidence"))
    reviews = [x for x in envelope.get("reviews", []) if isinstance(x, dict)]
    selections = [x for x in envelope.get("currentSelections", []) if isinstance(x, dict)]
    blocking: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    rights_findings, rights = _source_rights(source_key, workflow_contract, source_policy)
    blocking += [x for x in rights_findings if x["code"] in BLOCKERS]
    warnings += [x for x in rights_findings if x["code"] not in BLOCKERS]
    if source_key != "synthetic-fixture":
        source = sources.get(source_key)
        if source is None:
            blocking.append(_finding("adapter-contract-invalid", f"normalized evidence lacks source {source_key!r}"))
        elif source.get("sourceSnapshotID") != snapshot_id:
            blocking.append(_finding("source-snapshot-mismatch", "normalized source snapshot does not match workflow snapshot"))

    formulation, retail_age, cert_state, assessment_state = Counter(), Counter(), Counter(), Counter()
    source_classes = Counter(x.get("sourceClass") for x in sources.values() if isinstance(x.get("sourceClass"), str))
    review_states = Counter(x.get("state") for x in reviews if isinstance(x.get("state"), str))
    high_risk: set[tuple[str, str]] = set()
    per_product: Counter[tuple[str, str]] = Counter()
    missing = conflicts = second_review = machine = 0
    positives = set(policy["review"]["positiveStatusesRequiringIndependentSecondReview"])
    form_clock = policy["freshness"]["formulation"]

    for sel in sorted(selections, key=lambda x: (str(x.get("gtin", "")), str(x.get("market", "")))):
        gtin, market = str(sel.get("gtin", "")), str(sel.get("market", ""))
        key = (gtin, market)
        identity = identities.get(sel.get("identityObservationID"))
        ingredient = ingredients.get(sel.get("ingredientObservationID"))
        assessment = assessments.get(sel.get("assessmentID"))
        if assessment is not None and isinstance(assessment.get("status"), str):
            assessment_state[assessment["status"]] += 1
        if ingredient is None:
            missing += 1
            formulation["date-unknown"] += 1
            high_risk.add(key)
            per_product[key] += 1
            warnings.append(_finding("ingredients-missing", "current selection has no ingredient observation", gtin, market))
        else:
            state = freshness_state(ingredient.get(form_clock["anchorField"]), as_of, refresh_months=form_clock["refreshRecommendedMonths"], stale_months=form_clock["staleMonths"])
            targets = {str(ingredient.get("id"))}
            if assessment is not None and assessment.get("ingredientObservationID") == ingredient.get("id"):
                targets.add(str(assessment.get("id")))
            if ingredient.get("supersedesID") and not _approved_reviewers(reviews, targets):
                state = "changed-unreviewed"
            formulation[state] += 1
            if state != "fresh":
                warnings.append(_finding(f"formulation-{state}", f"formulation freshness state is {state}", gtin, market, ingredient.get("id")))
                high_risk.add(key)
                per_product[key] += 1
            if ingredient.get("captureMethod") == "ocr" or ingredient.get("verificationState") in {"unverified", "machine-assisted"}:
                machine += 1
            active_hashes = {x.get("contentHash") for x in _active_formulations(envelope, gtin, market) if isinstance(x.get("contentHash"), str)}
            if len(active_hashes) > 1 or bool(sel.get("conflictFlags")):
                conflicts += 1
                high_risk.add(key)
                per_product[key] += 1
                warnings.append(_finding("formulation-conflict", "active formulation evidence conflicts or declares a conflict", gtin, market))
                if assessment is not None and assessment.get("status") in positives:
                    blocking.append(_finding("positive-with-formulation-conflict", "positive assessment cannot remain current while formulation evidence conflicts", gtin, market, assessment.get("id")))
                    per_product[key] += 1
            if assessment is not None and assessment.get("ingredientObservationID") != ingredient.get("id") and assessment.get("status") in positives:
                blocking.append(_finding("unsafe-positive-inheritance", "positive assessment is not bound to the selected formulation", gtin, market, assessment.get("id")))
                high_risk.add(key)
                per_product[key] += 1
        if assessment is not None and assessment.get("status") in positives:
            reviewers = _approved_reviewers(reviews, {str(assessment.get("id"))})
            synthetic = bool(identity and isinstance(sources.get(identity.get("sourceKey")), dict) and sources[identity["sourceKey"]].get("sourceClass") == "synthetic")
            required = 1 if synthetic else policy["review"]["minimumIndependentReviewers"]
            if len(reviewers) < required:
                second_review += 1
                blocking.append(_finding("positive-second-review-missing", f"positive assessment has {len(reviewers)} independent approved reviewer(s); requires {required}", gtin, market, assessment.get("id")))
                high_risk.add(key)
                per_product[key] += 1
        for cert_id in [x for x in (sel.get("certificationIDs") or []) if isinstance(x, str)]:
            cert = certs.get(cert_id)
            if cert is None:
                blocking.append(_finding("certification-invalid", "selected certification reference is missing", gtin, market, cert_id))
                high_risk.add(key)
                per_product[key] += 1
                continue
            state = _cert_state(cert, as_of, policy)
            cert_state[state] += 1
            if cert.get("gtin") != gtin or cert.get("market") != market:
                blocking.append(_finding("certification-scope-mismatch", "certification does not match exact GTIN/market", gtin, market, cert_id))
                high_risk.add(key)
                per_product[key] += 1
            if state in {"expired", "revoked", "suspended", "not-effective", "date-unknown", "stale-check"}:
                warnings.append(_finding(f"certification-{state}", f"certification state is {state}", gtin, market, cert_id))
                high_risk.add(key)
                per_product[key] += 1
                if assessment is not None and assessment.get("status") == "halal-certified":
                    blocking.append(_finding("certification-invalid", f"halal-certified assessment depends on certification in state {state}", gtin, market, cert_id))
                    per_product[key] += 1
    for record in retailers.values():
        retail_age[_retailer_state(record, as_of, policy)] += 1
    change_findings, changes = _change_findings(change_report, policy)
    blocking += [x for x in change_findings if x["code"] in BLOCKERS]
    warnings += [x for x in change_findings if x["code"] not in BLOCKERS]

    def unique(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_json = {canonical_json(x): x for x in items}
        return [by_json[k] for k in sorted(by_json)]

    blocking, warnings = unique(blocking), unique(warnings)
    sample_policy = policy["sampling"]
    base = _sample(selections, sample_policy["seed"], sample_policy["baseSize"])
    sample_keys = {(x["gtin"], x["market"]) for x in base}
    defect_rate = sum(1 for x in sample_keys if per_product[x] > 0) / len(sample_keys) if sample_keys else 0.0
    escalated = defect_rate > sample_policy["defectRateThreshold"]
    selected_sample = _sample(selections, sample_policy["seed"], sample_policy["widenedSize"] if escalated else sample_policy["baseSize"])
    codes = {x["code"] for x in blocking}
    report = {
        "schemaVersion": 1,
        "policyVersion": policy["policyVersion"],
        "sourceKey": source_key,
        "snapshotID": snapshot_id,
        "evaluatedAt": as_of.isoformat().replace("+00:00", "Z"),
        "status": "blocked" if blocking else "pass",
        "quarantineRequired": bool(codes & QUARANTINE),
        "rollbackRequired": bool(codes & ROLLBACK),
        "releaseBlockingFindings": blocking,
        "warnings": warnings,
        "sourceRights": rights,
        "metrics": {
            "products": len(selections),
            "sources": len(sources),
            "sourceTrustClasses": dict(sorted(source_classes.items())),
            "sourceTrust": {k: {"sourceClass": v.get("sourceClass"), "trustScore": policy["sourceTrust"].get(v.get("sourceClass"), 0)} for k, v in sorted(sources.items())},
            "reviewState": dict(sorted(review_states.items())),
            "retailerEvidenceRecords": len(retailers),
            "certificationRecords": len(certs),
            "formulationFreshness": {x: formulation[x] for x in sorted(FRESHNESS)},
            "retailerFreshness": {x: retail_age[x] for x in sorted(FRESHNESS - {"changed-unreviewed"})},
            "certificationState": {x: cert_state[x] for x in sorted(CERT_STATES)},
            "assessmentStatus": {x: assessment_state[x] for x in sorted(STATUSES)},
            "missingIngredientSelections": missing,
            "formulationConflicts": conflicts,
            "positiveSecondReviewDeficits": second_review,
            "ocrOrMachineAssistedIngredients": machine,
            "assessmentValidityEvents": len(envelope.get("validityEvents", [])),
        },
        "changes": changes,
        "auditSample": {
            "seed": sample_policy["seed"],
            "base": base,
            "highRisk": [{"gtin": g, "market": m} for g, m in sorted(high_risk)],
            "defectRate": round(defect_rate, 6),
            "escalated": escalated,
            "selected": selected_sample,
        },
    }
    report["reportSha256"] = hashlib.sha256(canonical_json(report).encode()).hexdigest()
    return report


def human_summary(report: dict[str, Any]) -> str:
    m, c = report["metrics"], report["changes"]
    lines = [
        "# Catalog quality report",
        "",
        f"- Status: **{report['status']}**",
        f"- Policy: `{report['policyVersion']}`",
        f"- Source/snapshot: `{report['sourceKey']}` / `{report['snapshotID']}`",
        f"- Evaluation time: `{report['evaluatedAt']}`",
        f"- Products: {m['products']}",
        f"- Formulation freshness: {json.dumps(m['formulationFreshness'], sort_keys=True)}",
        f"- Formulation conflicts: {m['formulationConflicts']}",
        f"- Missing ingredient selections: {m['missingIngredientSelections']}",
        f"- Certification states: {json.dumps(m['certificationState'], sort_keys=True)}",
        f"- Independent positive-review deficits: {m['positiveSecondReviewDeficits']}",
        f"- Changes: additions={c.get('additions', 0)}, formulation={c.get('formulationChanges', 0)}, removals={c.get('removals', 0)}, review-queue={c.get('reviewQueueCount', 0)}",
        f"- Audit sample: {len(report['auditSample']['selected'])} selected; escalation={str(report['auditSample']['escalated']).lower()}",
        f"- Quarantine required: {str(report['quarantineRequired']).lower()}",
        f"- Rollback required: {str(report['rollbackRequired']).lower()}",
        "",
        "## Release-blocking findings",
    ]
    lines += [f"- `{x['code']}`" + (f" ({x['gtin']} {x.get('market', '')})" if x.get("gtin") else "") + f": {x['detail']}" for x in report["releaseBlockingFindings"]] or ["- None."]
    lines += ["", "## Warnings"]
    lines += [f"- `{x['code']}`" + (f" ({x['gtin']} {x.get('market', '')})" if x.get("gtin") else "") + f": {x['detail']}" for x in report["warnings"]] or ["- None."]
    return "\n".join(lines) + "\n"
