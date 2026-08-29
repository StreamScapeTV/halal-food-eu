#!/usr/bin/env python3
"""Versioned contracts and validation for Germany catalog selection."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = 1
MARKET_RE = re.compile(r"^[A-Z]{2}$")
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SIGNAL_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
SOURCE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
IMAGE_PURPOSES = {"front", "ingredients", "barcode", "nutrition", "certification"}

POLICY_FIELDS = (
    {
        "schemaVersion",
        "policyVersion",
        "targetMarket",
        "allowedProductTypes",
        "acceptedBarcodeKinds",
        "includeCategorySignals",
        "includeFormulationSignals",
        "includeEvidenceSignals",
        "basicRules",
        "auditSampleSize",
    },
    set(),
)

BUNDLE_FIELDS = (
    {"schemaVersion", "sourceSnapshot", "candidates"},
    set(),
)

SOURCE_SNAPSHOT_FIELDS = (
    {"sourceKey", "snapshotID", "sourceSchemaVersion", "taxonomyVersion", "retrievedAt"},
    set(),
)

CANDIDATE_FIELDS = (
    {
        "sourceRecordID",
        "barcode",
        "market",
        "productType",
        "barcodeKind",
        "name",
        "categoryTags",
        "categorySignals",
        "formulationSignals",
        "evidenceSignals",
        "retailerKeys",
        "remoteImages",
    },
    {
        "brand",
        "ingredientsText",
        "ingredientCount",
        "packageSignals",
    },
)

REMOTE_IMAGE_FIELDS = (
    {"purpose", "url", "sourceKey", "imageID"},
    {"revision", "width", "height"},
)

BASIC_RULE_FIELDS = (
    {"code", "categorySignals", "maxIngredientCount", "allowUnknownIngredientCount"},
    set(),
)


class SelectionValidationError(ValueError):
    """Raised when policy/candidate input violates the selection contract."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fail(path: str, message: str) -> None:
    raise SelectionValidationError(f"{path}: {message}")


def _shape(
    value: Any,
    path: str,
    fields: tuple[set[str], set[str]],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail(path, "must be an object")
    required, optional = fields
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required - optional)
    if missing:
        _fail(path, f"missing required fields {missing}")
    if unknown:
        _fail(path, f"unknown fields {unknown}")
    return value


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail(path, "must be a non-blank string")
    return value


def _optional_string(value: Any, path: str) -> str | None:
    if value is None:
        return None
    return _string(value, path)


def _string_array(value: Any, path: str, *, signal: bool = False) -> list[str]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    result: list[str] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        item = _string(raw, f"{path}[{index}]")
        if signal and not SIGNAL_RE.fullmatch(item):
            _fail(f"{path}[{index}]", "must be a lowercase kebab-case signal")
        if item in seen:
            _fail(f"{path}[{index}]", f"duplicate value {item!r}")
        seen.add(item)
        result.append(item)
    return result


def _validate_market(value: Any, path: str) -> str:
    market = _string(value, path)
    if not MARKET_RE.fullmatch(market):
        _fail(path, "must be an uppercase alpha-2 market code")
    return market


def _validate_timestamp(value: Any, path: str) -> str:
    timestamp = _string(value, path)
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as error:
        raise SelectionValidationError(
            f"{path}: invalid ISO-8601 timestamp {timestamp!r}"
        ) from error
    if parsed.tzinfo is None:
        _fail(path, "timestamp must include an explicit timezone")
    return timestamp


def _validate_check_digit(value: str) -> bool:
    total = 0
    for offset, character in enumerate(reversed(value[:-1])):
        total += int(character) * (3 if offset % 2 == 0 else 1)
    return (10 - total % 10) % 10 == int(value[-1])


def normalize_gtin(barcode: str) -> str | None:
    """Validate GTIN-8/12/13/14 and return canonical GTIN-14."""
    if not isinstance(barcode, str) or not barcode.isascii() or not barcode.isdigit():
        return None
    if len(barcode) not in {8, 12, 13, 14}:
        return None
    if not _validate_check_digit(barcode):
        return None
    return barcode.zfill(14)


def _validate_remote_image(value: Any, path: str) -> dict[str, Any]:
    image = _shape(value, path, REMOTE_IMAGE_FIELDS)
    purpose = _string(image["purpose"], f"{path}.purpose")
    if purpose not in IMAGE_PURPOSES:
        _fail(f"{path}.purpose", f"unsupported image purpose {purpose!r}")
    url = _string(image["url"], f"{path}.url")
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.netloc:
        _fail(f"{path}.url", "must be an absolute HTTPS URL")
    source_key = _string(image["sourceKey"], f"{path}.sourceKey")
    if not SOURCE_KEY_RE.fullmatch(source_key):
        _fail(f"{path}.sourceKey", "must be a stable lowercase source key")
    _string(image["imageID"], f"{path}.imageID")
    if "revision" in image:
        _optional_string(image["revision"], f"{path}.revision")
    for field in ("width", "height"):
        if field in image:
            dimension = image[field]
            if not isinstance(dimension, int) or isinstance(dimension, bool) or dimension <= 0:
                _fail(f"{path}.{field}", "must be a positive integer")
    return image


