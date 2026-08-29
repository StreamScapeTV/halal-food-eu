#!/usr/bin/env python3
"""Pure catalog-selection decision, projection, and reporting engine."""

from __future__ import annotations

import copy
import hashlib
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from catalog_selection_contract import (
    SCHEMA_VERSION,
    canonical_json,
    normalize_gtin,
    validate_bundle,
    validate_policy,
)

DECISION_INCLUDE = "include-detailed"
DECISION_BASIC = "exclude-basic"
DECISION_INVALID = "exclude-invalid"

INVALID_NON_FOOD = "non-food"
INVALID_WRONG_MARKET = "wrong-market"
INVALID_SOURCE_ASSIGNED = "source-assigned-no-barcode"
INVALID_BARCODE_KIND = "unsupported-barcode-kind"
INVALID_BARCODE = "invalid-or-unsupported-barcode"

INCLUDE_EXISTING_EVIDENCE = "existing-evidence"
INCLUDE_FORMULATION = "formulation-signal"
INCLUDE_CATEGORY = "included-category"
INCLUDE_MULTI_INGREDIENT = "multi-ingredient"
INCLUDE_INGREDIENTS_UNQUANTIFIED = "ingredients-present-unquantified"
INCLUDE_CONSERVATIVE_UNKNOWN = "conservative-unknown"


@dataclass(frozen=True)
class Decision:
    source_record_id: str
    decision: str
    reason_code: str
    gtin: str | None
    candidate: dict[str, Any]


def _has_text(candidate: dict[str, Any]) -> bool:
    value = candidate.get("ingredientsText")
    return isinstance(value, str) and bool(value.strip())


def classify_candidate(candidate: dict[str, Any], policy: dict[str, Any]) -> Decision:
    source_record_id = candidate["sourceRecordID"]
    barcode = candidate["barcode"]
    gtin = normalize_gtin(barcode)

    if candidate["productType"] not in set(policy["allowedProductTypes"]):
        return Decision(source_record_id, DECISION_INVALID, INVALID_NON_FOOD, gtin, candidate)
    if candidate["market"] != policy["targetMarket"]:
        return Decision(source_record_id, DECISION_INVALID, INVALID_WRONG_MARKET, gtin, candidate)
    if candidate["barcodeKind"] == "source-assigned-no-barcode":
        return Decision(source_record_id, DECISION_INVALID, INVALID_SOURCE_ASSIGNED, gtin, candidate)
    if candidate["barcodeKind"] not in set(policy["acceptedBarcodeKinds"]):
        return Decision(source_record_id, DECISION_INVALID, INVALID_BARCODE_KIND, gtin, candidate)
    if gtin is None:
        return Decision(source_record_id, DECISION_INVALID, INVALID_BARCODE, None, candidate)

    evidence_signals = set(candidate["evidenceSignals"])
    formulation_signals = set(candidate["formulationSignals"])
    category_signals = set(candidate["categorySignals"])

    if evidence_signals & set(policy["includeEvidenceSignals"]):
        return Decision(
            source_record_id,
            DECISION_INCLUDE,
            INCLUDE_EXISTING_EVIDENCE,
            gtin,
            candidate,
        )
    if formulation_signals & set(policy["includeFormulationSignals"]):
        return Decision(
            source_record_id,
            DECISION_INCLUDE,
            INCLUDE_FORMULATION,
            gtin,
            candidate,
        )
    if category_signals & set(policy["includeCategorySignals"]):
        return Decision(
            source_record_id,
            DECISION_INCLUDE,
            INCLUDE_CATEGORY,
            gtin,
            candidate,
        )

    ingredient_count = candidate.get("ingredientCount")
    if ingredient_count is not None and ingredient_count > 1:
        return Decision(
            source_record_id,
            DECISION_INCLUDE,
            INCLUDE_MULTI_INGREDIENT,
            gtin,
            candidate,
        )
    if _has_text(candidate) and ingredient_count is None:
        return Decision(
            source_record_id,
            DECISION_INCLUDE,
            INCLUDE_INGREDIENTS_UNQUANTIFIED,
            gtin,
            candidate,
        )

    for rule in policy["basicRules"]:
        if not (category_signals & set(rule["categorySignals"])):
            continue
        if ingredient_count is None:
            if not rule["allowUnknownIngredientCount"]:
                continue
            effective_count = 0
        else:
            effective_count = ingredient_count
        if effective_count <= rule["maxIngredientCount"]:
            return Decision(
                source_record_id,
                DECISION_BASIC,
                rule["code"],
                gtin,
                candidate,
            )

    return Decision(
        source_record_id,
        DECISION_INCLUDE,
        INCLUDE_CONSERVATIVE_UNKNOWN,
        gtin,
        candidate,
    )


