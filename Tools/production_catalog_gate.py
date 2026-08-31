#!/usr/bin/env python3
"""Fail-closed release admission for production catalog compilation.

This module deliberately re-checks the safety-critical parts of the upstream
catalog-quality decision before SQLite materialization.  The quality report is
an immutable reviewed input, not a permission bit that lets the compiler skip
validation of the exact evidence envelope it is about to publish.
"""

from __future__ import annotations

import calendar
import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

POSITIVE_STATUSES = {"halal-certified", "halal-reviewed"}
TERMINAL_REVIEW_STATES = {"rejected", "superseded"}
FRESHNESS_STATES = {"fresh", "refresh-recommended", "stale", "date-unknown", "changed-unreviewed"}


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parse_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is not a valid timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _add_months(value: datetime, months: int) -> datetime:
    index = value.month - 1 + months
    year, month = value.year + index // 12, index % 12 + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def _freshness_state(anchor: Any, as_of: datetime, *, refresh_months: int, stale_months: int) -> str:
    if anchor is None:
        return "date-unknown"
    observed = _parse_timestamp(anchor, "ingredient observedAt")
    if as_of >= _add_months(observed, stale_months):
        return "stale"
    if as_of >= _add_months(observed, refresh_months):
        return "refresh-recommended"
    return "fresh"


