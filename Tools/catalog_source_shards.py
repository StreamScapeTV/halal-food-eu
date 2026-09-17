#!/usr/bin/env python3
"""Validate Git-tracked catalog CSV shards and project them into evidence."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import evidence_model

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = 1
CSV_SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$|^und$")
IDENTITY_CONFIDENCE = {"low", "medium", "high"}
RETAILER_KINDS = {"community-store-report", "retailer-observation"}
CSV_COLUMNS = [
    "gtin", "original_barcode", "market", "brand", "product_name", "variant", "quantity", "category",
    "identity_confidence", "identity_source_key", "identity_source_record_id", "identity_retrieved_at",
    "ingredients_text", "ingredients_language", "allergens_text", "ingredients_source_key",
    "ingredients_source_record_id", "ingredients_retrieved_at", "ingredients_observed_at", "ingredients_source_url",
    "retailer_kind", "retailer_key", "retailer_confidence", "retailer_source_key", "retailer_source_record_id",
    "retailer_retrieved_at", "retailer_observed_at", "retailer_snapshot_at", "retailer_location_id",
    "retailer_source_url", "retailer_scope", "retailer_limitations",
]


class CatalogSourceError(ValueError):
    pass


@dataclass(frozen=True)
class ValidatedSourceSet:
    manifest_path: Path
    manifest: dict[str, Any]
    rows: list[dict[str, str]]
    source_policies: dict[str, dict[str, Any]]


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CatalogSourceError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise CatalogSourceError(f"{label} must be a JSON object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CatalogSourceError(f"{label} must be a non-empty string")
    return value.strip()


def _validate_timestamp(value: str, label: str) -> None:
    if not value.endswith("Z") or "T" not in value:
        raise CatalogSourceError(f"{label} must be explicit UTC ISO-8601 ending in Z")


def _valid_gtin(value: str) -> bool:
    if len(value) != 14 or not value.isdigit():
        return False
    digits = [int(ch) for ch in value]
    total = sum((3 if index % 2 == 0 else 1) * digit for index, digit in enumerate(digits[:-1]))
    return (10 - (total % 10)) % 10 == digits[-1]


def _validate_https(value: str, label: str) -> None:
    if value and not value.startswith("https://"):
        raise CatalogSourceError(f"{label} must be HTTPS")


def _policy_hosts(policy: dict[str, Any], source_key: str, *fields: str) -> set[str]:
    hosts: set[str] = set()
    for field in fields:
        raw = policy.get(field, [])
        if raw is None:
            raw = []
        if not isinstance(raw, list):
            raise CatalogSourceError(f"source {source_key}.{field} must be an array")
        for index, value in enumerate(raw):
            host = _require_string(value, f"source {source_key}.{field}[{index}]").lower().rstrip(".")
            if ":" in host or "/" in host or "@" in host:
                raise CatalogSourceError(f"source {source_key}.{field}[{index}] must be hostname-only")
            hosts.add(host)
    return hosts


def _validate_source_url(value: str, source_key: str, policy: dict[str, Any], label: str, *, acquisition: bool = False) -> None:
    _validate_https(value, label)
    if not value:
        return
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname is None or parsed.username is not None or parsed.password is not None:
        raise CatalogSourceError(f"{label} must be a canonical HTTPS URL")
    fields = ("allowedAcquisitionHosts",) if acquisition else ("allowedReferenceHosts", "allowedAcquisitionHosts")
    allowed = _policy_hosts(policy, source_key, *fields)
    if not allowed:
        raise CatalogSourceError(f"source {source_key} has no reviewed hosts for {label}")
    if parsed.hostname.lower().rstrip(".") not in allowed:
        raise CatalogSourceError(f"{label} host is not admitted by source policy {source_key}")


def _resolve_repo_path(raw: Any, label: str) -> Path:
    rel = _require_string(raw, label)
    path = (ROOT / rel).resolve()
    root = ROOT.resolve()
    if path != root and root not in path.parents:
        raise CatalogSourceError(f"{label} escapes repository root")
    return path


def _load_policy(source: dict[str, Any]) -> dict[str, Any]:
    source_key = _require_string(source.get("sourceKey"), "source.sourceKey")
    policy_path = _resolve_repo_path(source.get("policyPath"), f"source {source_key}.policyPath")
    expected_sha = _require_string(source.get("policySha256"), f"source {source_key}.policySha256")
    if not SHA256_RE.fullmatch(expected_sha):
        raise CatalogSourceError(f"source {source_key}.policySha256 is invalid")
    actual_sha = file_sha256(policy_path)
    if actual_sha != expected_sha:
        raise CatalogSourceError(f"source policy digest mismatch for {source_key}")
    policy = _load_json(policy_path, f"source policy {source_key}")
    if policy.get("sourceKey") != source_key:
        raise CatalogSourceError(f"source policy key mismatch for {source_key}")
    db_license = policy.get("databaseLicense")
    if not isinstance(db_license, dict) or db_license.get("identifier") != "ODbL":
        raise CatalogSourceError(f"source {source_key} is not admitted for Git/bundle redistribution under ODbL")
    if not isinstance(policy.get("attribution"), str) or not policy["attribution"].strip():
        raise CatalogSourceError(f"source {source_key} has no attribution")
    if policy.get("accessMethod") not in {"public-bulk", "public-api"}:
        raise CatalogSourceError(f"source {source_key} has unsupported access method")
    return policy


def _bucket_for(gtin: str, bucket_count: int) -> int:
    digest = hashlib.sha256(gtin.encode("ascii")).digest()
    return int.from_bytes(digest[:8], "big") % bucket_count


def logical_sha256(rows: list[dict[str, str]]) -> str:
    ordered = sorted(rows, key=lambda row: (row["gtin"], row["market"]))
    return hashlib.sha256(canonical_json(ordered).encode("utf-8")).hexdigest()


def _read_shard(path: Path, expected_columns: list[str], label: str, compression: str) -> list[dict[str, str]]:
    stored = path.read_bytes()
    if compression == "gzip":
        try:
            data = gzip.decompress(stored)
        except OSError as exc:
            raise CatalogSourceError(f"{label} is not valid gzip: {exc}") from exc
    elif compression == "xz":
        try:
            data = lzma.decompress(stored)
        except lzma.LZMAError as exc:
            raise CatalogSourceError(f"{label} is not valid xz: {exc}") from exc
    elif compression == "none":
        data = stored
    else:
        raise CatalogSourceError(f"{label} has unsupported compression {compression!r}")
    if b"\r" in data:
        raise CatalogSourceError(f"{label} must use LF line endings")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CatalogSourceError(f"{label} is not UTF-8") from exc
    reader = csv.DictReader(io.StringIO(text, newline=""))
    if reader.fieldnames != expected_columns:
        raise CatalogSourceError(f"{label} CSV header differs from reviewed schema")
    rows: list[dict[str, str]] = []
    for index, row in enumerate(reader, start=2):
        if None in row:
            raise CatalogSourceError(f"{label}:{index} contains extra columns")
        rows.append({key: value if value is not None else "" for key, value in row.items()})
    return rows


def _validate_row(row: dict[str, str], *, market: str, source_policies: dict[str, dict[str, Any]], label: str) -> None:
    sources = set(source_policies)
    gtin = row["gtin"]
    if not _valid_gtin(gtin):
        raise CatalogSourceError(f"{label}.gtin must be canonical valid GTIN-14")
    if row["market"] != market:
        raise CatalogSourceError(f"{label}.market must equal {market}")
    _require_string(row["original_barcode"], f"{label}.original_barcode")
    _require_string(row["product_name"], f"{label}.product_name")
    if row["identity_confidence"] not in IDENTITY_CONFIDENCE:
        raise CatalogSourceError(f"{label}.identity_confidence is unsupported")
    if row["identity_source_key"] not in sources:
        raise CatalogSourceError(f"{label}.identity_source_key is not admitted")
    _require_string(row["identity_source_record_id"], f"{label}.identity_source_record_id")
    _validate_timestamp(_require_string(row["identity_retrieved_at"], f"{label}.identity_retrieved_at"), f"{label}.identity_retrieved_at")

    ingredients_text = row["ingredients_text"]
    ingredient_metadata = [
        row["ingredients_language"], row["allergens_text"], row["ingredients_source_key"],
        row["ingredients_source_record_id"], row["ingredients_retrieved_at"], row["ingredients_observed_at"],
        row["ingredients_source_url"],
    ]
    if ingredients_text.strip():
        if row["ingredients_source_key"] not in sources:
            raise CatalogSourceError(f"{label}.ingredients_source_key is not admitted")
        _require_string(row["ingredients_source_record_id"], f"{label}.ingredients_source_record_id")
        _validate_timestamp(_require_string(row["ingredients_retrieved_at"], f"{label}.ingredients_retrieved_at"), f"{label}.ingredients_retrieved_at")
        if not LANGUAGE_RE.fullmatch(row["ingredients_language"]):
            raise CatalogSourceError(f"{label}.ingredients_language is invalid")
        if row["ingredients_observed_at"]:
            _validate_timestamp(row["ingredients_observed_at"], f"{label}.ingredients_observed_at")
        _validate_source_url(
            row["ingredients_source_url"], row["ingredients_source_key"],
            source_policies[row["ingredients_source_key"]], f"{label}.ingredients_source_url",
        )
    elif any(ingredient_metadata):
        raise CatalogSourceError(f"{label} has ingredient metadata without ingredients_text")

    retailer_kind = row["retailer_kind"]
    retailer_metadata = [
        row["retailer_key"], row["retailer_confidence"], row["retailer_source_key"], row["retailer_source_record_id"],
        row["retailer_retrieved_at"], row["retailer_observed_at"], row["retailer_snapshot_at"], row["retailer_location_id"],
        row["retailer_source_url"], row["retailer_scope"], row["retailer_limitations"],
    ]
    if retailer_kind:
        if retailer_kind not in RETAILER_KINDS:
            raise CatalogSourceError(f"{label}.retailer_kind is unsupported")
        _require_string(row["retailer_key"], f"{label}.retailer_key")
        if row["retailer_confidence"] not in IDENTITY_CONFIDENCE:
            raise CatalogSourceError(f"{label}.retailer_confidence is unsupported")
        if row["retailer_source_key"] not in sources:
            raise CatalogSourceError(f"{label}.retailer_source_key is not admitted")
        _require_string(row["retailer_source_record_id"], f"{label}.retailer_source_record_id")
        _validate_timestamp(_require_string(row["retailer_retrieved_at"], f"{label}.retailer_retrieved_at"), f"{label}.retailer_retrieved_at")
        if retailer_kind == "retailer-observation" and not row["retailer_observed_at"]:
            raise CatalogSourceError(f"{label}.retailer_observed_at is required for retailer-observation")
        for field in ("retailer_observed_at", "retailer_snapshot_at"):
            if row[field]:
                _validate_timestamp(row[field], f"{label}.{field}")
        _validate_source_url(
            row["retailer_source_url"], row["retailer_source_key"],
            source_policies[row["retailer_source_key"]], f"{label}.retailer_source_url",
        )
        _require_string(row["retailer_limitations"], f"{label}.retailer_limitations")
    elif any(retailer_metadata):
        raise CatalogSourceError(f"{label} has retailer metadata without retailer_kind")


def validate_source_set(manifest_path: Path) -> ValidatedSourceSet:
    manifest_path = manifest_path.resolve()
    manifest = _load_json(manifest_path, "bundle source manifest")
    required = {
        "schemaVersion", "csvSchemaVersion", "datasetID", "market", "catalogVersion", "selectionPolicyVersion",
        "generatedAt", "columns", "selectionPolicy", "qualityPolicy", "primaryQualitySourceKey", "sources",
        "shardStrategy", "shards", "recordCount", "logicalSha256",
    }
    allowed = required | {"migrationSummary"}
    if set(manifest) != allowed and not (set(manifest) == required):
        extra = sorted(set(manifest) - allowed)
        missing = sorted(required - set(manifest))
        raise CatalogSourceError(f"bundle source manifest keys differ; missing={missing}, extra={extra}")
    if manifest.get("schemaVersion") != SCHEMA_VERSION or manifest.get("csvSchemaVersion") != CSV_SCHEMA_VERSION:
        raise CatalogSourceError("unsupported bundle source manifest/schema version")
    _require_string(manifest.get("datasetID"), "datasetID")
    market = _require_string(manifest.get("market"), "market")
    if not re.fullmatch(r"[A-Z]{2}", market):
        raise CatalogSourceError("market must be ISO alpha-2 uppercase")
    _require_string(manifest.get("catalogVersion"), "catalogVersion")
    selection_version = _require_string(manifest.get("selectionPolicyVersion"), "selectionPolicyVersion")
    generated_at = _require_string(manifest.get("generatedAt"), "generatedAt")
    _validate_timestamp(generated_at, "generatedAt")
    if manifest.get("columns") != CSV_COLUMNS:
        raise CatalogSourceError("columns differ from reviewed CSV schema")

    for key, expected_version in (("selectionPolicy", selection_version), ("qualityPolicy", None)):
        binding = manifest.get(key)
        if not isinstance(binding, dict) or set(binding) != {"path", "sha256"}:
            raise CatalogSourceError(f"{key} must contain only path/sha256")
        path = _resolve_repo_path(binding["path"], f"{key}.path")
        sha = _require_string(binding["sha256"], f"{key}.sha256")
        if not SHA256_RE.fullmatch(sha) or file_sha256(path) != sha:
            raise CatalogSourceError(f"{key} digest mismatch")
        data = _load_json(path, key)
        if key == "selectionPolicy":
            if data.get("policyVersion") != expected_version or data.get("targetMarket") != market:
                raise CatalogSourceError("selection policy identity differs from source manifest")

    source_items = manifest.get("sources")
    if not isinstance(source_items, list) or not source_items:
        raise CatalogSourceError("sources must be a non-empty array")
    source_policies: dict[str, dict[str, Any]] = {}
    for index, source in enumerate(source_items):
        if not isinstance(source, dict) or set(source) != {
            "sourceKey", "policyPath", "policySha256", "snapshotID", "revision", "reference", "retrievedAt"
        }:
            raise CatalogSourceError(f"sources[{index}] has unexpected shape")
        key = _require_string(source["sourceKey"], f"sources[{index}].sourceKey")
        if key in source_policies:
            raise CatalogSourceError(f"duplicate source {key}")
        source_policies[key] = _load_policy(source)
        _require_string(source["snapshotID"], f"source {key}.snapshotID")
        _require_string(source["revision"], f"source {key}.revision")
        reference = _require_string(source["reference"], f"source {key}.reference")
        _validate_source_url(reference, key, source_policies[key], f"source {key}.reference", acquisition=True)
        _validate_timestamp(_require_string(source["retrievedAt"], f"source {key}.retrievedAt"), f"source {key}.retrievedAt")
    primary = _require_string(manifest.get("primaryQualitySourceKey"), "primaryQualitySourceKey")
    if primary not in source_policies:
        raise CatalogSourceError("primaryQualitySourceKey is not admitted")

    strategy = manifest.get("shardStrategy")
    if not isinstance(strategy, dict) or set(strategy) != {"kind", "bucketCount", "key"}:
        raise CatalogSourceError("shardStrategy has unexpected shape")
    if strategy.get("kind") != "sha256-mod" or strategy.get("key") != "gtin":
        raise CatalogSourceError("only sha256-mod/gtin sharding is supported")
    bucket_count = strategy.get("bucketCount")
    if not isinstance(bucket_count, int) or not (1 <= bucket_count <= 64):
        raise CatalogSourceError("shardStrategy.bucketCount must be 1...64")

    shards = manifest.get("shards")
    if not isinstance(shards, list) or len(shards) != bucket_count:
        raise CatalogSourceError("shards count must equal shardStrategy.bucketCount")
    rows: list[dict[str, str]] = []
    seen_buckets: set[int] = set()
    seen_gtins: set[str] = set()
    manifest_dir = manifest_path.parent.resolve()
    for index, shard in enumerate(shards):
        expected_keys = {"bucket", "path", "compression", "recordCount", "byteCount", "sha256"}
        if not isinstance(shard, dict) or set(shard) != expected_keys:
            raise CatalogSourceError(f"shards[{index}] has unexpected shape")
        bucket = shard["bucket"]
        if not isinstance(bucket, int) or not (0 <= bucket < bucket_count) or bucket in seen_buckets:
            raise CatalogSourceError(f"shards[{index}].bucket is invalid/duplicate")
        seen_buckets.add(bucket)
        rel = _require_string(shard["path"], f"shards[{index}].path")
        path = (manifest_dir / rel).resolve()
        if path != manifest_dir and manifest_dir not in path.parents:
            raise CatalogSourceError(f"shards[{index}].path escapes manifest directory")
        compression = _require_string(shard["compression"], f"shards[{index}].compression")
        if compression not in {"none", "gzip"}:
            raise CatalogSourceError(f"shards[{index}].compression is unsupported")
        if shard["byteCount"] != path.stat().st_size:
            raise CatalogSourceError(f"shards[{index}].byteCount mismatch")
        sha = _require_string(shard["sha256"], f"shards[{index}].sha256")
        if not SHA256_RE.fullmatch(sha) or file_sha256(path) != sha:
            raise CatalogSourceError(f"shards[{index}] digest mismatch")
        shard_rows = _read_shard(path, CSV_COLUMNS, f"shards[{index}]", compression)
        if shard["recordCount"] != len(shard_rows):
            raise CatalogSourceError(f"shards[{index}].recordCount mismatch")
        previous = None
        for row_index, row in enumerate(shard_rows, start=1):
            label = f"shards[{index}] row {row_index}"
            _validate_row(row, market=market, source_policies=source_policies, label=label)
            if _bucket_for(row["gtin"], bucket_count) != bucket:
                raise CatalogSourceError(f"{label} is in wrong shard bucket")
            if previous is not None and row["gtin"] <= previous:
                raise CatalogSourceError(f"{label} is not strictly sorted by GTIN")
            previous = row["gtin"]
            if row["gtin"] in seen_gtins:
                raise CatalogSourceError(f"duplicate GTIN across shards: {row['gtin']}")
            seen_gtins.add(row["gtin"])
            rows.append(row)
    if seen_buckets != set(range(bucket_count)):
        raise CatalogSourceError("shard buckets are incomplete")
    if manifest.get("recordCount") != len(rows):
        raise CatalogSourceError("recordCount differs from shard content")
    logical = _require_string(manifest.get("logicalSha256"), "logicalSha256")
    if not SHA256_RE.fullmatch(logical) or logical_sha256(rows) != logical:
        raise CatalogSourceError("logicalSha256 differs from canonical shard content")
    return ValidatedSourceSet(manifest_path, manifest, sorted(rows, key=lambda row: row["gtin"]), source_policies)


def _source_records(source_set: ValidatedSourceSet) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    by_key = {item["sourceKey"]: item for item in source_set.manifest["sources"]}
    for key in sorted(by_key):
        source = by_key[key]
        policy = source_set.source_policies[key]
        records.append({
            "sourceKey": key,
            "operator": policy.get("operator", key),
            "sourceClass": policy.get("sourceClass", "open-database"),
            "reference": source["reference"],
            "accessMethod": policy.get("accessMethod", "public-bulk"),
            "markets": [source_set.manifest["market"]],
            "retrievedAt": source["retrievedAt"],
            "sourceSnapshotID": source["snapshotID"],
        })
    return records


def evidence_envelope(source_set: ValidatedSourceSet) -> dict[str, Any]:
    envelope: dict[str, Any] = {
        "schemaVersion": 1,
        "sources": _source_records(source_set),
        "identities": [],
        "ingredients": [],
        "retailerEvidence": [],
        "remoteImages": [],
        "packageEvidence": [],
        "certifications": [],
        "reviews": [],
        "assessments": [],
        "validityEvents": [],
        "currentSelections": [],
        "releases": [],
    }
    for row in source_set.rows:
        identity = {
            "gtin": row["gtin"], "originalBarcode": row["original_barcode"], "market": row["market"],
            "sourceKey": row["identity_source_key"], "sourceRecordID": row["identity_source_record_id"],
            "name": row["product_name"], "retrievedAt": row["identity_retrieved_at"],
            "confidence": row["identity_confidence"],
        }
        if row["brand"]: identity["brand"] = row["brand"]
        if row["quantity"]: identity["quantity"] = row["quantity"]
        if row["category"]: identity["categories"] = [row["category"]]
        identity["id"] = evidence_model.derive_id("identities", identity)
        envelope["identities"].append(identity)

        ingredient_id = None
        if row["ingredients_text"].strip():
            ingredient = {
                "gtin": row["gtin"], "market": row["market"], "sourceKey": row["ingredients_source_key"],
                "sourceRecordID": row["ingredients_source_record_id"], "ingredientsText": row["ingredients_text"],
                "languageCode": row["ingredients_language"], "retrievedAt": row["ingredients_retrieved_at"],
                "captureMethod": "source-text", "verificationState": "unverified",
            }
            if row["ingredients_observed_at"]: ingredient["observedAt"] = row["ingredients_observed_at"]
            if row["allergens_text"]: ingredient["allergensText"] = row["allergens_text"]
            ingredient["contentHash"] = evidence_model.formulation_hash(ingredient)
            ingredient["id"] = evidence_model.derive_id("ingredients", ingredient)
            ingredient_id = ingredient["id"]
            envelope["ingredients"].append(ingredient)

        retailer_ids: list[str] = []
        if row["retailer_kind"]:
            retailer = {
                "kind": row["retailer_kind"], "retailerKey": row["retailer_key"], "gtin": row["gtin"],
                "market": row["market"], "sourceKey": row["retailer_source_key"],
                "sourceRecordID": row["retailer_source_record_id"], "retrievedAt": row["retailer_retrieved_at"],
                "confidence": row["retailer_confidence"], "limitations": row["retailer_limitations"],
            }
            if row["retailer_observed_at"]: retailer["observedAt"] = row["retailer_observed_at"]
            if row["retailer_snapshot_at"]: retailer["snapshotAt"] = row["retailer_snapshot_at"]
            if row["retailer_location_id"]: retailer["locationID"] = row["retailer_location_id"]
            if row["retailer_scope"]: retailer["scope"] = row["retailer_scope"]
            retailer["id"] = evidence_model.derive_id("retailerEvidence", retailer)
            retailer_ids.append(retailer["id"])
            envelope["retailerEvidence"].append(retailer)

        selection = {
            "gtin": row["gtin"], "market": row["market"], "identityObservationID": identity["id"],
            "certificationIDs": [], "retailerEvidenceIDs": retailer_ids, "remoteImageIDs": [], "conflictFlags": [],
        }
        if ingredient_id is not None: selection["ingredientObservationID"] = ingredient_id
        selection["id"] = evidence_model.derive_id("currentSelections", selection)
        envelope["currentSelections"].append(selection)
    evidence_model.validate_envelope(envelope)
    return envelope


def write_evidence(source_set: ValidatedSourceSet, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence_envelope(source_set), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--manifest", type=Path, required=True)
    evidence = sub.add_parser("evidence")
    evidence.add_argument("--manifest", type=Path, required=True)
    evidence.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    source_set = validate_source_set(args.manifest)
    if args.command == "evidence":
        write_evidence(source_set, args.output)
    print(f"Validated Git-backed catalog source {source_set.manifest['datasetID']} with {len(source_set.rows)} records; logicalSha256={source_set.manifest['logicalSha256']}")


if __name__ == "__main__":
    main()
