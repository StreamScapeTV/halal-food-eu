"""Versioned source-terms review checks for the catalog quality gate."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class SourceReviewError(ValueError):
    pass


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise SourceReviewError(f"{field} must be a non-blank RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SourceReviewError(f"{field} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise SourceReviewError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_source_reviews(raw: dict[str, Any]) -> None:
    if set(raw) != {"schemaVersion", "policyVersion", "sources"} or raw.get("schemaVersion") != 1:
        raise SourceReviewError("source-review policy has unsupported schema or fields")
    if not isinstance(raw.get("policyVersion"), str) or not raw["policyVersion"].strip():
        raise SourceReviewError("source-review policyVersion must be non-blank")
    sources = raw.get("sources")
    if not isinstance(sources, dict) or not sources:
        raise SourceReviewError("source-review sources must be a non-empty object")
    for source_key, review in sources.items():
        if not isinstance(source_key, str) or not source_key or not isinstance(review, dict):
            raise SourceReviewError("source-review entries must have non-blank source keys and object values")
        required = {"state", "reviewedAt", "expiresAt", "licenseIdentifiers", "scope"}
        if set(review) != required:
            raise SourceReviewError(f"source review {source_key!r} fields mismatch")
        if review["state"] not in {"approved", "revoked"}:
            raise SourceReviewError(f"source review {source_key!r} has unsupported state")
        reviewed = _timestamp(review["reviewedAt"], f"{source_key}.reviewedAt")
        expires = _timestamp(review["expiresAt"], f"{source_key}.expiresAt")
        if expires <= reviewed:
            raise SourceReviewError(f"source review {source_key!r} expiry must follow review time")
        licenses = review["licenseIdentifiers"]
        if not isinstance(licenses, list) or not licenses or any(not isinstance(item, str) or not item.strip() for item in licenses):
            raise SourceReviewError(f"source review {source_key!r} must list applicable license identifiers")
        if len(licenses) != len(set(licenses)):
            raise SourceReviewError(f"source review {source_key!r} repeats a license identifier")
        if not isinstance(review["scope"], str) or not review["scope"].strip():
            raise SourceReviewError(f"source review {source_key!r} scope must be non-blank")


def enforce_source_review(report: dict[str, Any], raw: dict[str, Any], source_key: str) -> dict[str, Any]:
    validate_source_reviews(raw)
    if source_key == "synthetic-fixture":
        report.setdefault("sourceRights", {})["termsReview"] = {"state": "fixture-only", "policyVersion": raw["policyVersion"]}
        return report
    evaluated_at = _timestamp(report.get("evaluatedAt"), "report.evaluatedAt")
    review = raw["sources"].get(source_key)
    reason: str | None = None
    if not isinstance(review, dict):
        reason = "source has no reviewed terms record"
    else:
        reviewed_at = _timestamp(review["reviewedAt"], f"{source_key}.reviewedAt")
        expires_at = _timestamp(review["expiresAt"], f"{source_key}.expiresAt")
        license_id = report.get("sourceRights", {}).get("licenseIdentifier")
        if review["state"] != "approved":
            reason = f"source terms review is {review['state']}"
        elif reviewed_at > evaluated_at:
            reason = "source terms review is not effective at proposal time"
        elif evaluated_at >= expires_at:
            reason = "source terms review expired before proposal time"
        elif isinstance(license_id, str) and license_id not in review["licenseIdentifiers"]:
            reason = f"review does not cover active license {license_id!r}"
    terms = {
        "policyVersion": raw["policyVersion"],
        "state": "invalid" if reason else "approved",
        "sourceKey": source_key,
    }
    if isinstance(review, dict):
        terms.update({
            "reviewedAt": review["reviewedAt"],
            "expiresAt": review["expiresAt"],
            "licenseIdentifiers": list(review["licenseIdentifiers"]),
            "scope": review["scope"],
        })
    if reason:
        terms["reason"] = reason
        finding = {"code": "source-terms-review-invalid", "detail": reason}
        existing = report.setdefault("releaseBlockingFindings", [])
        if finding not in existing:
            existing.append(finding)
            existing.sort(key=lambda item: (str(item.get("code", "")), str(item.get("detail", ""))))
        report["status"] = "blocked"
    report.setdefault("sourceRights", {})["termsReview"] = terms
    return report