def _decision_sort_key(decision: Decision) -> tuple[str, str, str]:
    return (
        decision.gtin or decision.candidate["barcode"],
        decision.candidate["market"],
        decision.source_record_id,
    )


def _sorted_strings(values: Iterable[str]) -> list[str]:
    return sorted(set(values))


def _selected_record(decision: Decision, policy_version: str) -> dict[str, Any]:
    candidate = decision.candidate
    result: dict[str, Any] = {
        "sourceRecordID": decision.source_record_id,
        "barcode": candidate["barcode"],
        "gtin": decision.gtin,
        "market": candidate["market"],
        "name": candidate["name"],
        "policyVersion": policy_version,
        "reasonCode": decision.reason_code,
        "categoryTags": _sorted_strings(candidate["categoryTags"]),
        "categorySignals": _sorted_strings(candidate["categorySignals"]),
        "formulationSignals": _sorted_strings(candidate["formulationSignals"]),
        "evidenceSignals": _sorted_strings(candidate["evidenceSignals"]),
        "retailerKeys": _sorted_strings(candidate["retailerKeys"]),
        "remoteImages": sorted(
            copy.deepcopy(candidate["remoteImages"]),
            key=lambda item: (item["purpose"], item["url"], item["imageID"]),
        ),
    }
    for field in ("brand", "ingredientsText", "ingredientCount", "packageSignals"):
        if field in candidate:
            value = copy.deepcopy(candidate[field])
            if field == "packageSignals" and isinstance(value, list):
                value = _sorted_strings(value)
            result[field] = value
    return result


def _basic_exclusion(decision: Decision, policy_version: str) -> dict[str, Any]:
    assert decision.gtin is not None
    return {
        "gtin": decision.gtin,
        "market": decision.candidate["market"],
        "policyVersion": policy_version,
        "reasonCode": decision.reason_code,
    }


def _invalid_exclusion(decision: Decision, policy_version: str) -> dict[str, Any]:
    result = {
        "sourceRecordID": decision.source_record_id,
        "barcode": decision.candidate["barcode"],
        "market": decision.candidate["market"],
        "policyVersion": policy_version,
        "reasonCode": decision.reason_code,
    }
    if decision.gtin is not None:
        result["gtin"] = decision.gtin
    return result


def _top(counter: Counter[str], limit: int = 10) -> list[dict[str, Any]]:
    return [
        {"value": value, "count": count}
        for value, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]
    ]


def _audit_sample(decisions: list[Decision], policy: dict[str, Any]) -> list[dict[str, Any]]:
    basic = [decision for decision in decisions if decision.decision == DECISION_BASIC]
    ranked = sorted(
        basic,
        key=lambda decision: hashlib.sha256(
            "|".join(
                (
                    policy["policyVersion"],
                    decision.source_record_id,
                    decision.gtin or "",
                    decision.candidate["market"],
                    decision.reason_code,
                )
            ).encode("utf-8")
        ).hexdigest(),
    )
    return [
        {
            "sourceRecordID": decision.source_record_id,
            "gtin": decision.gtin,
            "market": decision.candidate["market"],
            "name": decision.candidate["name"],
            "reasonCode": decision.reason_code,
        }
        for decision in ranked[: policy["auditSampleSize"]]
    ]


