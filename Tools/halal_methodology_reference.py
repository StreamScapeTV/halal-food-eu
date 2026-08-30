"""Validation and lookup for identity-only additive reference data."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any


class AdditiveReferenceError(ValueError):
    pass


def _timestamp(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AdditiveReferenceError(f"{field} must be a non-blank RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AdditiveReferenceError(f"{field} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise AdditiveReferenceError(f"{field} must include a timezone")


def validate_additive_identities(raw: dict[str, Any]) -> None:
    expected = {"schemaVersion", "datasetVersion", "reviewedAt", "identityOnly", "entries"}
    if set(raw) != expected or raw.get("schemaVersion") != 1:
        raise AdditiveReferenceError("additive identity data has unsupported schema or fields")
    if not isinstance(raw.get("datasetVersion"), str) or not raw["datasetVersion"].strip():
        raise AdditiveReferenceError("datasetVersion must be non-blank")
    _timestamp(raw.get("reviewedAt"), "reviewedAt")
    if raw.get("identityOnly") is not True:
        raise AdditiveReferenceError("additive identity data must remain identity-only")
    entries = raw.get("entries")
    if not isinstance(entries, list) or not entries:
        raise AdditiveReferenceError("entries must be a non-empty array")
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        required = {"id", "names", "originConclusion", "halalConclusion", "references"}
        if not isinstance(entry, dict) or set(entry) != required:
            raise AdditiveReferenceError(f"entries[{index}] fields mismatch")
        additive_id = entry.get("id")
        if not isinstance(additive_id, str) or re.fullmatch(r"E[0-9]{3,4}[a-z]?", additive_id) is None:
            raise AdditiveReferenceError(f"entries[{index}].id is invalid")
        if additive_id in seen:
            raise AdditiveReferenceError(f"duplicate additive identity {additive_id}")
        seen.add(additive_id)
        names = entry.get("names")
        if not isinstance(names, dict) or not names:
            raise AdditiveReferenceError(f"entries[{index}].names must be non-empty")
        for language, values in names.items():
            if not isinstance(language, str) or not language.strip():
                raise AdditiveReferenceError(f"entries[{index}] contains a blank language key")
            if not isinstance(values, list) or not values or any(not isinstance(value, str) or not value.strip() for value in values):
                raise AdditiveReferenceError(f"entries[{index}].names[{language!r}] must contain non-blank strings")
            folded = [value.casefold() for value in values]
            if len(folded) != len(set(folded)):
                raise AdditiveReferenceError(f"entries[{index}].names[{language!r}] contains duplicates")
        if entry.get("originConclusion") != "unknown-without-evidence":
            raise AdditiveReferenceError("additive identity reference must not infer ingredient origin")
        if entry.get("halalConclusion") is not None:
            raise AdditiveReferenceError("additive identity reference must not encode a halal conclusion")
        references = entry.get("references")
        if not isinstance(references, list) or not references or any(not isinstance(value, str) or not value.strip() for value in references):
            raise AdditiveReferenceError(f"entries[{index}].references must be non-empty")


def additive_lookup(raw: dict[str, Any]) -> dict[str, dict[str, Any]]:
    validate_additive_identities(raw)
    return {entry["id"]: entry for entry in raw["entries"]}
