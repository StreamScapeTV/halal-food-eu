#!/usr/bin/env python3
"""Validate immutable Halal Food EU evidence envelopes and emit runtime projections.

This tool is deliberately stdlib-only and performs no network access. Source
acquisition, assessment methodology, current-source precedence, and SQLite
compilation are separate stages.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

SCHEMA_VERSION = 1
ID_PREFIX = "hfeu"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ID_RE = re.compile(r"^hfeu:[a-z0-9-]+:sha256:[0-9a-f]{64}$")
MARKET_RE = re.compile(r"^[A-Z]{2}$")
LANGUAGE_RE = re.compile(r"^(?:und|[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)$")
SOURCE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")

COLLECTION_KIND = {
    "identities": "identity",
    "ingredients": "ingredient",
    "retailerEvidence": "retailer",
    "remoteImages": "remote-image",
    "packageEvidence": "package-evidence",
    "certifications": "certification",
    "reviews": "review",
    "assessments": "assessment",
    "validityEvents": "validity-event",
    "currentSelections": "current-selection",
    "releases": "release",
}

COLLECTIONS = tuple(COLLECTION_KIND)

ENUMS = {
    "sourceClass": {
        "package-photo",
        "manufacturer",
        "certifier",
        "retailer-official",
        "open-database",
        "community-observation",
        "identity-registry",
        "synthetic",
    },
    "accessMethod": {
        "package",
        "public-bulk",
        "public-api",
        "partner-api",
        "sftp",
        "object-feed",
        "manual",
        "synthetic",
    },
    "identityConfidence": {"high", "medium", "low", "conflict"},
    "captureMethod": {"source-text", "package-transcription", "ocr", "manual-review"},
    "verificationState": {"unverified", "machine-assisted", "human-verified"},
    "retailerKind": {
        "retailer-feed-listing",
        "retailer-observation",
        "community-store-report",
    },
    "imagePurpose": {"front", "ingredients", "barcode", "nutrition", "certification"},
    "packagePurpose": {"front", "ingredients", "barcode", "nutrition", "certification"},
    "consentState": {"recorded", "not-required"},
    "privacyState": {"screened", "redacted"},
    "reviewState": {"unreviewed", "in-review", "approved", "rejected", "superseded"},
    "reviewTargetType": {
        "identity",
        "ingredient",
        "retailer",
        "package-evidence",
        "certification",
        "assessment",
    },
    "assessmentStatus": {
        "halal-certified",
        "halal-reviewed",
        "not-halal",
        "questionable",
        "unknown",
    },
    "reasonSeverity": {"positive", "informational", "caution", "prohibitive"},
    "validityKind": {"invalidated", "superseded", "restored"},
}

FIELDS: dict[str, tuple[set[str], set[str]]] = {
    "sources": (
        {
            "sourceKey",
            "operator",
            "sourceClass",
            "reference",
            "accessMethod",
            "markets",
            "retrievedAt",
        },
        {"sourceSnapshotID", "sourceRevision", "sourceModifiedAt"},
    ),
    "identities": (
        {
            "id",
            "gtin",
            "originalBarcode",
            "market",
            "sourceKey",
            "sourceRecordID",
            "name",
            "retrievedAt",
            "confidence",
        },
        {
            "sourceRevision",
            "observedAt",
            "sourceModifiedAt",
            "brandOwner",
            "brand",
            "quantity",
            "categories",
            "packaging",
        },
    ),
    "ingredients": (
        {
            "id",
            "gtin",
            "market",
            "sourceKey",
            "sourceRecordID",
            "ingredientsText",
            "languageCode",
            "retrievedAt",
            "contentHash",
            "captureMethod",
            "verificationState",
        },
        {
            "sourceRevision",
            "observedAt",
            "sourceModifiedAt",
            "allergensText",
            "tracesText",
            "supersedesID",
            "transformation",
        },
    ),
    "retailerEvidence": (
        {
            "id",
            "kind",
            "retailerKey",
            "gtin",
            "market",
            "sourceKey",
            "sourceRecordID",
            "retrievedAt",
            "confidence",
            "limitations",
        },
        {"observedAt", "snapshotAt", "locationID", "scope", "sourceRevision"},
    ),
    "remoteImages": (
        {
            "id",
            "gtin",
            "market",
            "purpose",
            "url",
            "sourceKey",
            "imageID",
            "retrievedAt",
        },
        {"revision", "sourceModifiedAt", "width", "height"},
    ),
    "packageEvidence": (
        {
            "id",
            "gtin",
            "market",
            "purpose",
            "sha256",
            "observedAt",
            "consentState",
            "privacyState",
            "verificationState",
            "internalReference",
        },
        {"redactionState"},
    ),
    "certifications": (
        {
            "id",
            "certifier",
            "scheme",
            "certificateReference",
            "gtin",
            "market",
            "matchBasis",
            "scope",
            "sourceKey",
            "sourceRecordID",
            "retrievedAt",
            "lastCheckedAt",
        },
        {
            "sourceRevision",
            "issueAt",
            "effectiveAt",
            "expiryAt",
            "revokedAt",
            "suspendedAt",
            "facility",
            "batch",
            "evidenceHash",
        },
    ),
    "reviews": (
        {
            "id",
            "targetID",
            "targetType",
            "state",
            "reviewerID",
            "reviewedAt",
            "decisionCode",
            "reason",
        },
        {"methodologyVersion", "toolContext"},
    ),
    "assessments": (
        {
            "id",
            "gtin",
            "market",
            "status",
            "methodologyVersion",
            "assessedAt",
            "certificationIDs",
            "evidenceIDs",
            "reasons",
        },
        {"ingredientObservationID", "recheckAt"},
    ),
    "validityEvents": (
        {"id", "assessmentID", "kind", "occurredAt", "reason"},
        {"triggeredByEvidenceID"},
    ),
    "currentSelections": (
        {
            "id",
            "gtin",
            "market",
            "identityObservationID",
            "certificationIDs",
            "retailerEvidenceIDs",
            "remoteImageIDs",
            "conflictFlags",
        },
        {"ingredientObservationID", "assessmentID"},
    ),
    "releases": (
        {
            "id",
            "catalogVersion",
            "methodologyVersion",
            "selectionPolicyVersion",
            "generatedAt",
            "builderVersion",
            "commitSHA",
            "runtimeDigest",
            "sourceSnapshots",
            "counts",
        },
        set(),
    ),
}

SET_LIKE_FIELDS = {
    "identities": {"categories", "packaging"},
    "assessments": {"certificationIDs", "evidenceIDs"},
    "currentSelections": {
        "certificationIDs",
        "retailerEvidenceIDs",
        "remoteImageIDs",
        "conflictFlags",
    },
}


class EvidenceValidationError(ValueError):
    """Raised when an evidence envelope violates the v1 contract."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def formulation_hash(record: dict[str, Any]) -> str:
    payload = {
        "ingredientsText": record["ingredientsText"],
        "allergensText": record.get("allergensText"),
        "tracesText": record.get("tracesText"),
    }
    return sha256_text(canonical_json(payload))


