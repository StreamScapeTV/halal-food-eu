"""Normalize bounded Open Prices snapshots into immutable retailer evidence."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterator

import open_prices_common as common

MAX_MULTIPLEX_RECORDS = 25_000_000
MAX_ALIAS_REPORT_VALUES = 200


def _snapshot_digest(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    try:
        stream = path.open("rb")
    except OSError as exc:
        raise common.AdapterError(f"cannot read source snapshot: {exc}") from exc
    with stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _iter_multiplex(path: Path) -> Iterator[tuple[str | None, dict[str, Any] | None]]:
    seen = 0
    try:
        stream = path.open("r", encoding="utf-8")
    except OSError as exc:
        raise common.AdapterError(f"cannot read source snapshot: {exc}") from exc
    with stream:
        for line in stream:
            if not line.strip():
                continue
            seen += 1
            if seen > MAX_MULTIPLEX_RECORDS:
                raise common.AdapterError("source snapshot exceeds normalized record bound")
            try:
                envelope = json.loads(line)
            except json.JSONDecodeError:
                yield None, None
                continue
            if not isinstance(envelope, dict) or set(envelope) != {"kind", "record"} or not isinstance(envelope["record"], dict):
                yield None, None
                continue
            kind = envelope["kind"]
            if not isinstance(kind, str):
                yield None, None
                continue
            yield kind, envelope["record"]


def _load_joins(
    path: Path,
) -> tuple[
    dict[int, dict[str, Any]],
    dict[int, dict[str, Any]],
    frozenset[int],
    frozenset[int],
    Counter[str],
]:
    locations: dict[int, dict[str, Any]] = {}
    proofs: dict[int, dict[str, Any]] = {}
    conflicted_locations: set[int] = set()
    conflicted_proofs: set[int] = set()
    counters: Counter[str] = Counter()
    for kind, record in _iter_multiplex(path):
        if kind is None or record is None:
            counters["malformedSnapshotLines"] += 1
            continue
        if kind == "location":
            try:
                record_id = common.require_int(record.get("id"), "location.id")
            except common.AdapterError:
                counters["invalidLocationIDs"] += 1
                continue
            if record_id in conflicted_locations:
                counters["conflictingLocationIDRepeats"] += 1
                continue
            previous = locations.get(record_id)
            if previous is None:
                locations[record_id] = record
            elif previous == record:
                counters["duplicateLocationIDs"] += 1
            else:
                counters["conflictingLocationIDs"] += 1
                locations.pop(record_id, None)
                conflicted_locations.add(record_id)
        elif kind == "proof":
            try:
                record_id = common.require_int(record.get("id"), "proof.id")
            except common.AdapterError:
                counters["invalidProofIDs"] += 1
                continue
            if record_id in conflicted_proofs:
                counters["conflictingProofIDRepeats"] += 1
                continue
            previous = proofs.get(record_id)
            if previous is None:
                proofs[record_id] = record
            elif previous == record:
                counters["duplicateProofIDs"] += 1
            else:
                counters["conflictingProofIDs"] += 1
                proofs.pop(record_id, None)
                conflicted_proofs.add(record_id)
        elif kind not in {"price"}:
            counters["unknownKinds"] += 1
    return locations, proofs, frozenset(conflicted_locations), frozenset(conflicted_proofs), counters


def _iter_prices(path: Path) -> Iterator[dict[str, Any]]:
    for kind, record in _iter_multiplex(path):
        if kind == "price" and record is not None:
            yield record


def _source(metadata: dict[str, Any], aliases: common.RetailerAliases, policy: common.SourcePolicy) -> dict[str, Any]:
    return {
        "accessMethod": "public-bulk",
        "markets": [common.MARKET],
        "operator": common.SOURCE_OPERATOR,
        "reference": policy.raw["documentation"]["data"],
        "retrievedAt": metadata["retrievedAt"],
        "sourceClass": "open-database",
        "sourceKey": common.SOURCE_KEY,
        "sourceRevision": f"open-prices-export+retailer-aliases-{aliases.version}",
        "sourceSnapshotID": metadata["snapshotID"],
    }


def _price_context(price: dict[str, Any]) -> str:
    value = price.get("price")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise common.AdapterError("price.price must be a finite non-negative number")
    currency = common.safe_text(price.get("currency"), max_len=8)
    if not currency or not currency.isascii() or not currency.isalpha() or len(currency) != 3:
        raise common.AdapterError("price.currency must be a three-letter currency code")
    price_per = common.safe_text(price.get("price_per"), max_len=32)
    quantity = price.get("receipt_quantity")
    parts = [f"source price {value:g} {currency.upper()}"]
    if price_per:
        parts.append(f"per {price_per}")
    if quantity is not None:
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)) or not math.isfinite(quantity) or quantity <= 0:
            raise common.AdapterError("price.receipt_quantity must be a finite positive number when present")
        parts.append(f"receipt quantity {quantity:g}")
    return " ".join(parts)


def _location_scope(location: dict[str, Any], proof: dict[str, Any], price: dict[str, Any]) -> str:
    city = common.safe_text(location.get("osm_address_city"), max_len=80)
    postcode = common.safe_text(location.get("osm_address_postcode"), max_len=24)
    country = common.safe_text(location.get("osm_address_country_code"), max_len=8)
    proof_type = common.safe_text(proof.get("type"), max_len=32) or "UNKNOWN"
    parts = [item for item in (city, postcode, country) if item]
    place = " ".join(parts) if parts else "Germany"
    return f"single dated Open Prices observation at {place}; proof type {proof_type}; {_price_context(price)}"


def _retailer_record(
    price: dict[str, Any],
    location: dict[str, Any],
    proof: dict[str, Any],
    retailer_key: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    price_id = common.require_int(price.get("id"), "price.id")
    proof_id = common.require_int(proof.get("id"), "proof.id")
    location_id = common.require_int(location.get("id"), "location.id")
    gtin = common.canonical_gtin(price.get("product_code"))
    observed_at = common.observation_timestamp(price.get("date"), metadata["retrievedAt"])
    record: dict[str, Any] = {
        "confidence": "dated-source-observation",
        "gtin": gtin,
        "kind": "retailer-observation",
        "limitations": "Dated Open Prices observation only; not current stock, nationwide availability, normal assortment, or retailer completeness.",
        "locationID": f"open-prices:location:{location_id}",
        "market": common.MARKET,
        "observedAt": observed_at,
        "retailerKey": retailer_key,
        "retrievedAt": metadata["retrievedAt"],
        "scope": _location_scope(location, proof, price),
        "sourceKey": common.SOURCE_KEY,
        "sourceRecordID": f"price:{price_id};proof:{proof_id}",
        "sourceRevision": common.source_revision(price, proof),
    }
    record["id"] = common.derive_evidence_id("retailer", record)
    return record


def _load_previous(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    value = common.load_json(path)
    if not isinstance(value, dict) or value.get("schemaVersion") != 1 or not isinstance(value.get("retailerEvidence"), list):
        raise common.AdapterError("previous evidence is not a v1 evidence envelope")
    return value


def _bounded_alias_map(values: Counter[str]) -> dict[str, int]:
    ranked = sorted(values.items(), key=lambda item: (-item[1], item[0]))[:MAX_ALIAS_REPORT_VALUES]
    return {key: count for key, count in ranked}


def _validate_metadata(metadata: dict[str, Any], snapshot: Path) -> None:
    required_metadata = {
        "schemaVersion", "sourceKey", "snapshotID", "mode", "retrievedAt", "downloadComplete",
        "recordsEmitted", "recordCounts", "malformedRecords", "payloadSha256", "payloadBytes",
        "upstreamExports", "proofImageBinariesIncluded", "personalContributorFieldsIncluded", "noCompletenessClaim",
    }
    if set(metadata) != required_metadata:
        raise common.AdapterError("acquisition metadata shape is unsupported")
    if metadata["schemaVersion"] != 1 or metadata["sourceKey"] != common.SOURCE_KEY:
        raise common.AdapterError("acquisition metadata source/version mismatch")
    if metadata["mode"] not in {"fixture", "sample", "full"}:
        raise common.AdapterError("acquisition metadata has unsupported mode")
    expected_complete = metadata["mode"] != "sample"
    if metadata["downloadComplete"] is not expected_complete:
        raise common.AdapterError("acquisition completeness contradicts acquisition mode")
    common.parse_retrieved_at(metadata["retrievedAt"])
    if metadata["proofImageBinariesIncluded"] is not False or metadata["personalContributorFieldsIncluded"] is not False or metadata["noCompletenessClaim"] is not True:
        raise common.AdapterError("acquisition metadata violates privacy/completeness boundary")

    if not isinstance(metadata["payloadSha256"], str) or len(metadata["payloadSha256"]) != 64 or any(character not in "0123456789abcdef" for character in metadata["payloadSha256"]):
        raise common.AdapterError("acquisition payload digest is invalid")
    payload_bytes = common.require_int(metadata["payloadBytes"], "metadata.payloadBytes")
    emitted = common.require_int(metadata["recordsEmitted"], "metadata.recordsEmitted")

    counts = metadata["recordCounts"]
    malformed = metadata["malformedRecords"]
    if not isinstance(counts, dict) or set(counts) != set(common.EXPORT_KINDS):
        raise common.AdapterError("acquisition recordCounts are invalid")
    if not isinstance(malformed, dict) or set(malformed) != set(common.EXPORT_KINDS):
        raise common.AdapterError("acquisition malformedRecords are invalid")
    normalized_counts = {kind: common.require_int(counts[kind], f"recordCounts.{kind}") for kind in common.EXPORT_KINDS}
    for kind in common.EXPORT_KINDS:
        common.require_int(malformed[kind], f"malformedRecords.{kind}")
    if sum(normalized_counts.values()) != emitted:
        raise common.AdapterError("acquisition record count does not match per-export counts")

    upstream = metadata["upstreamExports"]
    if not isinstance(upstream, dict) or set(upstream) != set(common.EXPORT_KINDS):
        raise common.AdapterError("acquisition upstream export metadata is invalid")
    for kind in common.EXPORT_KINDS:
        entry = upstream[kind]
        if not isinstance(entry, dict):
            raise common.AdapterError("acquisition upstream export metadata is invalid")
        digest = entry.get("sha256")
        size = entry.get("compressedBytes")
        if not isinstance(digest, str) or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise common.AdapterError("acquisition upstream export digest is invalid")
        common.require_int(size, f"upstreamExports.{kind}.compressedBytes")

    actual_digest, actual_size = _snapshot_digest(snapshot)
    if actual_digest != metadata["payloadSha256"] or actual_size != payload_bytes:
        raise common.AdapterError("source snapshot digest/size does not match acquisition metadata")


def normalize_snapshot(
    *,
    snapshot: Path,
    metadata_path: Path,
    policy: common.SourcePolicy,
    aliases: common.RetailerAliases,
    previous_evidence_path: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    metadata = common.load_json(metadata_path)
    if not isinstance(metadata, dict):
        raise common.AdapterError("acquisition metadata must be an object")
    _validate_metadata(metadata, snapshot)

    locations, proofs, conflicted_locations, conflicted_proofs, counters = _load_joins(snapshot)
    observations_by_id: dict[str, dict[str, Any]] = {}
    observation_context: dict[str, tuple[str, str, str]] = {}
    unmatched_aliases: Counter[str] = Counter()
    ambiguous_aliases: Counter[str] = Counter()
    seen_price_ids: dict[int, tuple[str, str]] = {}
    conflicted_price_ids: set[int] = set()
    location_matches: dict[int, tuple[str | None, str, str | None]] = {}

    for price in _iter_prices(snapshot):
        try:
            price_id = common.require_int(price.get("id"), "price.id")
        except common.AdapterError:
            counters["invalidPriceIDs"] += 1
            continue
        if price_id in conflicted_price_ids:
            counters["conflictingPriceIDRepeats"] += 1
            continue
        if price.get("type") not in (None, "PRODUCT"):
            counters["nonProductPrices"] += 1
            continue
        try:
            common.canonical_gtin(price.get("product_code"))
        except common.AdapterError:
            counters["invalidGTIN"] += 1
            continue
        try:
            common.observation_timestamp(price.get("date"), metadata["retrievedAt"])
        except common.AdapterError:
            counters["invalidOrFutureDate"] += 1
            continue
        try:
            location_id = common.require_int(price.get("location_id"), "price.location_id")
            proof_id = common.require_int(price.get("proof_id"), "price.proof_id")
        except common.AdapterError:
            counters["invalidJoinIDs"] += 1
            continue
        if location_id in conflicted_locations:
            counters["conflictingLocationJoin"] += 1
            continue
        if proof_id in conflicted_proofs:
            counters["conflictingProofJoin"] += 1
            continue
        location = locations.get(location_id)
        proof = proofs.get(proof_id)
        if location is None:
            counters["missingLocation"] += 1
            continue
        if proof is None:
            counters["missingProof"] += 1
            continue
        try:
            proof_location = common.require_int(proof.get("location_id"), "proof.location_id")
        except common.AdapterError:
            counters["invalidProofLocation"] += 1
            continue
        if proof_location != location_id:
            counters["proofLocationMismatch"] += 1
            continue
        if isinstance(proof.get("date"), str) and proof["date"] != price.get("date"):
            counters["proofDateMismatch"] += 1
            continue
        country = common.safe_text(location.get("osm_address_country_code"), max_len=8)
        if not country or country.upper() != common.MARKET:
            counters["outsideGermany"] += 1
            continue
        match = location_matches.get(location_id)
        if match is None:
            match = common.match_retailer(location, aliases)
            location_matches[location_id] = match
        retailer_key, match_kind, alias = match
        if retailer_key is None:
            label = alias or "<missing>"
            if match_kind == "ambiguous":
                ambiguous_aliases[label] += 1
                counters["ambiguousRetailer"] += 1
            else:
                unmatched_aliases[label] += 1
                counters["unmatchedRetailer"] += 1
            continue
        try:
            record = _retailer_record(price, location, proof, retailer_key, metadata)
        except common.AdapterError:
            counters["invalidPriceContext"] += 1
            continue

        raw_fingerprint = hashlib.sha256(common.canonical_json(price).encode("utf-8")).hexdigest()
        prior = seen_price_ids.get(price_id)
        if prior is not None:
            prior_fingerprint, prior_evidence_id = prior
            if prior_fingerprint != raw_fingerprint:
                counters["conflictingPriceIDs"] += 1
                conflicted_price_ids.add(price_id)
                seen_price_ids.pop(price_id, None)
                observations_by_id.pop(prior_evidence_id, None)
                observation_context.pop(prior_evidence_id, None)
            else:
                counters["duplicatePriceIDs"] += 1
            continue

        seen_price_ids[price_id] = (raw_fingerprint, record["id"])
        observations_by_id[record["id"]] = record
        proof_type = common.safe_text(proof.get("type"), max_len=32) or "UNKNOWN"
        observation_context[record["id"]] = (retailer_key, proof_type, match_kind)

    retailer_counts: Counter[str] = Counter()
    proof_types: Counter[str] = Counter()
    for retailer_key, proof_type, match_kind in observation_context.values():
        retailer_counts[retailer_key] += 1
        proof_types[proof_type] += 1
        counters[f"matchedBy:{match_kind}"] += 1
    counters["included"] = len(observations_by_id)

    observations = sorted(observations_by_id.values(), key=lambda item: (item["gtin"], item["retailerKey"], item["observedAt"], item["sourceRecordID"]))
    envelope = common.evidence_envelope(_source(metadata, aliases, policy), observations)
    normalized_bytes = (json.dumps(envelope, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    normalized_digest = hashlib.sha256(normalized_bytes).hexdigest()

    previous = _load_previous(previous_evidence_path)
    previous_ids = {
        item["id"]
        for item in previous.get("retailerEvidence", [])
        if isinstance(item, dict) and item.get("sourceKey") == common.SOURCE_KEY and isinstance(item.get("id"), str)
    } if previous else set()
    current_ids = set(observations_by_id)
    additions = len(current_ids - previous_ids)
    unchanged = len(current_ids & previous_ids)
    complete = metadata["downloadComplete"] is True
    removed_ids = sorted(previous_ids - current_ids) if previous and complete else []

    ambiguous_report = _bounded_alias_map(ambiguous_aliases)
    unmatched_report = _bounded_alias_map(unmatched_aliases)
    review_queue = [
        {"kind": "retailer-alias", "state": "ambiguous", "value": value, "count": count}
        for value, count in ambiguous_report.items()
    ] + [
        {"kind": "retailer-alias", "state": "unmatched", "value": value, "count": count}
        for value, count in unmatched_report.items()
    ]
    observed_times = [item["observedAt"] for item in observations]
    quality = {
        "schemaVersion": 1,
        "sourceKey": common.SOURCE_KEY,
        "snapshotID": metadata["snapshotID"],
        "retrievedAt": metadata["retrievedAt"],
        "inputCompleteness": "complete" if complete else "partial",
        "aliasVersion": aliases.version,
        "counts": dict(sorted(counters.items())),
        "retailerCounts": dict(sorted(retailer_counts.items())),
        "proofTypeCounts": dict(sorted(proof_types.items())),
        "oldestObservationAt": min(observed_times) if observed_times else None,
        "newestObservationAt": max(observed_times) if observed_times else None,
        "ambiguousAliasObservationCount": sum(ambiguous_aliases.values()),
        "unmatchedAliasObservationCount": sum(unmatched_aliases.values()),
        "ambiguousAliases": ambiguous_report,
        "unmatchedAliases": unmatched_report,
        "aliasReportTruncated": len(ambiguous_aliases) > MAX_ALIAS_REPORT_VALUES or len(unmatched_aliases) > MAX_ALIAS_REPORT_VALUES,
        "proofImageBinariesIncluded": False,
        "personalContributorFieldsIncluded": False,
        "observationalOnly": True,
        "noCompletenessClaim": True,
    }
    changes = {
        "schemaVersion": 1,
        "sourceKey": common.SOURCE_KEY,
        "snapshotID": metadata["snapshotID"],
        "sourcePayloadSha256": metadata["payloadSha256"],
        "normalizedEvidenceSha256": normalized_digest,
        "baseline": "previous-evidence" if previous else "none",
        "additions": additions,
        "unchanged": unchanged,
        "formulationChanges": 0,
        "removals": len(removed_ids),
        "removedSelections": [],
        "removedRetailerEvidenceIDs": removed_ids,
        "reviewQueue": review_queue,
        "aliasVersion": aliases.version,
        "inputCompleteness": "complete" if complete else "partial",
        "observationalOnly": True,
        "noCompletenessClaim": True,
    }
    return envelope, quality, changes