def validate_policy(data: Any) -> dict[str, Any]:
    policy = _shape(data, "$policy", POLICY_FIELDS)
    if policy["schemaVersion"] != SCHEMA_VERSION:
        _fail(
            "$policy.schemaVersion",
            f"unsupported schema version {policy['schemaVersion']!r}; expected {SCHEMA_VERSION}",
        )
    version = _string(policy["policyVersion"], "$policy.policyVersion")
    if not SEMVER_RE.fullmatch(version):
        _fail("$policy.policyVersion", "must use MAJOR.MINOR.PATCH semantic versioning")
    _validate_market(policy["targetMarket"], "$policy.targetMarket")

    for field in (
        "allowedProductTypes",
        "acceptedBarcodeKinds",
        "includeCategorySignals",
        "includeFormulationSignals",
        "includeEvidenceSignals",
    ):
        values = _string_array(
            policy[field],
            f"$policy.{field}",
            signal=field not in {"allowedProductTypes", "acceptedBarcodeKinds"},
        )
        if field in {"allowedProductTypes", "acceptedBarcodeKinds"} and not values:
            _fail(f"$policy.{field}", "must not be empty")

    rules = policy["basicRules"]
    if not isinstance(rules, list) or not rules:
        _fail("$policy.basicRules", "must be a non-empty array")
    seen_codes: set[str] = set()
    for index, raw_rule in enumerate(rules):
        path = f"$policy.basicRules[{index}]"
        rule = _shape(raw_rule, path, BASIC_RULE_FIELDS)
        code = _string(rule["code"], f"{path}.code")
        if not SIGNAL_RE.fullmatch(code):
            _fail(f"{path}.code", "must be a lowercase kebab-case reason code")
        if code in seen_codes:
            _fail(f"{path}.code", f"duplicate basic-rule code {code!r}")
        seen_codes.add(code)
        categories = _string_array(
            rule["categorySignals"],
            f"{path}.categorySignals",
            signal=True,
        )
        if not categories:
            _fail(f"{path}.categorySignals", "must not be empty")
        maximum = rule["maxIngredientCount"]
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 0:
            _fail(f"{path}.maxIngredientCount", "must be a non-negative integer")
        allow_unknown = rule["allowUnknownIngredientCount"]
        if not isinstance(allow_unknown, bool):
            _fail(f"{path}.allowUnknownIngredientCount", "must be a boolean")

    sample_size = policy["auditSampleSize"]
    if (
        not isinstance(sample_size, int)
        or isinstance(sample_size, bool)
        or sample_size < 1
        or sample_size > 100
    ):
        _fail("$policy.auditSampleSize", "must be an integer from 1 through 100")
    return policy


def validate_bundle(data: Any) -> dict[str, Any]:
    bundle = _shape(data, "$input", BUNDLE_FIELDS)
    if bundle["schemaVersion"] != SCHEMA_VERSION:
        _fail(
            "$input.schemaVersion",
            f"unsupported schema version {bundle['schemaVersion']!r}; expected {SCHEMA_VERSION}",
        )

    snapshot = _shape(bundle["sourceSnapshot"], "$input.sourceSnapshot", SOURCE_SNAPSHOT_FIELDS)
    source_key = _string(snapshot["sourceKey"], "$input.sourceSnapshot.sourceKey")
    if not SOURCE_KEY_RE.fullmatch(source_key):
        _fail("$input.sourceSnapshot.sourceKey", "must be a stable lowercase source key")
    _string(snapshot["snapshotID"], "$input.sourceSnapshot.snapshotID")
    _string(snapshot["sourceSchemaVersion"], "$input.sourceSnapshot.sourceSchemaVersion")
    _string(snapshot["taxonomyVersion"], "$input.sourceSnapshot.taxonomyVersion")
    _validate_timestamp(snapshot["retrievedAt"], "$input.sourceSnapshot.retrievedAt")

    candidates = bundle["candidates"]
    if not isinstance(candidates, list):
        _fail("$input.candidates", "must be an array")
    seen_records: set[str] = set()
    for index, raw_candidate in enumerate(candidates):
        path = f"$input.candidates[{index}]"
        candidate = _shape(raw_candidate, path, CANDIDATE_FIELDS)
        record_id = _string(candidate["sourceRecordID"], f"{path}.sourceRecordID")
        if record_id in seen_records:
            _fail(f"{path}.sourceRecordID", f"duplicate source record {record_id!r}")
        seen_records.add(record_id)
        _string(candidate["barcode"], f"{path}.barcode")
        _validate_market(candidate["market"], f"{path}.market")
        _string(candidate["productType"], f"{path}.productType")
        _string(candidate["barcodeKind"], f"{path}.barcodeKind")
        _string(candidate["name"], f"{path}.name")
        if "brand" in candidate:
            _optional_string(candidate["brand"], f"{path}.brand")
        if "ingredientsText" in candidate:
            _optional_string(candidate["ingredientsText"], f"{path}.ingredientsText")
        if "ingredientCount" in candidate and candidate["ingredientCount"] is not None:
            count = candidate["ingredientCount"]
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                _fail(f"{path}.ingredientCount", "must be null or a non-negative integer")

        _string_array(candidate["categoryTags"], f"{path}.categoryTags")
        for field in (
            "categorySignals",
            "formulationSignals",
            "evidenceSignals",
            "packageSignals",
        ):
            if field in candidate:
                _string_array(candidate[field], f"{path}.{field}", signal=True)
        _string_array(candidate["retailerKeys"], f"{path}.retailerKeys")

        images = candidate["remoteImages"]
        if not isinstance(images, list):
            _fail(f"{path}.remoteImages", "must be an array")
        for image_index, image in enumerate(images):
            _validate_remote_image(image, f"{path}.remoteImages[{image_index}]")
    return bundle