def _canonical_record_for_id(collection: str, record: dict[str, Any]) -> dict[str, Any]:
    value = copy.deepcopy(record)
    value.pop("id", None)
    for field in SET_LIKE_FIELDS.get(collection, set()):
        if field in value:
            values = value[field]
            if not isinstance(values, list):
                raise EvidenceValidationError(f"{collection}.{field} must be an array")
            value[field] = sorted(values)
    if collection == "releases" and "sourceSnapshots" in value:
        value["sourceSnapshots"] = sorted(
            value["sourceSnapshots"],
            key=lambda item: (
                str(item.get("sourceKey", "")),
                str(item.get("snapshotID", "")),
                str(item.get("digest", "")),
            ),
        )
    return value


def derive_id(collection: str, record: dict[str, Any]) -> str:
    if collection not in COLLECTION_KIND:
        raise EvidenceValidationError(f"unsupported ID collection {collection!r}")
    digest = sha256_text(canonical_json(_canonical_record_for_id(collection, record)))
    return f"{ID_PREFIX}:{COLLECTION_KIND[collection]}:sha256:{digest}"


def _fail(path: str, message: str) -> None:
    raise EvidenceValidationError(f"{path}: {message}")


def _require_string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        _fail(path, "must be a string")
    if not allow_empty and not value.strip():
        _fail(path, "must not be blank")
    return value


def _require_array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    return value


def _validate_timestamp(value: Any, path: str) -> None:
    text = _require_string(value, path)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise EvidenceValidationError(f"{path}: invalid ISO-8601 timestamp {text!r}") from error
    if parsed.tzinfo is None:
        _fail(path, "timestamp must include an explicit timezone")


def _validate_optional_timestamps(record: dict[str, Any], path: str) -> None:
    for key, value in record.items():
        if key.endswith("At") and value is not None:
            _validate_timestamp(value, f"{path}.{key}")


def _validate_market(value: Any, path: str) -> str:
    market = _require_string(value, path)
    if not MARKET_RE.fullmatch(market):
        _fail(path, "must be an uppercase ISO-3166-style alpha-2 market code")
    return market


def _validate_language(value: Any, path: str) -> None:
    language = _require_string(value, path)
    if not LANGUAGE_RE.fullmatch(language):
        _fail(path, "must be a supported BCP-47-style language tag or 'und'")


def _valid_gtin_check_digit(value: str) -> bool:
    total = 0
    for offset, character in enumerate(reversed(value[:-1])):
        total += int(character) * (3 if offset % 2 == 0 else 1)
    return (10 - total % 10) % 10 == int(value[-1])


