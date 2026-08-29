"""Synthetic end-to-end fixture bridge for the catalog workflow architecture.

This module exists only to prove that the v1 workflow stages consume the exact
artifact handed to them. Production source normalization and the final
immutable-evidence-to-SQLite compiler remain separate source/compiler issues.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from catalog_builder import normalize_gtin
from catalog_workflow_common import ContractError, exact_keys, require_object

SYNTHETIC_LICENSE = "Halal-Food-EU-Synthetic-Fixture-1.0"
SYNTHETIC_ATTRIBUTION = (
    "Synthetic Halal Food EU workflow fixtures created by StreamScapeTV. "
    "No real products are represented."
)


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"{label} is not valid JSON: {path}") from exc
    return require_object(raw, label)


def _load_source_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ContractError(f"synthetic source fixture is unreadable: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            record = require_object(json.loads(line), f"source record line {line_number}")
        except json.JSONDecodeError as exc:
            raise ContractError(f"source record line {line_number} is not valid JSON") from exc
        exact_keys(
            record,
            required={"sourceRecordId", "gtin", "market", "ingredientText", "language"},
            optional=set(),
            label=f"source record line {line_number}",
        )
        source_record_id = record["sourceRecordId"]
        if not isinstance(source_record_id, str) or not source_record_id.strip():
            raise ContractError(f"source record line {line_number} has invalid sourceRecordId")
        if not isinstance(record["gtin"], str):
            raise ContractError(f"source record line {line_number} gtin must be a string")
        if record["market"] != "DE":
            raise ContractError(f"source record line {line_number} must target DE")
        ingredient_text = record["ingredientText"]
        language = record["language"]
        if ingredient_text is None:
            if language is not None:
                raise ContractError(f"source record line {line_number} missing ingredients must have null language")
        elif not isinstance(ingredient_text, str) or not ingredient_text.strip():
            raise ContractError(f"source record line {line_number} ingredientText is invalid")
        elif not isinstance(language, str) or not language.strip():
            raise ContractError(f"source record line {line_number} language is invalid")
        records.append(record)
    if not records:
        raise ContractError("synthetic source fixture is empty")
    return records


def _index(items: Any, key: str, label: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise ContractError(f"evidence {label} must be an array")
    indexed: dict[str, dict[str, Any]] = {}
    for item in items:
        value = require_object(item, f"evidence {label} item")
        identifier = value.get(key)
        if not isinstance(identifier, str) or not identifier:
            raise ContractError(f"evidence {label} item has invalid {key}")
        if identifier in indexed:
            raise ContractError(f"evidence {label} contains duplicate {key} {identifier}")
        indexed[identifier] = value
    return indexed


def _normalized_gtin(value: str) -> str:
    try:
        return normalize_gtin(value)
    except (TypeError, ValueError) as exc:
        raise ContractError(f"synthetic fixture contains invalid GTIN {value!r}") from exc


def validate_synthetic_normalization(source_path: Path, evidence_path: Path) -> int:
    """Prove each acquired fixture row is represented by the copied v1 evidence."""

    records = _load_source_records(source_path)
    evidence = _load_json(evidence_path, "evidence fixture")
    identities = _index(evidence.get("identities"), "id", "identities")
    ingredients = _index(evidence.get("ingredients"), "id", "ingredients")

    selections_raw = evidence.get("currentSelections")
    if not isinstance(selections_raw, list):
        raise ContractError("evidence currentSelections must be an array")
    selections: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in selections_raw:
        selection = require_object(raw, "evidence current selection")
        gtin = selection.get("gtin")
        market = selection.get("market")
        if not isinstance(gtin, str) or not isinstance(market, str):
            raise ContractError("evidence current selection has invalid gtin/market")
        key = (gtin, market)
        if key in selections:
            raise ContractError(f"duplicate evidence current selection for {gtin}/{market}")
        selections[key] = selection

    seen: set[tuple[str, str]] = set()
    seen_source_ids: set[str] = set()
    for record in records:
        gtin = _normalized_gtin(record["gtin"])
        market = record["market"]
        key = (gtin, market)
        if key in seen:
            raise ContractError(f"duplicate synthetic source record for {gtin}/{market}")
        seen.add(key)
        source_record_id = record["sourceRecordId"]
        if source_record_id in seen_source_ids:
            raise ContractError(f"duplicate synthetic sourceRecordId {source_record_id}")
        seen_source_ids.add(source_record_id)

        selection = selections.get(key)
        if selection is None:
            raise ContractError(f"source record {source_record_id} is missing from current evidence selection")
        identity_id = selection.get("identityObservationID")
        identity = identities.get(identity_id)
        if identity is None or identity.get("sourceRecordID") != source_record_id:
            raise ContractError(f"source record {source_record_id} does not match selected identity evidence")

        ingredient_id = selection.get("ingredientObservationID")
        if record["ingredientText"] is None:
            if ingredient_id is not None:
                raise ContractError(f"source record {source_record_id} unexpectedly has ingredient evidence")
            continue
        ingredient = ingredients.get(ingredient_id)
        if ingredient is None:
            raise ContractError(f"source record {source_record_id} is missing selected ingredient evidence")
        if ingredient.get("ingredientsText") != record["ingredientText"]:
            raise ContractError(f"source record {source_record_id} ingredient text differs from normalized evidence")
        if ingredient.get("languageCode") != record["language"]:
            raise ContractError(f"source record {source_record_id} language differs from normalized evidence")

    if seen != set(selections):
        missing = sorted(set(selections) - seen)
        raise ContractError(f"normalized evidence contains current selections absent from source fixture: {missing}")
    return len(records)


def materialize_fixture_builder_input(evidence_path: Path, output_path: Path) -> dict[str, Any]:
    """Project accepted synthetic evidence into the legacy demo builder input.

    This is deliberately fixture-only. Issue #12 owns the production compiler.
    """

    evidence = _load_json(evidence_path, "evidence fixture")
    identities = _index(evidence.get("identities"), "id", "identities")
    ingredients = _index(evidence.get("ingredients"), "id", "ingredients")
    assessments = _index(evidence.get("assessments"), "id", "assessments")
    certifications = _index(evidence.get("certifications"), "id", "certifications")
    sources = _index(evidence.get("sources"), "sourceKey", "sources")

    reviews_raw = evidence.get("reviews")
    if not isinstance(reviews_raw, list):
        raise ContractError("evidence reviews must be an array")
    reviews_by_target: dict[str, list[dict[str, Any]]] = {}
    for raw in reviews_raw:
        review = require_object(raw, "evidence review")
        target_id = review.get("targetID")
        if isinstance(target_id, str) and review.get("state") == "approved":
            reviews_by_target.setdefault(target_id, []).append(review)

    selections_raw = evidence.get("currentSelections")
    if not isinstance(selections_raw, list) or not selections_raw:
        raise ContractError("evidence currentSelections must be a non-empty array")

    products: list[dict[str, Any]] = []
    methodology_versions: set[str] = set()
    reviewed_times: list[str] = []
    used_source_keys: set[str] = set()
    for raw in sorted(selections_raw, key=lambda item: (item.get("gtin", ""), item.get("market", ""))):
        selection = require_object(raw, "evidence current selection")
        if selection.get("market") != "DE":
            raise ContractError("fixture builder input only supports DE current selections")
        gtin = selection.get("gtin")
        if not isinstance(gtin, str):
            raise ContractError("current selection gtin is invalid")
        identity = identities.get(selection.get("identityObservationID"))
        if identity is None:
            raise ContractError(f"current selection {gtin} is missing identity evidence")
        assessment = assessments.get(selection.get("assessmentID"))
        if assessment is None:
            raise ContractError(f"current selection {gtin} is missing an accepted assessment")
        approved_reviews = reviews_by_target.get(assessment["id"], [])
        if not approved_reviews:
            raise ContractError(f"current assessment {assessment['id']} lacks an approved review")
        reviewed_at = max(str(review.get("reviewedAt", "")) for review in approved_reviews)
        reviewed_times.append(reviewed_at)
        methodology = assessment.get("methodologyVersion")
        if not isinstance(methodology, str) or not methodology:
            raise ContractError(f"current assessment {assessment['id']} has invalid methodologyVersion")
        methodology_versions.add(methodology)

        ingredient_id = selection.get("ingredientObservationID")
        ingredient = ingredients.get(ingredient_id) if ingredient_id is not None else None
        status = assessment.get("status")
        if ingredient is None and status != "unknown":
            raise ContractError(f"current selection {gtin} lacks ingredients but status is {status}")

        source_key = identity.get("sourceKey")
        if not isinstance(source_key, str) or source_key not in sources:
            raise ContractError(f"identity source for {gtin} is not registered in evidence")
        used_source_keys.add(source_key)

        projected_certifications: list[dict[str, Any]] = []
        certification_ids = selection.get("certificationIDs", [])
        if not isinstance(certification_ids, list):
            raise ContractError(f"current selection {gtin} certificationIDs must be an array")
        for certification_id in certification_ids:
            certification = certifications.get(certification_id)
            if certification is None:
                raise ContractError(f"current selection {gtin} references missing certification {certification_id}")
            certification_source = certification.get("sourceKey")
            if not isinstance(certification_source, str) or certification_source not in sources:
                raise ContractError(f"certification source for {gtin} is not registered in evidence")
            used_source_keys.add(certification_source)
            projected_certifications.append(
                {
                    "sourceKey": certification_source,
                    "certifyingBody": certification["certifier"],
                    "certificateReference": certification["certificateReference"],
                    "scope": certification["scope"],
                    "validFrom": certification.get("effectiveAt") or certification.get("issueAt"),
                    "validUntil": certification.get("expiryAt"),
                }
            )

        reasons = assessment.get("reasons")
        if not isinstance(reasons, list) or not reasons:
            raise ContractError(f"current assessment {assessment['id']} has no reasons")
        projected_reasons = []
        for reason_raw in reasons:
            reason = require_object(reason_raw, "assessment reason")
            projected_reasons.append(
                {
                    "code": reason["code"],
                    "title": reason["title"],
                    "detail": reason["detail"],
                    "ingredient": reason.get("ingredient"),
                    "severity": reason["severity"],
                }
            )

        products.append(
            {
                "barcode": gtin,
                "name": identity["name"],
                "brand": identity.get("brand"),
                "sourceKey": source_key,
                "sourceProductId": identity["sourceRecordID"],
                "ingredients": {
                    "text": ingredient.get("ingredientsText") if ingredient is not None else None,
                    "languageCode": ingredient.get("languageCode") if ingredient is not None else "und",
                    "observedAt": ingredient.get("observedAt") if ingredient is not None else identity["observedAt"],
                },
                "assessment": {
                    "status": status,
                    "summary": f"Synthetic fixture projection of immutable assessment {assessment['id']}.",
                    "reviewedAt": reviewed_at,
                    "reasons": projected_reasons,
                    "certifications": projected_certifications,
                },
            }
        )

    if len(methodology_versions) != 1:
        raise ContractError("fixture current assessments must use one methodologyVersion")

    projected_sources = []
    for source_key in sorted(used_source_keys):
        source = sources[source_key]
        projected_sources.append(
            {
                "key": source_key,
                "name": source["operator"],
                "kind": source["sourceClass"],
                "reference": source["reference"],
                "license": SYNTHETIC_LICENSE,
                "retrievedAt": source["retrievedAt"],
            }
        )

    output = {
        "catalog": {
            "catalogVersion": "0.1.0-workflow-fixture",
            "schemaVersion": 1,
            "methodologyVersion": next(iter(methodology_versions)),
            "generatedAt": max(reviewed_times),
            "dataLicense": SYNTHETIC_LICENSE,
            "attribution": SYNTHETIC_ATTRIBUTION,
        },
        "sources": projected_sources,
        "products": products,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return output
