#!/usr/bin/env python3
"""Bound and report producer-origin formulation provenance from admitted OFF data.

Open Food Facts remains the licensed source. This module never invents a direct
manufacturer source, never upgrades verification/freshness, and never creates a
halal or retailer conclusion. Producer provenance is a workflow-side audit
sidecar keyed to canonical evidence IDs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import open_food_facts_common as off

SOURCE_KEY = "open-food-facts"
PROVENANCE_KEY = "_hfeu_producer_provenance"
METADATA_KEY = "_hfeu_open_food_facts_metadata"
MAX_OWNER_LENGTH = 256
MAX_FIELD_VALUE_LENGTH = 64 * 1024
MAX_SOURCE_TEXT_LENGTH = 512
MAX_URL_LENGTH = 2048
MAX_MANUFACTURER_SOURCES = 16
MAX_DATA_SOURCE_TAGS = 32

PRODUCER_FIELDS = {
    "product_name",
    "product_name_de",
    "product_name_en",
    "generic_name",
    "generic_name_de",
    "generic_name_en",
    "brands",
    "quantity",
    "ingredients_text",
    "ingredients_text_de",
    "ingredients_text_en",
    "allergens",
    "allergens_de",
    "allergens_en",
    "traces",
    "traces_de",
    "traces_en",
}
INGREDIENT_FIELDS = {
    "ingredients_text",
    "ingredients_text_de",
    "ingredients_text_en",
}
LIMITATION = (
    "Producer provenance is mediated through Open Food Facts and identifies only the "
    "upstream origin of an exact field value. It does not prove current formulation "
    "freshness, package verification, certification, retailer availability, or halal status."
)


class ManufacturerEvidenceError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sidecar_id(value: dict[str, Any]) -> str:
    return f"hfeu:producer-provenance:sha256:{sha256_text(canonical_json(value))}"


def _bounded_text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        return None
    return value.strip()


def _exact_field_value(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_FIELD_VALUE_LENGTH:
        return None
    return value


def _safe_https(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_URL_LENGTH:
        return None
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    return value


def _manufacturer_flag(value: Any) -> bool:
    if value is True or value == 1:
        return True
    return isinstance(value, str) and value.strip().casefold() in {"1", "true", "yes"}


def _field_names(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return sorted({item for item in value if isinstance(item, str) and item in PRODUCER_FIELDS})


def _import_timestamp(value: Any) -> str | None:
    return off.timestamp_from_epoch(value)


def sanitize_producer_provenance(record: dict[str, Any]) -> dict[str, Any] | None:
    """Return only the bounded producer metadata needed for exact-field auditing."""
    result: dict[str, Any] = {}
    owner = _bounded_text(record.get("owner"), MAX_OWNER_LENGTH)
    if owner is not None:
        result["owner"] = owner

    owner_fields: dict[str, str] = {}
    raw_owner_fields = record.get("owner_fields")
    if isinstance(raw_owner_fields, dict):
        for field in sorted(PRODUCER_FIELDS):
            exact = _exact_field_value(raw_owner_fields.get(field))
            if exact is not None:
                owner_fields[field] = exact
    if owner_fields:
        result["ownerFields"] = owner_fields

    manufacturer_sources: list[dict[str, Any]] = []
    raw_sources = record.get("sources")
    if isinstance(raw_sources, list):
        for raw in raw_sources:
            if len(manufacturer_sources) >= MAX_MANUFACTURER_SOURCES:
                break
            if not isinstance(raw, dict) or not _manufacturer_flag(raw.get("manufacturer")):
                continue
            source_id = _bounded_text(raw.get("id"), MAX_SOURCE_TEXT_LENGTH)
            fields = _field_names(raw.get("fields"))
            if source_id is None or not fields:
                continue
            source: dict[str, Any] = {"sourceID": source_id, "fields": fields}
            name = _bounded_text(raw.get("name"), MAX_SOURCE_TEXT_LENGTH)
            if name is not None:
                source["sourceName"] = name
            imported = _import_timestamp(raw.get("import_t"))
            if imported is not None:
                source["importedAt"] = imported
            licence = _bounded_text(raw.get("source_licence"), MAX_SOURCE_TEXT_LENGTH)
            if licence is not None:
                source["sourceLicence"] = licence
            licence_url = _safe_https(raw.get("source_licence_url"))
            if licence_url is not None:
                source["sourceLicenceURL"] = licence_url
            manufacturer_sources.append(source)
    if manufacturer_sources:
        manufacturer_sources.sort(key=lambda item: (item["sourceID"], item.get("sourceName", "")))
        result["manufacturerSources"] = manufacturer_sources

    tags = off.strings(record.get("data_sources_tags"))
    if tags:
        bounded = sorted({tag for tag in tags if 0 < len(tag) <= MAX_SOURCE_TEXT_LENGTH})[:MAX_DATA_SOURCE_TAGS]
        if bounded:
            result["dataSourceTags"] = bounded

    return result or None


def project_source_record(record: dict[str, Any]) -> dict[str, Any]:
    """Apply the normal OFF projection, then append only sanitized producer metadata."""
    projected = off.project_source_record(record)
    provenance = sanitize_producer_provenance(record)
    if provenance is not None:
        projected[PROVENANCE_KEY] = provenance
    return projected


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManufacturerEvidenceError(f"failed to read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ManufacturerEvidenceError(f"{label} must be a JSON object")
    return value


def _snapshot(path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    metadata: dict[str, Any] | None = None
    try:
        with path.open("r", encoding="utf-8", errors="strict") as source:
            for line_number, raw in enumerate(source, start=1):
                if not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise ManufacturerEvidenceError(f"snapshot line {line_number} is invalid JSON") from exc
                if not isinstance(value, dict):
                    raise ManufacturerEvidenceError(f"snapshot line {line_number} must be an object")
                if METADATA_KEY in value:
                    if set(value) != {METADATA_KEY} or metadata is not None:
                        raise ManufacturerEvidenceError("snapshot metadata record is invalid or duplicated")
                    metadata = value[METADATA_KEY]
                    continue
                if metadata is not None:
                    raise ManufacturerEvidenceError("product records must precede acquisition metadata")
                source_id = str(value.get("_id") or value.get("id") or value.get("code") or "")
                if not source_id:
                    raise ManufacturerEvidenceError(f"snapshot line {line_number} lacks a source record ID")
                records[source_id] = value
    except (OSError, UnicodeDecodeError) as exc:
        raise ManufacturerEvidenceError(f"failed to read snapshot: {exc}") from exc
    if not isinstance(metadata, dict):
        raise ManufacturerEvidenceError("snapshot is missing acquisition metadata")
    if metadata.get("sourceKey") != SOURCE_KEY:
        raise ManufacturerEvidenceError("producer analysis supports only Open Food Facts snapshots")
    return records, metadata


def _ingredient_field_candidates(record: dict[str, Any], ingredient: dict[str, Any]) -> list[str]:
    text = ingredient.get("ingredientsText")
    language = ingredient.get("languageCode")
    if not isinstance(text, str) or not isinstance(language, str):
        return []
    base_language = language.split("-", 1)[0].casefold()
    matches = [
        field for field in sorted(INGREDIENT_FIELDS)
        if isinstance(record.get(field), str) and record[field] == text
    ]
    preferred = [f"ingredients_text_{base_language}", "ingredients_text"]
    return sorted(matches, key=lambda field: (preferred.index(field) if field in preferred else len(preferred), field))


def _manufacturer_contexts(provenance: dict[str, Any], field: str) -> list[dict[str, Any]]:
    values = provenance.get("manufacturerSources", [])
    if not isinstance(values, list):
        return []
    return [
        source for source in values
        if isinstance(source, dict)
        and isinstance(source.get("fields"), list)
        and field in source["fields"]
    ]


def _provenance_record(
    *, ingredient: dict[str, Any], field: str, producer_id: str,
    contexts: list[dict[str, Any]],
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "targetEvidenceID": ingredient["id"],
        "targetType": "ingredient",
        "gtin": ingredient["gtin"],
        "market": ingredient["market"],
        "sourceRecordID": ingredient["sourceRecordID"],
        "fieldName": field,
        "fieldValueSha256": sha256_text(ingredient["ingredientsText"]),
        "producerID": producer_id,
        "detectionBasis": "owner-field-exact",
        "manufacturerSources": contexts,
        "limitations": LIMITATION,
    }
    revision = ingredient.get("sourceRevision")
    if isinstance(revision, str) and revision.strip():
        value["sourceRevision"] = revision
    modified = ingredient.get("sourceModifiedAt")
    if isinstance(modified, str) and modified.strip():
        value["sourceModifiedAt"] = modified
    value["id"] = sidecar_id(value)
    return value


def _queue_item(
    *, gtin: str, market: str, source_record_id: str, priority: str,
    reason: str, detail: str, ingredient_id: str | None = None,
    provenance_id: str | None = None, producer_id: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "gtin": gtin,
        "market": market,
        "sourceRecordID": source_record_id,
        "priority": priority,
        "reason": reason,
        "detail": detail,
    }
    if ingredient_id:
        item["ingredientObservationID"] = ingredient_id
    if provenance_id:
        item["producerProvenanceID"] = provenance_id
    if producer_id:
        item["producerID"] = producer_id
    return item


def _dedupe_queue(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique = {canonical_json(item): item for item in items}
    priority = {"high": 0, "medium": 1, "low": 2}
    return sorted(
        unique.values(),
        key=lambda item: (
            priority[item["priority"]], item["gtin"], item["market"], item["reason"], item["detail"]
        ),
    )


def analyze(
    *, snapshot_path: Path, evidence_path: Path, change_report_path: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    records, metadata = _snapshot(snapshot_path)
    evidence = _load_json(evidence_path, "evidence envelope")
    changes = _load_json(change_report_path, "change report")
    if changes.get("sourceKey") != SOURCE_KEY or changes.get("snapshotID") != metadata.get("snapshotID"):
        raise ManufacturerEvidenceError("change report lineage does not match the OFF snapshot")

    ingredients = {
        item["id"]: item for item in evidence.get("ingredients", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    selections = [item for item in evidence.get("currentSelections", []) if isinstance(item, dict)]
    provenance_records: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = []
    producer_ids: set[str] = set()
    candidate_products: set[tuple[str, str]] = set()
    source_modified_confirmed = 0

    for selection in sorted(selections, key=lambda item: (str(item.get("gtin", "")), str(item.get("market", "")))):
        gtin, market = selection.get("gtin"), selection.get("market")
        if not isinstance(gtin, str) or not isinstance(market, str):
            continue
        ingredient_id = selection.get("ingredientObservationID")
        ingredient = ingredients.get(ingredient_id) if isinstance(ingredient_id, str) else None
        source_record_id = ""
        if ingredient is not None:
            source_record_id = str(ingredient.get("sourceRecordID") or "")
        if not source_record_id:
            identity_id = selection.get("identityObservationID")
            identity = next((item for item in evidence.get("identities", []) if isinstance(item, dict) and item.get("id") == identity_id), None)
            source_record_id = str(identity.get("sourceRecordID") or "") if isinstance(identity, dict) else ""
        record = records.get(source_record_id)
        if record is None:
            continue

        if ingredient is None:
            queue.append(_queue_item(
                gtin=gtin, market=market, source_record_id=source_record_id,
                priority="high", reason="ingredients-missing",
                detail="Selected product has no current ingredient observation; manufacturer formulation evidence is a priority target.",
            ))
        else:
            producer = record.get(PROVENANCE_KEY)
            fields = _ingredient_field_candidates(record, ingredient)
            confirmed: dict[str, Any] | None = None
            if isinstance(producer, dict) and fields:
                owner = producer.get("owner")
                owner_fields = producer.get("ownerFields")
                exact_owner_fields = [
                    field for field in fields
                    if isinstance(owner_fields, dict)
                    and owner_fields.get(field) == ingredient.get("ingredientsText")
                ]
                if isinstance(owner, str) and owner.strip() and exact_owner_fields:
                    field = exact_owner_fields[0]
                    contexts = _manufacturer_contexts(producer, field)
                    confirmed = _provenance_record(
                        ingredient=ingredient,
                        field=field,
                        producer_id=owner,
                        contexts=contexts,
                    )
                    provenance_records.append(confirmed)
                    producer_ids.add(owner)
                    if "sourceModifiedAt" in confirmed:
                        source_modified_confirmed += 1
                    queue.append(_queue_item(
                        gtin=gtin, market=market, source_record_id=source_record_id,
                        ingredient_id=ingredient["id"], provenance_id=confirmed["id"], producer_id=owner,
                        priority="medium", reason="producer-formulation-confirmed",
                        detail="Exact OFF owner-field value matches the current ingredient observation; prioritize this producer-origin formulation for methodology review where useful.",
                    ))
                else:
                    contexts = [context for field in fields for context in _manufacturer_contexts(producer, field)]
                    owner_conflict = isinstance(owner_fields, dict) and any(
                        field in owner_fields and owner_fields.get(field) != ingredient.get("ingredientsText")
                        for field in fields
                    )
                    if owner_conflict or len({context.get("sourceID") for context in contexts}) > 1:
                        candidate_products.add((gtin, market))
                        queue.append(_queue_item(
                            gtin=gtin, market=market, source_record_id=source_record_id,
                            ingredient_id=ingredient["id"], priority="high",
                            reason="producer-provenance-ambiguous",
                            detail="Producer metadata exists but does not bind one unambiguous exact producer value to the current ingredient observation.",
                            producer_id=owner if isinstance(owner, str) and owner.strip() else None,
                        ))
                    elif contexts or producer.get("dataSourceTags") or owner:
                        candidate_products.add((gtin, market))
                        queue.append(_queue_item(
                            gtin=gtin, market=market, source_record_id=source_record_id,
                            ingredient_id=ingredient["id"], priority="medium",
                            reason="producer-provenance-candidate",
                            detail="Producer/import metadata references this product or field but lacks an exact owner-field value match; human/source review is required before treating it as producer-provided formulation evidence.",
                            producer_id=owner if isinstance(owner, str) and owner.strip() else None,
                        ))

        for flag in sorted({item for item in selection.get("conflictFlags", []) if isinstance(item, str)}):
            if flag == "ingredients-missing":
                continue
            queue.append(_queue_item(
                gtin=gtin, market=market, source_record_id=source_record_id,
                ingredient_id=ingredient["id"] if ingredient else None,
                priority="high", reason="evidence-conflict",
                detail=f"Current selection conflict requires evidence review: {flag}.",
            ))

    selection_by_gtin = {
        (item.get("gtin"), item.get("market")): item for item in selections
        if isinstance(item.get("gtin"), str) and isinstance(item.get("market"), str)
    }
    for raw in changes.get("reviewQueue", []):
        if not isinstance(raw, dict):
            continue
        gtin = raw.get("gtin")
        selection = next((item for (candidate, _market), item in selection_by_gtin.items() if candidate == gtin), None)
        if not isinstance(gtin, str) or selection is None:
            continue
        market = selection["market"]
        source_record_id = str(raw.get("sourceRecordID") or "")
        reason = raw.get("reason")
        if reason == "formulation-changed":
            queue.append(_queue_item(
                gtin=gtin, market=market, source_record_id=source_record_id,
                ingredient_id=raw.get("ingredientObservationID") if isinstance(raw.get("ingredientObservationID"), str) else None,
                priority="high", reason="formulation-changed",
                detail="The formulation hash changed and the prior assessment must remain invalidated until the new exact formulation is reviewed.",
            ))
        elif reason == "ingredient-field-deleted":
            queue.append(_queue_item(
                gtin=gtin, market=market, source_record_id=source_record_id,
                priority="high", reason="ingredient-field-deleted",
                detail="The current complete source record no longer contains the previously selected ingredient field.",
            ))

    queue = _dedupe_queue(queue)
    provenance_records.sort(key=lambda item: (item["gtin"], item["market"], item["targetEvidenceID"]))
    generated_at = metadata.get("retrievedAt")
    if not isinstance(generated_at, str) or not generated_at.endswith("Z"):
        raise ManufacturerEvidenceError("snapshot retrievedAt is missing or invalid")

    provenance = {
        "schemaVersion": 1,
        "sourceKey": SOURCE_KEY,
        "snapshotID": metadata["snapshotID"],
        "generatedAt": generated_at,
        "metrics": {
            "selectedProducts": len(selections),
            "ingredientObservations": sum(1 for item in selections if isinstance(item.get("ingredientObservationID"), str)),
            "confirmedProducerFormulations": len(provenance_records),
            "producerProvenanceCandidates": len(candidate_products),
            "uniqueProducerIDs": len(producer_ids),
            "confirmedWithSourceModifiedAt": source_modified_confirmed,
            "freshnessEvidenceGain": 0,
        },
        "records": provenance_records,
        "limitations": [
            LIMITATION,
            "sourceModifiedAt/importedAt supplement provenance only and do not create an ingredient observedAt date.",
            "Coverage is a partial Open Food Facts producer-origin subset, not a denominator for all manufacturers or products.",
        ],
    }
    reason_counts = Counter(item["reason"] for item in queue)
    target = {
        "schemaVersion": 1,
        "sourceKey": SOURCE_KEY,
        "snapshotID": metadata["snapshotID"],
        "generatedAt": generated_at,
        "metrics": {
            "items": len(queue),
            **{f"reason:{key}": value for key, value in sorted(reason_counts.items())},
        },
        "items": queue,
        "limitations": [
            "This queue prioritizes review work; it does not publish a manufacturer coverage or freshness claim.",
            LIMITATION,
        ],
    }
    validate_provenance_report(provenance)
    validate_target_queue(target)
    return provenance, target


def _timestamp(value: Any, label: str) -> None:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ManufacturerEvidenceError(f"{label} must be RFC3339 UTC")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00").astimezone(timezone.utc)
    except ValueError as exc:
        raise ManufacturerEvidenceError(f"{label} must be RFC3339 UTC") from exc


def validate_provenance_report(report: dict[str, Any]) -> None:
    expected = {"schemaVersion", "sourceKey", "snapshotID", "generatedAt", "metrics", "records", "limitations"}
    if set(report) != expected or report.get("schemaVersion") != 1 or report.get("sourceKey") != SOURCE_KEY:
        raise ManufacturerEvidenceError("producer provenance report schema/identity mismatch")
    _timestamp(report.get("generatedAt"), "generatedAt")
    if not isinstance(report.get("snapshotID"), str) or not report["snapshotID"].strip():
        raise ManufacturerEvidenceError("snapshotID must be non-blank")
    metrics = report.get("metrics")
    required_metrics = {
        "selectedProducts", "ingredientObservations", "confirmedProducerFormulations",
        "producerProvenanceCandidates", "uniqueProducerIDs", "confirmedWithSourceModifiedAt",
        "freshnessEvidenceGain",
    }
    if not isinstance(metrics, dict) or set(metrics) != required_metrics or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in metrics.values()
    ) or metrics["freshnessEvidenceGain"] != 0:
        raise ManufacturerEvidenceError("producer provenance metrics are invalid")
    records = report.get("records")
    if not isinstance(records, list):
        raise ManufacturerEvidenceError("producer provenance records must be an array")
    seen: set[str] = set()
    for index, record in enumerate(records):
        if not isinstance(record, dict):
            raise ManufacturerEvidenceError(f"records[{index}] must be an object")
        required = {
            "id", "targetEvidenceID", "targetType", "gtin", "market", "sourceRecordID",
            "fieldName", "fieldValueSha256", "producerID", "detectionBasis",
            "manufacturerSources", "limitations",
        }
        optional = {"sourceRevision", "sourceModifiedAt"}
        if not required.issubset(record) or set(record) - required - optional:
            raise ManufacturerEvidenceError(f"records[{index}] fields mismatch")
        if record["id"] in seen or not str(record["id"]).startswith("hfeu:producer-provenance:sha256:"):
            raise ManufacturerEvidenceError(f"records[{index}] ID invalid/duplicate")
        seen.add(record["id"])
        if record["targetType"] != "ingredient" or record["detectionBasis"] != "owner-field-exact":
            raise ManufacturerEvidenceError("producer provenance may only confirm exact owner-field ingredient values")
        if record["fieldName"] not in INGREDIENT_FIELDS:
            raise ManufacturerEvidenceError("producer provenance field is outside the reviewed ingredient allowlist")
        if len(record["fieldValueSha256"]) != 64:
            raise ManufacturerEvidenceError("producer provenance field hash must be SHA-256")
        if "sourceModifiedAt" in record:
            _timestamp(record["sourceModifiedAt"], "sourceModifiedAt")
    limitations = report.get("limitations")
    if not isinstance(limitations, list) or not limitations or any(not isinstance(item, str) or not item.strip() for item in limitations):
        raise ManufacturerEvidenceError("producer provenance limitations must be non-empty")


def validate_target_queue(report: dict[str, Any]) -> None:
    expected = {"schemaVersion", "sourceKey", "snapshotID", "generatedAt", "metrics", "items", "limitations"}
    if set(report) != expected or report.get("schemaVersion") != 1 or report.get("sourceKey") != SOURCE_KEY:
        raise ManufacturerEvidenceError("manufacturer target queue schema/identity mismatch")
    _timestamp(report.get("generatedAt"), "generatedAt")
    if not isinstance(report.get("metrics"), dict) or any(
        not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in report["metrics"].values()
    ):
        raise ManufacturerEvidenceError("manufacturer target metrics must be non-negative integers")
    allowed_reasons = {
        "producer-formulation-confirmed", "producer-provenance-candidate",
        "producer-provenance-ambiguous", "ingredients-missing", "formulation-changed",
        "ingredient-field-deleted", "evidence-conflict",
    }
    allowed_priority = {"high", "medium", "low"}
    items = report.get("items")
    if not isinstance(items, list):
        raise ManufacturerEvidenceError("manufacturer target items must be an array")
    for index, item in enumerate(items):
        if not isinstance(item, dict) or item.get("reason") not in allowed_reasons or item.get("priority") not in allowed_priority:
            raise ManufacturerEvidenceError(f"manufacturer target item {index} is invalid")
        if not isinstance(item.get("detail"), str) or not item["detail"].strip():
            raise ManufacturerEvidenceError(f"manufacturer target item {index} detail is blank")
    limitations = report.get("limitations")
    if not isinstance(limitations, list) or not limitations:
        raise ManufacturerEvidenceError("manufacturer target queue must declare limitations")


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    analyze_parser = sub.add_parser("analyze")
    analyze_parser.add_argument("--snapshot", type=Path, required=True)
    analyze_parser.add_argument("--evidence", type=Path, required=True)
    analyze_parser.add_argument("--change-report", type=Path, required=True)
    analyze_parser.add_argument("--provenance-output", type=Path, required=True)
    analyze_parser.add_argument("--target-output", type=Path, required=True)
    validate_parser = sub.add_parser("validate")
    validate_parser.add_argument("--provenance", type=Path, required=True)
    validate_parser.add_argument("--target", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.command == "validate":
            validate_provenance_report(_load_json(args.provenance, "producer provenance"))
            validate_target_queue(_load_json(args.target, "manufacturer target queue"))
            print("Validated manufacturer producer-provenance reports")
            return
        provenance, target = analyze(
            snapshot_path=args.snapshot,
            evidence_path=args.evidence,
            change_report_path=args.change_report,
        )
        _write_json(args.provenance_output, provenance)
        _write_json(args.target_output, target)
        print(
            f"Manufacturer provenance: {provenance['metrics']['confirmedProducerFormulations']} confirmed, "
            f"{provenance['metrics']['producerProvenanceCandidates']} candidates, "
            f"{len(target['items'])} target queue items"
        )
    except ManufacturerEvidenceError as exc:
        raise SystemExit(f"manufacturer evidence failed: {exc}") from exc


if __name__ == "__main__":
    main()