def _report(
    bundle: dict[str, Any],
    policy: dict[str, Any],
    decisions: list[Decision],
    selected: list[dict[str, Any]],
    basic_exclusions: list[dict[str, Any]],
) -> dict[str, Any]:
    reason_counts = Counter(decision.reason_code for decision in decisions)
    basic_reason_counts = Counter(
        decision.reason_code for decision in decisions if decision.decision == DECISION_BASIC
    )
    invalid_reason_counts = Counter(
        decision.reason_code for decision in decisions if decision.decision == DECISION_INVALID
    )
    category_counts: Counter[str] = Counter()
    brand_counts: Counter[str] = Counter()
    retailer_counts: Counter[str] = Counter()
    for decision in decisions:
        if decision.decision == DECISION_INVALID:
            continue
        category_counts.update(decision.candidate["categoryTags"])
        brand = decision.candidate.get("brand")
        if isinstance(brand, str) and brand.strip():
            brand_counts[brand] += 1
        retailer_counts.update(decision.candidate["retailerKeys"])

    included_decisions = [d for d in decisions if d.decision == DECISION_INCLUDE]
    included_with_ingredients = sum(1 for d in included_decisions if _has_text(d.candidate))
    included_missing_ingredients = len(included_decisions) - included_with_ingredients
    market_records = sum(
        1 for candidate in bundle["candidates"] if candidate["market"] == policy["targetMarket"]
    )
    eligible = sum(1 for d in decisions if d.decision != DECISION_INVALID)

    return {
        "policyVersion": policy["policyVersion"],
        "sourceSnapshot": copy.deepcopy(bundle["sourceSnapshot"]),
        "sourceRecordsExamined": len(bundle["candidates"]),
        "targetMarketRecords": market_records,
        "germanyRelevantCandidates": eligible,
        "includedProducts": len(included_decisions),
        "excludedBasicProducts": len(basic_exclusions),
        "excludedInvalidRecords": sum(1 for d in decisions if d.decision == DECISION_INVALID),
        "includedWithIngredients": included_with_ingredients,
        "includedMissingIngredients": included_missing_ingredients,
        "decisionReasons": dict(sorted(reason_counts.items())),
        "excludedBasicByReason": dict(sorted(basic_reason_counts.items())),
        "excludedInvalidByReason": dict(sorted(invalid_reason_counts.items())),
        "topCategories": _top(category_counts),
        "topBrands": _top(brand_counts),
        "topRetailers": _top(retailer_counts),
        "logicalDetailedPayloadBytes": len(canonical_json(selected).encode("utf-8")),
        "logicalBasicExclusionIndexBytes": len(
            canonical_json(basic_exclusions).encode("utf-8")
        ),
        "excludedBasicSample": _audit_sample(decisions, policy),
    }


def _comparison(
    current: list[Decision],
    previous: list[Decision],
    current_policy: dict[str, Any],
    previous_policy: dict[str, Any],
) -> dict[str, Any]:
    current_map = {decision.source_record_id: decision for decision in current}
    previous_map = {decision.source_record_id: decision for decision in previous}
    changes: list[dict[str, Any]] = []
    for source_record_id in sorted(set(current_map) | set(previous_map)):
        current_decision = current_map[source_record_id]
        previous_decision = previous_map[source_record_id]
        if (
            current_decision.decision,
            current_decision.reason_code,
        ) == (
            previous_decision.decision,
            previous_decision.reason_code,
        ):
            continue
        changes.append(
            {
                "sourceRecordID": source_record_id,
                "gtin": current_decision.gtin or previous_decision.gtin,
                "previousDecision": previous_decision.decision,
                "previousReasonCode": previous_decision.reason_code,
                "currentDecision": current_decision.decision,
                "currentReasonCode": current_decision.reason_code,
            }
        )

    def count(decisions: list[Decision], value: str) -> int:
        return sum(1 for decision in decisions if decision.decision == value)

    return {
        "previousPolicyVersion": previous_policy["policyVersion"],
        "currentPolicyVersion": current_policy["policyVersion"],
        "decisionChanges": changes,
        "decisionChangeCount": len(changes),
        "includedDelta": count(current, DECISION_INCLUDE) - count(previous, DECISION_INCLUDE),
        "excludedBasicDelta": count(current, DECISION_BASIC) - count(previous, DECISION_BASIC),
        "excludedInvalidDelta": count(current, DECISION_INVALID) - count(previous, DECISION_INVALID),
    }


def evaluate_bundle(
    policy_data: Any,
    bundle_data: Any,
    *,
    previous_policy_data: Any | None = None,
) -> dict[str, Any]:
    policy = validate_policy(copy.deepcopy(policy_data))
    bundle = validate_bundle(copy.deepcopy(bundle_data))

    decisions = sorted(
        (classify_candidate(candidate, policy) for candidate in bundle["candidates"]),
        key=_decision_sort_key,
    )
    selected = [
        _selected_record(decision, policy["policyVersion"])
        for decision in decisions
        if decision.decision == DECISION_INCLUDE
    ]
    basic_exclusions = [
        _basic_exclusion(decision, policy["policyVersion"])
        for decision in decisions
        if decision.decision == DECISION_BASIC
    ]
    invalid_exclusions = [
        _invalid_exclusion(decision, policy["policyVersion"])
        for decision in decisions
        if decision.decision == DECISION_INVALID
    ]

    result: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "policyVersion": policy["policyVersion"],
        "selected": selected,
        "basicExclusions": basic_exclusions,
        "invalidExclusions": invalid_exclusions,
        "report": _report(bundle, policy, decisions, selected, basic_exclusions),
    }
    if previous_policy_data is not None:
        previous_policy = validate_policy(copy.deepcopy(previous_policy_data))
        previous_decisions = sorted(
            (
                classify_candidate(candidate, previous_policy)
                for candidate in bundle["candidates"]
            ),
            key=_decision_sort_key,
        )
        result["comparison"] = _comparison(
            decisions,
            previous_decisions,
            policy,
            previous_policy,
        )
    return result
