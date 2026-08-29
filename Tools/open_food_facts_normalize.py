#!/usr/bin/env python3
"""Normalize an admitted Open Food Facts snapshot into selection + evidence artifacts.

The adapter is deliberately standard-library-only and local-file-only. Acquisition is
performed separately by ``open_food_facts_acquire.py``; this module consumes only the
bounded JSONL snapshot produced by that stage.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any, Iterable

from catalog_selection_engine import evaluate_bundle
from catalog_selection_contract import normalize_gtin
from evidence_model import derive_id, formulation_hash, validate_envelope
from open_food_facts_common import (
    AdapterError,
    DEFAULT_SELECTION_POLICY,
    DEFAULT_SOURCE_POLICY,
    SOURCE_KEY,
    SOURCE_OPERATOR,
    SourcePolicy,
    allergen_text,
    canonical_tags,
    ingredient_language_conflicts,
    ingredient_texts,
    load_json,
    load_source_policy,
    record_to_candidate,
    reserved_prefix_ambiguity,
    source_revision,
    timestamp_from_epoch,
    traces_text,
)

METADATA_KEY = "_hfeu_open_food_facts_metadata"
EVIDENCE_SCHEMA_VERSION = 1
SELECTION_SCHEMA_VERSION = 1
TAXONOMY_VERSION = "schema-1004:tags_sources"
EMPTY_COLLECTIONS = (
    "identities",
    "ingredients",
    "retailerEvidence",
    "remoteImages",
    "packageEvidence",
    "certifications",
    "reviews",
    "assessments",
    "validityEvents",
    "currentSelections",
    "releases",
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _read_snapshot(path: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    metadata: dict[str, Any] | None = None
    try:
        with path.open("r", encoding="utf-8", errors="strict") as source:
            for line_number, raw in enumerate(source, start=1):
                if not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                except json.JSONDecodeError as exc:
                    raise AdapterError(f"snapshot line {line_number} is malformed JSON: {exc}") from exc
                if not isinstance(value, dict):
                    raise AdapterError(f"snapshot line {line_number} must be a JSON object")
                if METADATA_KEY in value:
                    if set(value) != {METADATA_KEY} or metadata is not None:
                        raise AdapterError("snapshot must contain exactly one final acquisition metadata record")
                    raw_metadata = value[METADATA_KEY]
                    if not isinstance(raw_metadata, dict):
                        raise AdapterError("acquisition metadata must be an object")
                    metadata = raw_metadata
                    continue
                if metadata is not None:
                    raise AdapterError("product records must precede the final acquisition metadata record")
                records.append(value)
    except (OSError, UnicodeDecodeError) as exc:
        raise AdapterError(f"failed to read snapshot {path}: {exc}") from exc
    if metadata is None:
        raise AdapterError("snapshot is missing acquisition metadata")
    return records, metadata


def _require_metadata(metadata: dict[str, Any], policy: SourcePolicy) -> None:
    required = {
        "sourceKey", "snapshotID", "mode", "exportURL", "retrievedAt", "transportSha256",
        "transportBytes", "downloadComplete", "sourceSchemaVersions",
        "expectedProductSchemaVersion", "apiVersion", "tagSchema", "recordsExamined",
        "recordsEmitted", "malformedRecords", "oversizedLines", "malformedRate",
        "noCompletenessClaim", "imageBinaryDownloads", "licenseIdentifiers",
    }
    missing = sorted(required - set(metadata))
    if missing:
        raise AdapterError(f"acquisition metadata missing fields {missing}")
    if metadata["sourceKey"] != SOURCE_KEY:
        raise AdapterError("snapshot sourceKey does not identify Open Food Facts")
    if metadata["expectedProductSchemaVersion"] != policy.product_schema_version:
        raise AdapterError("snapshot product schema contract does not match reviewed source policy")
    if metadata["apiVersion"] != policy.api_version or metadata["tagSchema"] != policy.raw["tagSchema"]:
        raise AdapterError("snapshot API/tag contract does not match reviewed source policy")
    if metadata["noCompletenessClaim"] is not True or metadata["imageBinaryDownloads"] is not False:
        raise AdapterError("snapshot must prohibit completeness claims and image-binary acquisition")
    if not isinstance(metadata["snapshotID"], str) or not metadata["snapshotID"].strip():
        raise AdapterError("snapshotID must be a non-blank string")
    if not isinstance(metadata["retrievedAt"], str) or not metadata["retrievedAt"].endswith("Z"):
        raise AdapterError("retrievedAt must be an RFC3339 UTC timestamp")
    if not isinstance(metadata["downloadComplete"], bool):
        raise AdapterError("downloadComplete must be boolean")


def _empty_envelope() -> dict[str, Any]:
    envelope: dict[str, Any] = {"schemaVersion": EVIDENCE_SCHEMA_VERSION, "sources": []}
    for collection in EMPTY_COLLECTIONS:
        envelope[collection] = []
    return envelope


def _by_id(records: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {record["id"]: copy.deepcopy(record) for record in records}


def _selection_by_key(envelope: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(item["gtin"], item["market"]): item for item in envelope["currentSelections"]}


def _previous_ingredient(
    previous: dict[str, Any] | None,
    gtin: str,
    market: str,
) -> dict[str, Any] | None:
    if previous is None:
        return None
    ingredients = _by_id(previous["ingredients"])
    selection = _selection_by_key(previous).get((gtin, market))
    if not selection:
        return None
    ingredient_id = selection.get("ingredientObservationID")
    if not isinstance(ingredient_id, str):
        return None
    return ingredients.get(ingredient_id)


def _source_reference(metadata: dict[str, Any], policy: SourcePolicy) -> dict[str, Any]:
    return {
        "sourceKey": SOURCE_KEY,
        "operator": SOURCE_OPERATOR,
        "sourceClass": "open-database",
        "reference": str(metadata["exportURL"]),
        "accessMethod": "public-bulk",
        "markets": ["DE"],
        "retrievedAt": metadata["retrievedAt"],
        "sourceSnapshotID": metadata["snapshotID"],
    }


def _identity_record(
    record: dict[str, Any],
    selected: dict[str, Any],
    retrieved_at: str,
) -> dict[str, Any]:
    source_modified = timestamp_from_epoch(record.get("last_modified_t"))
    value: dict[str, Any] = {
        "gtin": selected["gtin"],
        "originalBarcode": selected["barcode"],
        "market": selected["market"],
        "sourceKey": SOURCE_KEY,
        "sourceRecordID": selected["sourceRecordID"],
        "name": selected["name"],
        "retrievedAt": retrieved_at,
        "confidence": "medium",
    }
    revision = source_revision(record)
    if revision is not None:
        value["sourceRevision"] = revision
    if source_modified is not None:
        value["sourceModifiedAt"] = source_modified
    brand = selected.get("brand")
    if isinstance(brand, str) and brand.strip():
        value["brand"] = brand
    quantity = record.get("quantity")
    if isinstance(quantity, str) and quantity.strip():
        value["quantity"] = quantity
    categories = sorted(set(selected["categoryTags"]))
    if categories:
        value["categories"] = categories
    packaging = sorted(set(canonical_tags(record, "packaging")))
    if packaging:
        value["packaging"] = packaging
    value["id"] = derive_id("identities", value)
    return value


def _ingredient_record(
    record: dict[str, Any],
    selected: dict[str, Any],
    retrieved_at: str,
    previous: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, bool]:
    texts = ingredient_texts(record)
    if not texts:
        return None, False
    language, text = texts[0]
    value: dict[str, Any] = {
        "gtin": selected["gtin"],
        "market": selected["market"],
        "sourceKey": SOURCE_KEY,
        "sourceRecordID": selected["sourceRecordID"],
        "ingredientsText": text,
        "languageCode": language,
        "retrievedAt": retrieved_at,
        "captureMethod": "source-text",
        "verificationState": "unverified",
    }
    revision = source_revision(record)
    source_modified = timestamp_from_epoch(record.get("last_modified_t"))
    if revision is not None:
        value["sourceRevision"] = revision
    if source_modified is not None:
        value["sourceModifiedAt"] = source_modified
    allergens = allergen_text(record)
    traces = traces_text(record)
    if allergens is not None:
        value["allergensText"] = allergens
    if traces is not None:
        value["tracesText"] = traces
    value["contentHash"] = formulation_hash(value)

    prior = _previous_ingredient(previous, selected["gtin"], selected["market"])
    if prior is not None and prior["contentHash"] == value["contentHash"]:
        return copy.deepcopy(prior), False
    changed = prior is not None and prior["contentHash"] != value["contentHash"]
    if changed:
        value["supersedesID"] = prior["id"]
    value["id"] = derive_id("ingredients", value)
    return value, changed


def _retailer_records(
    record: dict[str, Any],
    selected: dict[str, Any],
    retrieved_at: str,
) -> list[dict[str, Any]]:
    revision = source_revision(record)
    result: list[dict[str, Any]] = []
    for retailer_key in sorted(set(selected["retailerKeys"])):
        value: dict[str, Any] = {
            "kind": "community-store-report",
            "retailerKey": retailer_key,
            "gtin": selected["gtin"],
            "market": selected["market"],
            "sourceKey": SOURCE_KEY,
            "sourceRecordID": selected["sourceRecordID"],
            "retrievedAt": retrieved_at,
            "confidence": "low",
            "limitations": "Community/open-database store tag; not proof of official, nationwide, or current availability.",
        }
        if revision is not None:
            value["sourceRevision"] = revision
        value["id"] = derive_id("retailerEvidence", value)
        result.append(value)
    return result


def _image_records(
    record: dict[str, Any],
    selected: dict[str, Any],
    retrieved_at: str,
) -> list[dict[str, Any]]:
    source_modified = timestamp_from_epoch(record.get("last_modified_t"))
    result: list[dict[str, Any]] = []
    for image in selected["remoteImages"]:
        value: dict[str, Any] = {
            "gtin": selected["gtin"],
            "market": selected["market"],
            "purpose": image["purpose"],
            "url": image["url"],
            "sourceKey": SOURCE_KEY,
            "imageID": image["imageID"],
            "retrievedAt": retrieved_at,
        }
        if isinstance(image.get("revision"), str) and image["revision"].strip():
            value["revision"] = image["revision"]
        if source_modified is not None:
            value["sourceModifiedAt"] = source_modified
        for field in ("width", "height"):
            if isinstance(image.get(field), int) and image[field] > 0:
                value[field] = image[field]
        value["id"] = derive_id("remoteImages", value)
        result.append(value)
    return result


def _validity_event(
    assessment_id: str,
    ingredient_id: str,
    occurred_at: str,
) -> dict[str, Any]:
    value = {
        "assessmentID": assessment_id,
        "kind": "invalidated",
        "occurredAt": occurred_at,
        "reason": "Open Food Facts formulation text changed; assessment requires re-review.",
        "triggeredByEvidenceID": ingredient_id,
    }
    value["id"] = derive_id("validityEvents", value)
    return value


def _normalize_previous(previous: dict[str, Any] | None) -> dict[str, Any]:
    if previous is None:
        return _empty_envelope()
    validate_envelope(previous)
    result = copy.deepcopy(previous)
    result["sources"] = [source for source in result["sources"] if source["sourceKey"] != SOURCE_KEY]
    return result


def normalize_snapshot(
    *,
    snapshot: Path,
    policy: SourcePolicy,
    selection_policy: dict[str, Any],
    previous_evidence: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    records, metadata = _read_snapshot(snapshot)
    _require_metadata(metadata, policy)

    candidate_bundle = {
        "schemaVersion": SELECTION_SCHEMA_VERSION,
        "sourceSnapshot": {
            "sourceKey": SOURCE_KEY,
            "snapshotID": metadata["snapshotID"],
            "sourceSchemaVersion": policy.product_schema_version,
            "taxonomyVersion": TAXONOMY_VERSION,
            "retrievedAt": metadata["retrievedAt"],
        },
        "candidates": [record_to_candidate(record, policy) for record in records],
    }
    selection = evaluate_bundle(selection_policy, candidate_bundle)
    by_source_id = {
        str(record.get("_id") or record.get("id") or record.get("code") or "missing-code"): record
        for record in records
    }

    envelope = _normalize_previous(previous_evidence)
    envelope["sources"].append(_source_reference(metadata, policy))
    prior_selections = _selection_by_key(envelope)
    current_keys: set[tuple[str, str]] = set()
    changed_formulations: list[dict[str, str]] = []
    additions = 0
    unchanged = 0
    language_conflicts: list[dict[str, Any]] = []
    reserved_prefix: list[str] = []

    identity_map = _by_id(envelope["identities"])
    ingredient_map = _by_id(envelope["ingredients"])
    retailer_map = _by_id(envelope["retailerEvidence"])
    image_map = _by_id(envelope["remoteImages"])
    validity_map = _by_id(envelope["validityEvents"])

    new_selections: dict[tuple[str, str], dict[str, Any]] = {
        key: copy.deepcopy(value) for key, value in prior_selections.items()
    }

    for selected in selection["selected"]:
        source_id = selected["sourceRecordID"]
        record = by_source_id.get(source_id)
        if record is None:
            raise AdapterError(f"selected source record {source_id!r} is missing from snapshot")
        gtin = selected["gtin"]
        if normalize_gtin(selected["barcode"]) != gtin:
            raise AdapterError(f"selection returned inconsistent GTIN for {source_id}")
        key = (gtin, selected["market"])
        current_keys.add(key)

        identity = _identity_record(record, selected, metadata["retrievedAt"])
        identity_map[identity["id"]] = identity

        ingredient, changed = _ingredient_record(
            record, selected, metadata["retrievedAt"], previous_evidence
        )
        if ingredient is not None:
            ingredient_map[ingredient["id"]] = ingredient
        if changed and ingredient is not None:
            changed_formulations.append(
                {"gtin": gtin, "sourceRecordID": source_id, "ingredientObservationID": ingredient["id"]}
            )
        elif key in prior_selections:
            unchanged += 1
        else:
            additions += 1

        retailers = _retailer_records(record, selected, metadata["retrievedAt"])
        for retailer in retailers:
            retailer_map[retailer["id"]] = retailer
        images = _image_records(record, selected, metadata["retrievedAt"])
        for image in images:
            image_map[image["id"]] = image

        flags: set[str] = set()
        conflicts = ingredient_language_conflicts(record)
        if conflicts:
            flags.add("ingredient-language-conflict")
            language_conflicts.append({"sourceRecordID": source_id, "conflicts": conflicts})
        if reserved_prefix_ambiguity(record):
            flags.add("restricted-prefix-provenance-ambiguous")
            reserved_prefix.append(source_id)
        if ingredient is None:
            flags.add("ingredients-missing")

        prior = prior_selections.get(key)
        if changed and prior is not None and isinstance(prior.get("assessmentID"), str) and ingredient is not None:
            event = _validity_event(prior["assessmentID"], ingredient["id"], metadata["retrievedAt"])
            validity_map[event["id"]] = event

        current: dict[str, Any] = {
            "gtin": gtin,
            "market": selected["market"],
            "identityObservationID": identity["id"],
            "certificationIDs": [],
            "retailerEvidenceIDs": sorted(item["id"] for item in retailers),
            "remoteImageIDs": sorted(item["id"] for item in images),
            "conflictFlags": sorted(flags),
        }
        if ingredient is not None:
            current["ingredientObservationID"] = ingredient["id"]
        # The source adapter must never invent or carry a positive assessment across
        # a changed formulation. Unchanged assessments are intentionally left for the
        # source-precedence/review stage rather than implicitly selected here.
        current["id"] = derive_id("currentSelections", current)
        new_selections[key] = current

    removals: list[dict[str, str]] = []
    if metadata["downloadComplete"] is True and metadata["mode"] in {"fixture", "full"}:
        for key, prior in prior_selections.items():
            if key not in current_keys:
                removals.append({"gtin": key[0], "market": key[1], "selectionID": prior["id"]})
                new_selections.pop(key, None)

    envelope["identities"] = sorted(identity_map.values(), key=lambda item: item["id"])
    envelope["ingredients"] = sorted(ingredient_map.values(), key=lambda item: item["id"])
    envelope["retailerEvidence"] = sorted(retailer_map.values(), key=lambda item: item["id"])
    envelope["remoteImages"] = sorted(image_map.values(), key=lambda item: item["id"])
    envelope["validityEvents"] = sorted(validity_map.values(), key=lambda item: item["id"])
    envelope["currentSelections"] = sorted(new_selections.values(), key=lambda item: (item["gtin"], item["market"]))
    envelope["sources"] = sorted(envelope["sources"], key=lambda item: item["sourceKey"])
    validate_envelope(envelope)

    quality = {
        "schemaVersion": 1,
        "sourceKey": SOURCE_KEY,
        "snapshotID": metadata["snapshotID"],
        "downloadComplete": metadata["downloadComplete"],
        "noCompletenessClaim": True,
        "imageBinaryDownloads": False,
        "selectionPolicyVersion": selection["policyVersion"],
        "acquisition": {
            "mode": metadata["mode"],
            "recordsExamined": metadata["recordsExamined"],
            "recordsEmitted": metadata["recordsEmitted"],
            "malformedRecords": metadata["malformedRecords"],
            "oversizedLines": metadata["oversizedLines"],
            "transportSha256": metadata["transportSha256"],
            "transportBytes": metadata["transportBytes"],
            "sourceSchemaVersions": metadata["sourceSchemaVersions"],
            "apiVersion": metadata["apiVersion"],
            "tagSchema": metadata["tagSchema"],
        },
        "selection": selection["report"],
        "evidence": {
            "currentSelections": len(envelope["currentSelections"]),
            "ingredientObservations": len(envelope["ingredients"]),
            "retailerEvidence": len(envelope["retailerEvidence"]),
            "remoteImageReferences": len(envelope["remoteImages"]),
            "positiveAssessmentsCreated": 0,
        },
        "warnings": {
            "ingredientLanguageConflicts": language_conflicts,
            "restrictedPrefixProvenanceAmbiguities": sorted(reserved_prefix),
        },
    }

    review_queue = [
        {
            "gtin": item["gtin"],
            "sourceRecordID": item["sourceRecordID"],
            "ingredientObservationID": item["ingredientObservationID"],
            "reason": "formulation-changed",
        }
        for item in sorted(changed_formulations, key=lambda item: (item["gtin"], item["sourceRecordID"]))
    ]
    change_report = {
        "schemaVersion": 1,
        "sourceKey": SOURCE_KEY,
        "snapshotID": metadata["snapshotID"],
        "baseline": "provided-evidence" if previous_evidence is not None else "none",
        "completeSnapshot": metadata["downloadComplete"] is True,
        "deletionComparisonAllowed": metadata["downloadComplete"] is True and metadata["mode"] in {"fixture", "full"},
        "additions": additions,
        "unchanged": unchanged,
        "formulationChanges": len(changed_formulations),
        "removals": len(removals),
        "removedSelections": removals,
        "reviewQueue": review_queue,
        "selectionPolicyVersion": selection["policyVersion"],
        "selectionDecisionReasons": selection["report"]["decisionReasons"],
        "noCompletenessClaim": True,
    }
    return envelope, {"selection": selection, "quality": quality}, change_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--evidence-output", type=Path, required=True)
    parser.add_argument("--selection-output", type=Path, required=True)
    parser.add_argument("--quality-output", type=Path, required=True)
    parser.add_argument("--change-output", type=Path, required=True)
    parser.add_argument("--source-policy", type=Path, default=DEFAULT_SOURCE_POLICY)
    parser.add_argument("--selection-policy", type=Path, default=DEFAULT_SELECTION_POLICY)
    parser.add_argument("--previous-evidence", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    policy = load_source_policy(args.source_policy)
    selection_policy = load_json(args.selection_policy)
    previous = load_json(args.previous_evidence) if args.previous_evidence else None
    envelope, reports, changes = normalize_snapshot(
        snapshot=args.snapshot,
        policy=policy,
        selection_policy=selection_policy,
        previous_evidence=previous,
    )
    _write_json(args.evidence_output, envelope)
    _write_json(args.selection_output, reports["selection"])
    _write_json(args.quality_output, reports["quality"])
    _write_json(args.change_output, changes)
    print(
        f"Normalized {reports['quality']['acquisition']['recordsEmitted']} Open Food Facts records; "
        f"selected {reports['selection']['report']['includedProducts']} detailed products"
    )


if __name__ == "__main__":
    main()