def _validate_gtin(value: Any, path: str) -> str:
    gtin = _require_string(value, path)
    if len(gtin) != 14 or not gtin.isascii() or not gtin.isdigit():
        _fail(path, "must be the canonical 14-digit GTIN with leading zeros preserved")
    if not _valid_gtin_check_digit(gtin):
        _fail(path, "has an invalid GTIN check digit")
    return gtin


def _validate_sha256(value: Any, path: str) -> str:
    digest = _require_string(value, path)
    if not SHA256_RE.fullmatch(digest):
        _fail(path, "must be a lowercase 64-character SHA-256 hex digest")
    return digest


def _validate_enum(value: Any, enum_name: str, path: str) -> str:
    text = _require_string(value, path)
    if text not in ENUMS[enum_name]:
        _fail(path, f"unsupported value {text!r}; allowed={sorted(ENUMS[enum_name])}")
    return text


def _validate_shape(collection: str, record: Any, index: int) -> dict[str, Any]:
    path = f"{collection}[{index}]"
    if not isinstance(record, dict):
        _fail(path, "must be an object")
    required, optional = FIELDS[collection]
    missing = sorted(required - set(record))
    if missing:
        _fail(path, f"missing required fields {missing}")
    unknown = sorted(set(record) - required - optional)
    if unknown:
        _fail(path, f"unknown fields {unknown}")
    return record


def _record_maps(data: dict[str, Any]) -> dict[str, dict[str, dict[str, Any]]]:
    result: dict[str, dict[str, dict[str, Any]]] = {}
    seen_ids: set[str] = set()
    for collection in COLLECTIONS:
        records = _require_array(data.get(collection), collection)
        mapping: dict[str, dict[str, Any]] = {}
        for index, raw in enumerate(records):
            record = _validate_shape(collection, raw, index)
            record_id = _require_string(record["id"], f"{collection}[{index}].id")
            if not ID_RE.fullmatch(record_id):
                _fail(f"{collection}[{index}].id", "must use hfeu:<kind>:sha256:<64-hex> format")
            if record_id in seen_ids:
                _fail(f"{collection}[{index}].id", f"duplicate global evidence ID {record_id}")
            expected = derive_id(collection, record)
            if record_id != expected:
                _fail(
                    f"{collection}[{index}].id",
                    f"does not match deterministic derivation; expected {expected}",
                )
            seen_ids.add(record_id)
            mapping[record_id] = record
        result[collection] = mapping
    return result


def _validate_source(record: dict[str, Any], path: str) -> None:
    source_key = _require_string(record["sourceKey"], f"{path}.sourceKey")
    if not SOURCE_KEY_RE.fullmatch(source_key):
        _fail(f"{path}.sourceKey", "must be a stable lowercase source key")
    _require_string(record["operator"], f"{path}.operator")
    _validate_enum(record["sourceClass"], "sourceClass", f"{path}.sourceClass")
    _require_string(record["reference"], f"{path}.reference")
    _validate_enum(record["accessMethod"], "accessMethod", f"{path}.accessMethod")
    markets = _require_array(record["markets"], f"{path}.markets")
    if not markets:
        _fail(f"{path}.markets", "must not be empty")
    for index, market in enumerate(markets):
        _validate_market(market, f"{path}.markets[{index}]")
    _validate_optional_timestamps(record, path)


def _validate_common_evidence(
    record: dict[str, Any],
    path: str,
    source_keys: set[str],
) -> tuple[str, str]:
    gtin = _validate_gtin(record["gtin"], f"{path}.gtin")
    market = _validate_market(record["market"], f"{path}.market")
    source_key = _require_string(record["sourceKey"], f"{path}.sourceKey")
    if source_key not in source_keys:
        _fail(f"{path}.sourceKey", f"unknown source {source_key!r}")
    _require_string(record["sourceRecordID"], f"{path}.sourceRecordID")
    _validate_optional_timestamps(record, path)
    return gtin, market


def _detect_supersession_cycles(
    ingredients: dict[str, dict[str, Any]],
) -> None:
    for start_id in ingredients:
        seen: set[str] = set()
        current_id = start_id
        while True:
            current = ingredients[current_id]
            parent = current.get("supersedesID")
            if parent is None:
                break
            if parent in seen or parent == start_id:
                _fail("ingredients", f"supersession cycle involving {start_id}")
            seen.add(parent)
            if parent not in ingredients:
                _fail(f"ingredients[{current_id}].supersedesID", f"unknown ingredient ID {parent}")
            current_id = parent