def _map(envelope: dict[str, Any], collection: str) -> dict[str, dict[str, Any]]:
    return {
        item["id"]: item
        for item in envelope.get(collection, [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def _approved_reviews(envelope: dict[str, Any], assessment_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in envelope.get("reviews", [])
        if isinstance(item, dict)
        and item.get("targetType") == "assessment"
        and item.get("targetID") == assessment_id
        and item.get("state") == "approved"
    ]


def _terminal_reviews(envelope: dict[str, Any], assessment_id: str) -> list[dict[str, Any]]:
    return [
        item
        for item in envelope.get("reviews", [])
        if isinstance(item, dict)
        and item.get("targetType") == "assessment"
        and item.get("targetID") == assessment_id
        and item.get("state") in TERMINAL_REVIEW_STATES
    ]


def _active_formulations(envelope: dict[str, Any], gtin: str, market: str) -> list[dict[str, Any]]:
    items = [
        item
        for item in envelope.get("ingredients", [])
        if isinstance(item, dict) and item.get("gtin") == gtin and item.get("market") == market
    ]
    superseded = {
        item.get("supersedesID")
        for item in items
        if isinstance(item.get("supersedesID"), str)
    }
    return [item for item in items if item.get("id") not in superseded]


def _validate_quality_report_digest(report: dict[str, Any]) -> None:
    digest = report.get("reportSha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("quality report has no valid reportSha256")
    subject = copy.deepcopy(report)
    subject.pop("reportSha256", None)
    if _sha256_json(subject) != digest:
        raise ValueError("quality report self-digest mismatch")


def _validate_quality_report_shape(
    report: dict[str, Any],
    policy: dict[str, Any],
    envelope: dict[str, Any],
) -> datetime:
    if report.get("schemaVersion") != 1:
        raise ValueError("quality report schemaVersion is unsupported")
    if report.get("policyVersion") != policy.get("policyVersion"):
        raise ValueError("quality report policy version does not match reviewed quality policy")
    if report.get("status") != "pass":
        raise ValueError("quality report is not release-passing")
    if report.get("releaseBlockingFindings") != []:
        raise ValueError("quality report contains release-blocking findings")
    if report.get("quarantineRequired") is True or report.get("rollbackRequired") is True:
        raise ValueError("quality report requires quarantine or rollback")
    _validate_quality_report_digest(report)

    source_key = report.get("sourceKey")
    snapshot_id = report.get("snapshotID")
    if not isinstance(source_key, str) or not source_key:
        raise ValueError("quality report sourceKey is missing")
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("quality report snapshotID is missing")

    sources = [item for item in envelope.get("sources", []) if isinstance(item, dict)]
    if source_key == "synthetic-fixture":
        if not sources or any(item.get("sourceClass") != "synthetic" for item in sources):
            raise ValueError("synthetic-fixture quality report cannot admit non-synthetic evidence")
        rights = report.get("sourceRights")
        if not isinstance(rights, dict) or rights.get("fixtureOnly") is not True:
            raise ValueError("synthetic-fixture quality report must remain fixture-only")
    else:
        source = next((item for item in sources if item.get("sourceKey") == source_key), None)
        if source is None:
            raise ValueError(f"quality report source {source_key!r} is absent from evidence")
        if source.get("sourceSnapshotID") != snapshot_id:
            raise ValueError("quality report snapshot does not match normalized evidence")

    metrics = report.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("quality report metrics are missing")
    if metrics.get("products") != len(envelope.get("currentSelections", [])):
        raise ValueError("quality report product count does not match evidence")
    return _parse_timestamp(report.get("evaluatedAt"), "quality report evaluatedAt")


def validate_release_gate(
    *,
    envelope: dict[str, Any],
    quality_report: dict[str, Any],
    quality_policy: dict[str, Any],
) -> dict[str, Any]:
    """Validate exact current selections against the reviewed quality decision.

    Returns compiler-only derived state.  No review records are copied into the
    runtime database; only the approved-review timestamp/count and formulation
    freshness needed for truthful offline display/validation are retained.
    """
    review_policy = quality_policy.get("review")
    freshness = quality_policy.get("freshness", {}).get("formulation")
    if not isinstance(review_policy, dict):
        raise ValueError("quality policy review section is missing")
    if not isinstance(freshness, dict):
        raise ValueError("quality policy formulation freshness section is missing")
    required_reviewers = review_policy.get("minimumIndependentReviewers")
    positives = set(review_policy.get("positiveStatusesRequiringIndependentSecondReview", []))
    if not isinstance(required_reviewers, int) or required_reviewers < 2:
        raise ValueError("quality policy minimumIndependentReviewers is invalid")
    if positives != POSITIVE_STATUSES:
        raise ValueError("quality policy positive review statuses do not match the supported runtime contract")
    refresh_months = freshness.get("refreshRecommendedMonths")
    stale_months = freshness.get("staleMonths")
    if not isinstance(refresh_months, int) or not isinstance(stale_months, int) or stale_months <= refresh_months:
        raise ValueError("quality policy formulation freshness thresholds are invalid")

    as_of = _validate_quality_report_shape(quality_report, quality_policy, envelope)
    identities = _map(envelope, "identities")
    ingredients = _map(envelope, "ingredients")
    assessments = _map(envelope, "assessments")
    sources = {item.get("sourceKey"): item for item in envelope.get("sources", []) if isinstance(item, dict)}

    assessment_reviews: dict[str, dict[str, Any]] = {}
    ingredient_freshness: dict[str, str] = {}
    status_counts: Counter[str] = Counter()
    freshness_counts: Counter[str] = Counter()
    missing_ingredients = 0
    conflicts = 0
    second_review_deficits = 0

    selections = sorted(
        [item for item in envelope.get("currentSelections", []) if isinstance(item, dict)],
        key=lambda item: (str(item.get("gtin", "")), str(item.get("market", ""))),
    )
    for selection in selections:
        gtin = str(selection.get("gtin", ""))
        market = str(selection.get("market", ""))
        assessment_id = selection.get("assessmentID")
        assessment: dict[str, Any] | None = None
        status: str | None = None
        if assessment_id is not None:
            if not isinstance(assessment_id, str) or assessment_id not in assessments:
                raise ValueError(f"{gtin}/{market} selected assessment is missing")
            assessment = assessments[assessment_id]
            status = assessment.get("status")
            if not isinstance(status, str):
                raise ValueError(f"{gtin}/{market} has an invalid assessment status")
            status_counts[status] += 1

            approved = _approved_reviews(envelope, assessment_id)
            terminal = _terminal_reviews(envelope, assessment_id)
            if terminal:
                raise ValueError(f"{gtin}/{market} current assessment has a terminal rejected/superseded review")
            reviewers = sorted({str(item.get("reviewerID")) for item in approved if str(item.get("reviewerID", "")).strip()})
            if not reviewers:
                raise ValueError(f"{gtin}/{market} current assessment has no approved reviewer")
            reviewed_times = sorted(_parse_timestamp(item.get("reviewedAt"), "approved review reviewedAt") for item in approved)
            identity = identities.get(selection.get("identityObservationID"))
            identity_source = sources.get(identity.get("sourceKey")) if isinstance(identity, dict) else None
            synthetic = isinstance(identity_source, dict) and identity_source.get("sourceClass") == "synthetic"
            required = 1 if synthetic else required_reviewers
            if status in positives and len(reviewers) < required:
                second_review_deficits += 1
                raise ValueError(
                    f"{gtin}/{market} positive assessment has {len(reviewers)} independent reviewer(s); requires {required}"
                )
            assessment_reviews[assessment_id] = {
                "reviewedAt": reviewed_times[-1].isoformat().replace("+00:00", "Z"),
                "approvedReviewerCount": len(reviewers),
            }

        ingredient_id = selection.get("ingredientObservationID")
        if ingredient_id is None:
            missing_ingredients += 1
            freshness_counts["date-unknown"] += 1
        else:
            ingredient = ingredients.get(ingredient_id)
            if ingredient is None:
                raise ValueError(f"{gtin}/{market} selected ingredient observation is missing")
            state = _freshness_state(
                ingredient.get(freshness.get("anchorField", "observedAt")),
                as_of,
                refresh_months=refresh_months,
                stale_months=stale_months,
            )
            approved_targets = {ingredient_id}
            if isinstance(assessment_id, str):
                approved_targets.add(assessment_id)
            if ingredient.get("supersedesID") and not any(
                review.get("targetID") in approved_targets
                for review in envelope.get("reviews", [])
                if isinstance(review, dict) and review.get("state") == "approved"
            ):
                state = "changed-unreviewed"
            ingredient_freshness[str(ingredient_id)] = state
            freshness_counts[state] += 1

        active_hashes = {
            item.get("contentHash")
            for item in _active_formulations(envelope, gtin, market)
            if isinstance(item.get("contentHash"), str)
        }
        has_conflict = len(active_hashes) > 1 or bool(selection.get("conflictFlags"))
        if has_conflict:
            conflicts += 1
            if status in positives:
                raise ValueError(f"{gtin}/{market} positive assessment cannot be compiled with a formulation conflict")

        if assessment is not None and status == "not-halal" and not any(
            reason.get("severity") == "prohibitive" for reason in assessment.get("reasons", [])
        ):
            raise ValueError(f"{gtin}/{market} not-halal assessment lacks a prohibitive structured reason")

    metrics = quality_report["metrics"]
    expected_freshness = metrics.get("formulationFreshness")
    normalized_freshness = {state: freshness_counts[state] for state in sorted(FRESHNESS_STATES)}
    if expected_freshness != normalized_freshness:
        raise ValueError("quality report formulation freshness metrics do not match evidence")
    if metrics.get("assessmentStatus") != {status: status_counts[status] for status in sorted(status_counts)}:
        expected_status = metrics.get("assessmentStatus")
        # The canonical report includes zero-valued supported statuses. Accept that
        # representation only when every non-zero count matches exactly.
        if not isinstance(expected_status, dict) or any(
            int(expected_status.get(status, 0) or 0) != status_counts[status]
            for status in set(expected_status) | set(status_counts)
        ):
            raise ValueError("quality report assessment status metrics do not match evidence")
    if metrics.get("missingIngredientSelections") != missing_ingredients:
        raise ValueError("quality report missing-ingredient metric does not match evidence")
    if metrics.get("formulationConflicts") != conflicts:
        raise ValueError("quality report formulation-conflict metric does not match evidence")
    if metrics.get("positiveSecondReviewDeficits") != second_review_deficits:
        raise ValueError("quality report second-review metric does not match evidence")

    return {
        "evaluatedAt": as_of.isoformat().replace("+00:00", "Z"),
        "reportSha256": quality_report["reportSha256"],
        "policyVersion": quality_report["policyVersion"],
        "sourceKey": quality_report["sourceKey"],
        "snapshotID": quality_report["snapshotID"],
        "warningCount": len(quality_report.get("warnings", [])),
        "assessmentReviews": assessment_reviews,
        "ingredientFreshness": ingredient_freshness,
    }