def _validate_reason(reason: Any, path: str, evidence_ids: set[str]) -> None:
    if not isinstance(reason, dict):
        _fail(path, "must be an object")
    required = {"code", "title", "detail", "severity", "evidenceIDs"}
    optional = {"ingredient"}
    missing = sorted(required - set(reason))
    unknown = sorted(set(reason) - required - optional)
    if missing:
        _fail(path, f"missing required fields {missing}")
    if unknown:
        _fail(path, f"unknown fields {unknown}")
    for key in ("code", "title", "detail"):
        _require_string(reason[key], f"{path}.{key}")
    _validate_enum(reason["severity"], "reasonSeverity", f"{path}.severity")
    ids = _require_array(reason["evidenceIDs"], f"{path}.evidenceIDs")
    if not ids:
        _fail(f"{path}.evidenceIDs", "must not be empty")
    for index, evidence_id in enumerate(ids):
        evidence_id = _require_string(evidence_id, f"{path}.evidenceIDs[{index}]")
        if evidence_id not in evidence_ids:
            _fail(f"{path}.evidenceIDs[{index}]", f"unknown evidence ID {evidence_id}")


def validate_envelope(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        _fail("$", "evidence envelope must be a JSON object")

    allowed_top = {"schemaVersion", "sources", *COLLECTIONS}
    unknown_top = sorted(set(data) - allowed_top)
    if unknown_top:
        _fail("$", f"unknown top-level fields {unknown_top}")

    if data.get("schemaVersion") != SCHEMA_VERSION:
        _fail(
            "schemaVersion",
            f"unsupported evidence schema version {data.get('schemaVersion')!r}; expected {SCHEMA_VERSION}",
        )

    sources = _require_array(data.get("sources"), "sources")
    source_keys: set[str] = set()
    for index, raw in enumerate(sources):
        record = _validate_shape("sources", raw, index)
        _validate_source(record, f"sources[{index}]")
        source_key = record["sourceKey"]
        if source_key in source_keys:
            _fail(f"sources[{index}].sourceKey", f"duplicate source key {source_key}")
        source_keys.add(source_key)

    maps = _record_maps(data)

    key_by_id: dict[str, tuple[str, str]] = {}
    evidence_ids: set[str] = set()
    for collection, records in maps.items():
        if collection not in {"validityEvents", "currentSelections", "releases"}:
            evidence_ids.update(records)
        for record_id, record in records.items():
            if "gtin" in record and "market" in record:
                key_by_id[record_id] = (
                    _validate_gtin(record["gtin"], f"{collection}[{record_id}].gtin"),
                    _validate_market(record["market"], f"{collection}[{record_id}].market"),
                )

    for record_id, record in maps["identities"].items():
        path = f"identities[{record_id}]"
        _validate_common_evidence(record, path, source_keys)
        _require_string(record["originalBarcode"], f"{path}.originalBarcode")
        _require_string(record["name"], f"{path}.name")
        _validate_enum(record["confidence"], "identityConfidence", f"{path}.confidence")
        for field in ("categories", "packaging"):
            if field in record:
                values = _require_array(record[field], f"{path}.{field}")
                for index, value in enumerate(values):
                    _require_string(value, f"{path}.{field}[{index}]")

    for record_id, record in maps["ingredients"].items():
        path = f"ingredients[{record_id}]"
        gtin, market = _validate_common_evidence(record, path, source_keys)
        _require_string(record["ingredientsText"], f"{path}.ingredientsText")
        _validate_language(record["languageCode"], f"{path}.languageCode")
        actual_hash = _validate_sha256(record["contentHash"], f"{path}.contentHash")
        expected_hash = formulation_hash(record)
        if actual_hash != expected_hash:
            _fail(
                f"{path}.contentHash",
                f"does not match exact formulation text; expected {expected_hash}",
            )
        _validate_enum(record["captureMethod"], "captureMethod", f"{path}.captureMethod")
        _validate_enum(
            record["verificationState"],
            "verificationState",
            f"{path}.verificationState",
        )
        if "supersedesID" in record:
            parent_id = _require_string(record["supersedesID"], f"{path}.supersedesID")
            parent = maps["ingredients"].get(parent_id)
            if parent is None:
                _fail(f"{path}.supersedesID", f"unknown ingredient ID {parent_id}")
            if (parent["gtin"], parent["market"]) != (gtin, market):
                _fail(f"{path}.supersedesID", "cannot supersede another GTIN or market")
        if "transformation" in record:
            transformation = record["transformation"]
            if not isinstance(transformation, dict):
                _fail(f"{path}.transformation", "must be an object")
            allowed = {"tool", "version", "confidence", "language"}
            unknown = sorted(set(transformation) - allowed)
            if unknown:
                _fail(f"{path}.transformation", f"unknown fields {unknown}")
            for key in ("tool", "version"):
                if key in transformation:
                    _require_string(transformation[key], f"{path}.transformation.{key}")
            if "confidence" in transformation:
                confidence = transformation["confidence"]
                if not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
                    _fail(f"{path}.transformation.confidence", "must be between 0 and 1")
            if "language" in transformation:
                _validate_language(
                    transformation["language"],
                    f"{path}.transformation.language",
                )

    _detect_supersession_cycles(maps["ingredients"])

    for record_id, record in maps["retailerEvidence"].items():
        path = f"retailerEvidence[{record_id}]"
        _validate_common_evidence(record, path, source_keys)
        _validate_enum(record["kind"], "retailerKind", f"{path}.kind")
        _require_string(record["retailerKey"], f"{path}.retailerKey")
        _require_string(record["confidence"], f"{path}.confidence")
        _require_string(record["limitations"], f"{path}.limitations")
        if record["kind"] == "retailer-feed-listing" and "snapshotAt" not in record:
            _fail(path, "retailer-feed-listing requires snapshotAt")
        if record["kind"] == "retailer-observation" and "observedAt" not in record:
            _fail(path, "retailer-observation requires observedAt")

    for record_id, record in maps["remoteImages"].items():
        path = f"remoteImages[{record_id}]"
        gtin = _validate_gtin(record["gtin"], f"{path}.gtin")
        market = _validate_market(record["market"], f"{path}.market")
        source_key = _require_string(record["sourceKey"], f"{path}.sourceKey")
        if source_key not in source_keys:
            _fail(f"{path}.sourceKey", f"unknown source {source_key!r}")
        _validate_enum(record["purpose"], "imagePurpose", f"{path}.purpose")
        url = _require_string(record["url"], f"{path}.url")
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            _fail(f"{path}.url", "must be an absolute HTTPS URL")
        _require_string(record["imageID"], f"{path}.imageID")
        for field in ("width", "height"):
            if field in record and (
                not isinstance(record[field], int) or record[field] <= 0
            ):
                _fail(f"{path}.{field}", "must be a positive integer")
        _validate_optional_timestamps(record, path)
        key_by_id[record_id] = (gtin, market)

    for record_id, record in maps["packageEvidence"].items():
        path = f"packageEvidence[{record_id}]"
        _validate_gtin(record["gtin"], f"{path}.gtin")
        _validate_market(record["market"], f"{path}.market")
        _validate_enum(record["purpose"], "packagePurpose", f"{path}.purpose")
        _validate_sha256(record["sha256"], f"{path}.sha256")
        _validate_enum(record["consentState"], "consentState", f"{path}.consentState")
        _validate_enum(record["privacyState"], "privacyState", f"{path}.privacyState")
        _validate_enum(
            record["verificationState"],
            "verificationState",
            f"{path}.verificationState",
        )
        _require_string(record["internalReference"], f"{path}.internalReference")
        _validate_optional_timestamps(record, path)

    for record_id, record in maps["certifications"].items():
        path = f"certifications[{record_id}]"
        _validate_common_evidence(record, path, source_keys)
        for field in (
            "certifier",
            "scheme",
            "certificateReference",
            "matchBasis",
            "scope",
        ):
            _require_string(record[field], f"{path}.{field}")
        if "evidenceHash" in record:
            _validate_sha256(record["evidenceHash"], f"{path}.evidenceHash")

    target_maps = {
        "identity": maps["identities"],
        "ingredient": maps["ingredients"],
        "retailer": maps["retailerEvidence"],
        "package-evidence": maps["packageEvidence"],
        "certification": maps["certifications"],
        "assessment": maps["assessments"],
    }

    for record_id, record in maps["reviews"].items():
        path = f"reviews[{record_id}]"
        target_type = _validate_enum(
            record["targetType"],
            "reviewTargetType",
            f"{path}.targetType",
        )
        target_id = _require_string(record["targetID"], f"{path}.targetID")
        if target_id not in target_maps[target_type]:
            _fail(f"{path}.targetID", f"unknown {target_type} ID {target_id}")
        _validate_enum(record["state"], "reviewState", f"{path}.state")
        _require_string(record["reviewerID"], f"{path}.reviewerID")
        _require_string(record["decisionCode"], f"{path}.decisionCode")
        _require_string(record["reason"], f"{path}.reason")
        _validate_optional_timestamps(record, path)

    for record_id, record in maps["assessments"].items():
        path = f"assessments[{record_id}]"
        gtin = _validate_gtin(record["gtin"], f"{path}.gtin")
        market = _validate_market(record["market"], f"{path}.market")
        status = _validate_enum(record["status"], "assessmentStatus", f"{path}.status")
        _require_string(record["methodologyVersion"], f"{path}.methodologyVersion")
        _validate_optional_timestamps(record, path)

        ingredient_id = record.get("ingredientObservationID")
        if ingredient_id is not None:
            ingredient_id = _require_string(
                ingredient_id,
                f"{path}.ingredientObservationID",
            )
            ingredient = maps["ingredients"].get(ingredient_id)
            if ingredient is None:
                _fail(f"{path}.ingredientObservationID", f"unknown ingredient ID {ingredient_id}")
            if (ingredient["gtin"], ingredient["market"]) != (gtin, market):
                _fail(f"{path}.ingredientObservationID", "belongs to another GTIN/market")
        elif status != "unknown":
            _fail(path, "only unknown assessment may omit ingredientObservationID")

        certification_ids = _require_array(
            record["certificationIDs"],
            f"{path}.certificationIDs",
        )
        for index, certification_id in enumerate(certification_ids):
            certification_id = _require_string(
                certification_id,
                f"{path}.certificationIDs[{index}]",
            )
            certification = maps["certifications"].get(certification_id)
            if certification is None:
                _fail(
                    f"{path}.certificationIDs[{index}]",
                    f"unknown certification ID {certification_id}",
                )
            if (certification["gtin"], certification["market"]) != (gtin, market):
                _fail(
                    f"{path}.certificationIDs[{index}]",
                    "belongs to another GTIN/market",
                )
        if status == "halal-certified" and not certification_ids:
            _fail(path, "halal-certified assessment requires certification evidence")

        linked_evidence = _require_array(record["evidenceIDs"], f"{path}.evidenceIDs")
        for index, evidence_id in enumerate(linked_evidence):
            evidence_id = _require_string(evidence_id, f"{path}.evidenceIDs[{index}]")
            if evidence_id not in evidence_ids:
                _fail(f"{path}.evidenceIDs[{index}]", f"unknown evidence ID {evidence_id}")
            key = key_by_id.get(evidence_id)
            if key is not None and key != (gtin, market):
                _fail(
                    f"{path}.evidenceIDs[{index}]",
                    "belongs to another GTIN/market",
                )

        reasons = _require_array(record["reasons"], f"{path}.reasons")
        if not reasons:
            _fail(f"{path}.reasons", "must contain at least one structured reason")
        for index, reason in enumerate(reasons):
            _validate_reason(reason, f"{path}.reasons[{index}]", evidence_ids)

    assessment_events: dict[str, list[dict[str, Any]]] = {}
    for record_id, record in maps["validityEvents"].items():
        path = f"validityEvents[{record_id}]"
        assessment_id = _require_string(record["assessmentID"], f"{path}.assessmentID")
        if assessment_id not in maps["assessments"]:
            _fail(f"{path}.assessmentID", f"unknown assessment ID {assessment_id}")
        _validate_enum(record["kind"], "validityKind", f"{path}.kind")
        _validate_optional_timestamps(record, path)
        _require_string(record["reason"], f"{path}.reason")
        trigger_id = record.get("triggeredByEvidenceID")
        if trigger_id is not None and trigger_id not in evidence_ids:
            _fail(f"{path}.triggeredByEvidenceID", f"unknown evidence ID {trigger_id}")
        assessment_events.setdefault(assessment_id, []).append(record)

    approved_reviews_by_target: dict[str, list[dict[str, Any]]] = {}
    terminal_bad_reviews: dict[str, list[dict[str, Any]]] = {}
    for review in maps["reviews"].values():
        if review["state"] == "approved":
            approved_reviews_by_target.setdefault(review["targetID"], []).append(review)
        elif review["state"] in {"rejected", "superseded"}:
            terminal_bad_reviews.setdefault(review["targetID"], []).append(review)

    superseded_ingredient_ids = {
        record["supersedesID"]
        for record in maps["ingredients"].values()
        if record.get("supersedesID") is not None
    }

    seen_selection_keys: set[tuple[str, str]] = set()
    for record_id, record in maps["currentSelections"].items():
        path = f"currentSelections[{record_id}]"
        gtin = _validate_gtin(record["gtin"], f"{path}.gtin")
        market = _validate_market(record["market"], f"{path}.market")
        key = (gtin, market)
        if key in seen_selection_keys:
            _fail(path, f"duplicate current selection for GTIN/market {key}")
        seen_selection_keys.add(key)

        identity_id = _require_string(
            record["identityObservationID"],
            f"{path}.identityObservationID",
        )
        identity = maps["identities"].get(identity_id)
        if identity is None or (identity["gtin"], identity["market"]) != key:
            _fail(f"{path}.identityObservationID", "must reference matching identity evidence")

        ingredient_id = record.get("ingredientObservationID")
        if ingredient_id is not None:
            ingredient = maps["ingredients"].get(ingredient_id)
            if ingredient is None or (ingredient["gtin"], ingredient["market"]) != key:
                _fail(f"{path}.ingredientObservationID", "must reference matching ingredient evidence")
            if ingredient_id in superseded_ingredient_ids:
                _fail(f"{path}.ingredientObservationID", "cannot select a superseded ingredient observation")

        assessment_id = record.get("assessmentID")
        if assessment_id is not None:
            assessment = maps["assessments"].get(assessment_id)
            if assessment is None or (assessment["gtin"], assessment["market"]) != key:
                _fail(f"{path}.assessmentID", "must reference matching assessment")
            if assessment.get("ingredientObservationID") != ingredient_id:
                if not (
                    assessment["status"] == "unknown"
                    and assessment.get("ingredientObservationID") is None
                    and ingredient_id is None
                ):
                    _fail(f"{path}.assessmentID", "does not bind to the selected formulation")
            if assessment_id not in approved_reviews_by_target:
                _fail(f"{path}.assessmentID", "current assessment requires an approved review")
            if assessment_id in terminal_bad_reviews:
                _fail(f"{path}.assessmentID", "assessment has a rejected/superseded review")
            active_events = sorted(
                assessment_events.get(assessment_id, []),
                key=lambda event: event["occurredAt"],
            )
            valid = True
            for event in active_events:
                valid = event["kind"] == "restored"
            if not valid:
                _fail(f"{path}.assessmentID", "assessment is invalidated/superseded")

        if ingredient_id is None and assessment_id is not None:
            if maps["assessments"][assessment_id]["status"] != "unknown":
                _fail(f"{path}.assessmentID", "missing formulation may only select unknown assessment")

        for field, mapping in (
            ("certificationIDs", maps["certifications"]),
            ("retailerEvidenceIDs", maps["retailerEvidence"]),
            ("remoteImageIDs", maps["remoteImages"]),
        ):
            ids = _require_array(record[field], f"{path}.{field}")
            for index, linked_id in enumerate(ids):
                linked_id = _require_string(linked_id, f"{path}.{field}[{index}]")
                linked = mapping.get(linked_id)
                if linked is None:
                    _fail(f"{path}.{field}[{index}]", f"unknown ID {linked_id}")
                if (linked["gtin"], linked["market"]) != key:
                    _fail(f"{path}.{field}[{index}]", "belongs to another GTIN/market")

        flags = _require_array(record["conflictFlags"], f"{path}.conflictFlags")
        for index, flag in enumerate(flags):
            _require_string(flag, f"{path}.conflictFlags[{index}]")

    for record_id, record in maps["releases"].items():
        path = f"releases[{record_id}]"
        for field in ("catalogVersion", "methodologyVersion", "selectionPolicyVersion", "builderVersion"):
            _require_string(record[field], f"{path}.{field}")
        _validate_optional_timestamps(record, path)
        commit = _require_string(record["commitSHA"], f"{path}.commitSHA")
        if not COMMIT_RE.fullmatch(commit):
            _fail(f"{path}.commitSHA", "must be lowercase hexadecimal commit identifier")
        _validate_sha256(record["runtimeDigest"], f"{path}.runtimeDigest")
        snapshots = _require_array(record["sourceSnapshots"], f"{path}.sourceSnapshots")
        for index, snapshot in enumerate(snapshots):
            if not isinstance(snapshot, dict):
                _fail(f"{path}.sourceSnapshots[{index}]", "must be an object")
            required = {"sourceKey", "snapshotID", "digest", "retrievedAt"}
            unknown = sorted(set(snapshot) - required)
            missing = sorted(required - set(snapshot))
            if missing or unknown:
                _fail(
                    f"{path}.sourceSnapshots[{index}]",
                    f"missing={missing}, unknown={unknown}",
                )
            if snapshot["sourceKey"] not in source_keys:
                _fail(
                    f"{path}.sourceSnapshots[{index}].sourceKey",
                    "unknown source",
                )
            _require_string(snapshot["snapshotID"], f"{path}.sourceSnapshots[{index}].snapshotID")
            _validate_sha256(snapshot["digest"], f"{path}.sourceSnapshots[{index}].digest")
            _validate_timestamp(snapshot["retrievedAt"], f"{path}.sourceSnapshots[{index}].retrievedAt")
        counts = record["counts"]
        if not isinstance(counts, dict) or not counts:
            _fail(f"{path}.counts", "must be a non-empty object")
        for name, value in counts.items():
            if not isinstance(name, str) or not name:
                _fail(f"{path}.counts", "count keys must be non-empty strings")
            if not isinstance(value, int) or value < 0:
                _fail(f"{path}.counts.{name}", "must be a non-negative integer")

    return data


def runtime_projection(data: dict[str, Any]) -> dict[str, Any]:
    validate_envelope(data)
    maps = _record_maps(data)
    source_map = {source["sourceKey"]: source for source in data["sources"]}

    used_sources: set[str] = set()
    products: list[dict[str, Any]] = []
    for selection in sorted(
        maps["currentSelections"].values(),
        key=lambda record: (record["gtin"], record["market"]),
    ):
        identity = maps["identities"][selection["identityObservationID"]]
        ingredient = (
            maps["ingredients"][selection["ingredientObservationID"]]
            if selection.get("ingredientObservationID")
            else None
        )
        assessment = (
            maps["assessments"][selection["assessmentID"]]
            if selection.get("assessmentID")
            else None
        )
        certifications = [
            maps["certifications"][record_id]
            for record_id in sorted(selection["certificationIDs"])
        ]
        retailer_evidence = [
            maps["retailerEvidence"][record_id]
            for record_id in sorted(selection["retailerEvidenceIDs"])
        ]
        remote_images = [
            maps["remoteImages"][record_id]
            for record_id in sorted(selection["remoteImageIDs"])
        ]

        used_sources.add(identity["sourceKey"])
        if ingredient:
            used_sources.add(ingredient["sourceKey"])
        used_sources.update(item["sourceKey"] for item in certifications)
        used_sources.update(item["sourceKey"] for item in retailer_evidence)
        used_sources.update(item["sourceKey"] for item in remote_images)

        product = {
            "gtin": selection["gtin"],
            "market": selection["market"],
            "selectionID": selection["id"],
            "identity": {
                "id": identity["id"],
                "name": identity["name"],
                "brand": identity.get("brand"),
                "brandOwner": identity.get("brandOwner"),
                "quantity": identity.get("quantity"),
                "categories": sorted(identity.get("categories", [])),
                "sourceKey": identity["sourceKey"],
                "sourceRecordID": identity["sourceRecordID"],
                "retrievedAt": identity["retrievedAt"],
            },
            "ingredients": None,
            "assessment": None,
            "certifications": [
                {
                    "id": item["id"],
                    "certifier": item["certifier"],
                    "scheme": item["scheme"],
                    "certificateReference": item["certificateReference"],
                    "scope": item["scope"],
                    "effectiveAt": item.get("effectiveAt"),
                    "expiryAt": item.get("expiryAt"),
                    "lastCheckedAt": item["lastCheckedAt"],
                    "sourceKey": item["sourceKey"],
                }
                for item in certifications
            ],
            "retailerEvidence": [
                {
                    "id": item["id"],
                    "kind": item["kind"],
                    "retailerKey": item["retailerKey"],
                    "observedAt": item.get("observedAt"),
                    "snapshotAt": item.get("snapshotAt"),
                    "scope": item.get("scope"),
                    "locationID": item.get("locationID"),
                    "limitations": item["limitations"],
                    "sourceKey": item["sourceKey"],
                }
                for item in retailer_evidence
            ],
            "remoteImages": [
                {
                    "id": item["id"],
                    "purpose": item["purpose"],
                    "url": item["url"],
                    "sourceKey": item["sourceKey"],
                    "imageID": item["imageID"],
                    "revision": item.get("revision"),
                }
                for item in remote_images
            ],
            "conflictFlags": sorted(selection["conflictFlags"]),
        }
        if ingredient:
            product["ingredients"] = {
                "id": ingredient["id"],
                "text": ingredient["ingredientsText"],
                "languageCode": ingredient["languageCode"],
                "allergensText": ingredient.get("allergensText"),
                "tracesText": ingredient.get("tracesText"),
                "observedAt": ingredient.get("observedAt"),
                "retrievedAt": ingredient["retrievedAt"],
                "contentHash": ingredient["contentHash"],
                "sourceKey": ingredient["sourceKey"],
                "sourceRecordID": ingredient["sourceRecordID"],
                "verificationState": ingredient["verificationState"],
            }
        if assessment:
            product["assessment"] = {
                "id": assessment["id"],
                "status": assessment["status"],
                "methodologyVersion": assessment["methodologyVersion"],
                "assessedAt": assessment["assessedAt"],
                "recheckAt": assessment.get("recheckAt"),
                "reasons": copy.deepcopy(assessment["reasons"]),
            }
        products.append(product)

    sources = [
        {
            "sourceKey": key,
            "operator": source_map[key]["operator"],
            "sourceClass": source_map[key]["sourceClass"],
            "reference": source_map[key]["reference"],
            "retrievedAt": source_map[key]["retrievedAt"],
        }
        for key in sorted(used_sources)
    ]

    return {
        "schemaVersion": 1,
        "evidenceSchemaVersion": data["schemaVersion"],
        "sources": sources,
        "products": products,
    }


def load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise EvidenceValidationError("top-level JSON value must be an object")
    return value


def write_projection(input_path: Path, output_path: Path) -> None:
    projection = runtime_projection(load_json(input_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(projection, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate an evidence envelope")
    validate.add_argument("--input", required=True, type=Path)

    project = subparsers.add_parser("project", help="emit minimal deterministic runtime projection")
    project.add_argument("--input", required=True, type=Path)
    project.add_argument("--output", required=True, type=Path)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "validate":
        data = load_json(args.input)
        validate_envelope(data)
        print(
            f"Validated evidence schema v{data['schemaVersion']} with "
            f"{len(data['currentSelections'])} current product selections"
        )
    else:
        write_projection(args.input, args.output)
        print(f"Wrote runtime projection to {args.output}")


if __name__ == "__main__":
    main()
