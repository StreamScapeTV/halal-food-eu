#!/usr/bin/env python3
"""Compile reviewed evidence into the immutable production SQLite catalog.

The compiler is deliberately offline. It consumes only local, validated evidence
and reviewed source-policy metadata; acquisition is a separate workflow stage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import evidence_model

APPLICATION_ID = 1_212_564_821  # ASCII "HFEU"
SCHEMA_VERSION = 2
BUILDER_VERSION = "production-catalog-v1"
STATUS_VALUES = {"halal-certified", "halal-reviewed", "not-halal", "questionable", "unknown"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$")

SCHEMA = """
CREATE TABLE catalog_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    operator TEXT NOT NULL CHECK(length(trim(operator)) > 0),
    source_class TEXT NOT NULL CHECK(length(trim(source_class)) > 0),
    reference TEXT NOT NULL CHECK(length(trim(reference)) > 0),
    license TEXT NOT NULL CHECK(length(trim(license)) > 0),
    attribution TEXT NOT NULL CHECK(length(trim(attribution)) > 0),
    retrieved_at TEXT NOT NULL,
    source_snapshot_id TEXT,
    policy_schema_version INTEGER NOT NULL,
    policy_sha256 TEXT NOT NULL CHECK(length(policy_sha256) = 64)
);

CREATE TABLE products (
    gtin TEXT PRIMARY KEY CHECK(length(gtin) = 14 AND gtin NOT GLOB '*[^0-9]*'),
    market TEXT NOT NULL CHECK(length(market) = 2),
    selection_id TEXT NOT NULL UNIQUE,
    identity_evidence_id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    brand TEXT,
    brand_owner TEXT,
    quantity TEXT,
    identity_source_id INTEGER NOT NULL,
    identity_source_record_id TEXT NOT NULL CHECK(length(trim(identity_source_record_id)) > 0),
    current_observation_id INTEGER,
    current_assessment_id INTEGER,
    conflict_flags_json TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY(identity_source_id) REFERENCES sources(id) ON DELETE RESTRICT,
    FOREIGN KEY(current_observation_id) REFERENCES product_observations(id) ON DELETE RESTRICT,
    FOREIGN KEY(current_assessment_id) REFERENCES product_assessments(id) ON DELETE RESTRICT
) WITHOUT ROWID;

CREATE TABLE product_observations (
    id INTEGER PRIMARY KEY,
    evidence_id TEXT NOT NULL UNIQUE,
    gtin TEXT NOT NULL,
    source_id INTEGER NOT NULL,
    source_record_id TEXT NOT NULL CHECK(length(trim(source_record_id)) > 0),
    ingredients_text TEXT NOT NULL,
    language_code TEXT NOT NULL CHECK(length(trim(language_code)) > 0),
    allergens_text TEXT,
    traces_text TEXT,
    observed_at TEXT,
    retrieved_at TEXT NOT NULL,
    ingredients_hash TEXT NOT NULL CHECK(length(ingredients_hash) = 64),
    verification_state TEXT NOT NULL,
    FOREIGN KEY(gtin) REFERENCES products(gtin) ON DELETE RESTRICT,
    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE RESTRICT
);

CREATE TABLE product_assessments (
    id INTEGER PRIMARY KEY,
    evidence_id TEXT NOT NULL UNIQUE,
    gtin TEXT NOT NULL,
    observation_id INTEGER,
    status TEXT NOT NULL CHECK(status IN (
        'halal-certified', 'halal-reviewed', 'not-halal', 'questionable', 'unknown'
    )),
    summary TEXT NOT NULL CHECK(length(trim(summary)) > 0),
    methodology_version TEXT NOT NULL CHECK(length(trim(methodology_version)) > 0),
    reviewed_at TEXT NOT NULL,
    recheck_at TEXT,
    FOREIGN KEY(gtin) REFERENCES products(gtin) ON DELETE RESTRICT,
    FOREIGN KEY(observation_id) REFERENCES product_observations(id) ON DELETE RESTRICT,
    CHECK(observation_id IS NOT NULL OR status = 'unknown')
);

CREATE TABLE assessment_reasons (
    id INTEGER PRIMARY KEY,
    assessment_id INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    code TEXT NOT NULL CHECK(length(trim(code)) > 0),
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    detail TEXT NOT NULL CHECK(length(trim(detail)) > 0),
    ingredient TEXT,
    severity TEXT NOT NULL CHECK(severity IN ('positive','informational','caution','prohibitive')),
    evidence_ids_json TEXT NOT NULL DEFAULT '[]',
    FOREIGN KEY(assessment_id) REFERENCES product_assessments(id) ON DELETE CASCADE,
    UNIQUE(assessment_id, position)
);

CREATE TABLE certification_evidence (
    id INTEGER PRIMARY KEY,
    evidence_id TEXT NOT NULL UNIQUE,
    assessment_id INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    source_id INTEGER NOT NULL,
    certifying_body TEXT NOT NULL,
    scheme TEXT NOT NULL,
    certificate_reference TEXT NOT NULL,
    scope TEXT NOT NULL,
    valid_from TEXT,
    valid_until TEXT,
    last_checked_at TEXT NOT NULL,
    FOREIGN KEY(assessment_id) REFERENCES product_assessments(id) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE RESTRICT,
    UNIQUE(assessment_id, position)
);

CREATE TABLE retailer_evidence (
    id INTEGER PRIMARY KEY,
    evidence_id TEXT NOT NULL UNIQUE,
    gtin TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    source_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    retailer_key TEXT NOT NULL,
    observed_at TEXT,
    snapshot_at TEXT,
    scope TEXT,
    location_id TEXT,
    limitations TEXT NOT NULL,
    FOREIGN KEY(gtin) REFERENCES products(gtin) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE RESTRICT,
    UNIQUE(gtin, position)
);

CREATE TABLE remote_image_references (
    id INTEGER PRIMARY KEY,
    evidence_id TEXT NOT NULL UNIQUE,
    gtin TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    source_id INTEGER NOT NULL,
    purpose TEXT NOT NULL,
    url TEXT NOT NULL CHECK(url LIKE 'https://%'),
    image_id TEXT NOT NULL,
    revision TEXT,
    FOREIGN KEY(gtin) REFERENCES products(gtin) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE RESTRICT,
    UNIQUE(gtin, position)
);

CREATE TABLE basic_exclusions (
    gtin TEXT NOT NULL CHECK(length(gtin) = 14 AND gtin NOT GLOB '*[^0-9]*'),
    market TEXT NOT NULL CHECK(length(market) = 2),
    selection_policy_version TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(length(trim(reason)) > 0),
    PRIMARY KEY(gtin, market)
) WITHOUT ROWID;

CREATE INDEX idx_product_observations_gtin ON product_observations(gtin, observed_at DESC);
CREATE INDEX idx_product_assessments_gtin ON product_assessments(gtin, reviewed_at DESC);
CREATE INDEX idx_product_assessments_status ON product_assessments(status);
CREATE INDEX idx_assessment_reasons_order ON assessment_reasons(assessment_id, position);
CREATE INDEX idx_certification_assessment ON certification_evidence(assessment_id, position);
CREATE INDEX idx_retailer_gtin_recency ON retailer_evidence(gtin, observed_at DESC, snapshot_at DESC);
CREATE INDEX idx_remote_images_gtin ON remote_image_references(gtin, position);
"""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path, label: str) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _policy_license(policy: dict[str, Any]) -> str:
    license_value = policy.get("databaseLicense")
    if isinstance(license_value, dict):
        identifier = license_value.get("identifier") or license_value.get("name")
        if isinstance(identifier, str) and identifier.strip():
            return identifier.strip()
    license_value = policy.get("license")
    if isinstance(license_value, str) and license_value.strip():
        return license_value.strip()
    raise ValueError(f"source policy {policy.get('sourceKey')!r} has no database license")


def load_policies(paths: Iterable[Path]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in paths:
        policy = load_json(path, f"source policy {path}")
        source_key = policy.get("sourceKey")
        if not isinstance(source_key, str) or not source_key.strip():
            raise ValueError(f"source policy {path} has invalid sourceKey")
        if source_key in result:
            raise ValueError(f"duplicate source policy for {source_key}")
        schema_version = policy.get("schemaVersion")
        if not isinstance(schema_version, int) or schema_version <= 0:
            raise ValueError(f"source policy {source_key} has invalid schemaVersion")
        attribution = policy.get("attribution")
        if not isinstance(attribution, str) or not attribution.strip():
            raise ValueError(f"source policy {source_key} has no attribution")
        result[source_key] = {
            "path": path.as_posix(),
            "sha256": file_sha256(path),
            "schemaVersion": schema_version,
            "license": _policy_license(policy),
            "attribution": attribution.strip(),
        }
    return result


def load_basic_exclusions(path: Path | None, selection_policy_version: str) -> list[dict[str, str]]:
    if path is None:
        return []
    data = load_json(path, "basic exclusions")
    if data.get("schemaVersion") != 1:
        raise ValueError("basic exclusions require schemaVersion 1")
    if data.get("selectionPolicyVersion") != selection_policy_version:
        raise ValueError("basic exclusions selection-policy version mismatch")
    records = data.get("records")
    if not isinstance(records, list):
        raise ValueError("basic exclusions records must be an array")
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(records):
        if not isinstance(raw, dict) or set(raw) != {"gtin", "market", "reason"}:
            raise ValueError(f"basic exclusions[{index}] must contain only gtin/market/reason")
        gtin, market, reason = raw["gtin"], raw["market"], raw["reason"]
        if not isinstance(gtin, str) or len(gtin) != 14 or not gtin.isdigit():
            raise ValueError(f"basic exclusions[{index}].gtin is invalid")
        if not isinstance(market, str) or len(market) != 2 or market.upper() != market:
            raise ValueError(f"basic exclusions[{index}].market is invalid")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"basic exclusions[{index}].reason is invalid")
        key = (gtin, market)
        if key in seen:
            raise ValueError(f"duplicate basic exclusion {gtin}/{market}")
        seen.add(key)
        result.append({"gtin": gtin, "market": market, "reason": reason.strip()})
    return sorted(result, key=lambda item: (item["gtin"], item["market"]))


def _assert_build_metadata(catalog_version: str, selection_policy_version: str, generated_at: str, source_commit: str) -> None:
    for name, value in (("catalogVersion", catalog_version), ("selectionPolicyVersion", selection_policy_version), ("generatedAt", generated_at)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required")
    if not COMMIT_RE.fullmatch(source_commit):
        raise ValueError("source commit must be a lowercase hexadecimal Git commit identifier")
    if "T" not in generated_at or not generated_at.endswith("Z"):
        raise ValueError("generatedAt must be an explicit UTC ISO-8601 timestamp ending in Z")


def _source_ids(connection: sqlite3.Connection, projection: dict[str, Any], evidence: dict[str, Any], policies: dict[str, dict[str, Any]]) -> dict[str, int]:
    evidence_sources = {item["sourceKey"]: item for item in evidence["sources"]}
    result: dict[str, int] = {}
    for source in sorted(projection["sources"], key=lambda item: item["sourceKey"]):
        key = source["sourceKey"]
        policy = policies.get(key)
        if policy is None:
            raise ValueError(f"no reviewed source policy supplied for runtime source {key!r}")
        full = evidence_sources[key]
        cursor = connection.execute(
            """INSERT INTO sources(source_key,operator,source_class,reference,license,attribution,retrieved_at,source_snapshot_id,policy_schema_version,policy_sha256)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (key, source["operator"], source["sourceClass"], source["reference"], policy["license"], policy["attribution"], source["retrievedAt"], full.get("sourceSnapshotID"), policy["schemaVersion"], policy["sha256"]),
        )
        result[key] = int(cursor.lastrowid)
    return result


def _summary(assessment: dict[str, Any]) -> str:
    reasons = assessment.get("reasons") or []
    if not reasons:
        raise ValueError(f"assessment {assessment.get('id')} has no reasons")
    first = reasons[0]
    title = first.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"assessment {assessment.get('id')} first reason has no title")
    return title.strip()


def build_catalog(
    *, evidence_path: Path, database_path: Path, manifest_path: Path,
    policy_paths: list[Path], basic_exclusions_path: Path | None,
    catalog_version: str, selection_policy_version: str, generated_at: str,
    source_commit: str, workflow_run: str, logical_dump_path: Path | None = None,
    release_notes_path: Path | None = None, previous_manifest_path: Path | None = None,
    max_database_bytes: int = 250 * 1024 * 1024,
) -> dict[str, Any]:
    _assert_build_metadata(catalog_version, selection_policy_version, generated_at, source_commit)
    evidence = load_json(evidence_path, "evidence")
    evidence_model.validate_envelope(evidence)
    projection = evidence_model.runtime_projection(evidence)
    policies = load_policies(policy_paths)
    exclusions = load_basic_exclusions(basic_exclusions_path, selection_policy_version)

    products = projection["products"]
    if not products:
        raise ValueError("production catalog cannot be empty")
    product_keys = {(item["gtin"], item["market"]) for item in products}
    overlap = product_keys.intersection((item["gtin"], item["market"]) for item in exclusions)
    if overlap:
        raise ValueError(f"basic exclusions overlap detailed products: {sorted(overlap)[:5]}")

    methodology_versions = {item["assessment"]["methodologyVersion"] for item in products if item.get("assessment")}
    if len(methodology_versions) != 1:
        raise ValueError(f"runtime catalog must have one methodology version, got {sorted(methodology_versions)}")
    methodology_version = next(iter(methodology_versions))
    missing_assessment = [item["gtin"] for item in products if item.get("assessment") is None]
    if missing_assessment:
        raise ValueError(f"runtime products require an explicit reviewed assessment: {missing_assessment[:5]}")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix="production-catalog-", suffix=".sqlite3", dir=database_path.parent)
    os.close(fd)
    temporary = Path(temp_name)
    try:
        connection = sqlite3.connect(temporary)
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        connection.execute("PRAGMA page_size=4096")
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
        connection.executescript(SCHEMA)
        metadata = {
            "catalogVersion": catalog_version,
            "schemaVersion": str(SCHEMA_VERSION),
            "methodologyVersion": methodology_version,
            "selectionPolicyVersion": selection_policy_version,
            "generatedAt": generated_at,
            "sourceCommit": source_commit,
            "builderVersion": BUILDER_VERSION,
            "evidenceSchemaVersion": str(projection["evidenceSchemaVersion"]),
        }
        connection.executemany("INSERT INTO catalog_metadata(key,value) VALUES (?,?)", sorted(metadata.items()))
        source_ids = _source_ids(connection, projection, evidence, policies)

        for product in sorted(products, key=lambda item: (item["gtin"], item["market"])):
            identity = product["identity"]
            assessment = product["assessment"]
            connection.execute(
                """INSERT INTO products(gtin,market,selection_id,identity_evidence_id,name,brand,brand_owner,quantity,identity_source_id,identity_source_record_id,current_assessment_id,conflict_flags_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,NULL,?)""",
                (product["gtin"], product["market"], product["selectionID"], identity["id"], identity["name"], identity.get("brand"), identity.get("brandOwner"), identity.get("quantity"), source_ids[identity["sourceKey"]], identity["sourceRecordID"], canonical_json(product.get("conflictFlags", []))),
            )
            observation_id: int | None = None
            ingredient = product.get("ingredients")
            if ingredient is not None:
                cursor = connection.execute(
                    """INSERT INTO product_observations(evidence_id,gtin,source_id,source_record_id,ingredients_text,language_code,allergens_text,traces_text,observed_at,retrieved_at,ingredients_hash,verification_state)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (ingredient["id"], product["gtin"], source_ids[ingredient["sourceKey"]], ingredient["sourceRecordID"], ingredient["text"], ingredient["languageCode"], ingredient.get("allergensText"), ingredient.get("tracesText"), ingredient.get("observedAt"), ingredient["retrievedAt"], ingredient["contentHash"], ingredient["verificationState"]),
                )
                observation_id = int(cursor.lastrowid)

            if observation_id is None and assessment["status"] != "unknown":
                raise ValueError(f"{product['gtin']} lacks ingredient evidence but assessment is {assessment['status']}")
            cursor = connection.execute(
                """INSERT INTO product_assessments(evidence_id,gtin,observation_id,status,summary,methodology_version,reviewed_at,recheck_at)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (assessment["id"], product["gtin"], observation_id, assessment["status"], _summary(assessment), assessment["methodologyVersion"], assessment["assessedAt"], assessment.get("recheckAt")),
            )
            assessment_id = int(cursor.lastrowid)
            for position, reason in enumerate(assessment["reasons"]):
                connection.execute(
                    """INSERT INTO assessment_reasons(assessment_id,position,code,title,detail,ingredient,severity,evidence_ids_json)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (assessment_id, position, reason["code"], reason["title"], reason["detail"], reason.get("ingredient"), reason["severity"], canonical_json(sorted(reason.get("evidenceIDs", [])))),
                )
            for position, certification in enumerate(product["certifications"]):
                connection.execute(
                    """INSERT INTO certification_evidence(evidence_id,assessment_id,position,source_id,certifying_body,scheme,certificate_reference,scope,valid_from,valid_until,last_checked_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (certification["id"], assessment_id, position, source_ids[certification["sourceKey"]], certification["certifier"], certification["scheme"], certification["certificateReference"], certification["scope"], certification.get("effectiveAt"), certification.get("expiryAt"), certification["lastCheckedAt"]),
                )
            if assessment["status"] == "halal-certified" and not product["certifications"]:
                raise ValueError(f"halal-certified product {product['gtin']} has no certification evidence")
            for position, retailer in enumerate(product["retailerEvidence"]):
                connection.execute(
                    """INSERT INTO retailer_evidence(evidence_id,gtin,position,source_id,kind,retailer_key,observed_at,snapshot_at,scope,location_id,limitations)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (retailer["id"], product["gtin"], position, source_ids[retailer["sourceKey"]], retailer["kind"], retailer["retailerKey"], retailer.get("observedAt"), retailer.get("snapshotAt"), retailer.get("scope"), retailer.get("locationID"), retailer["limitations"]),
                )
            for position, image in enumerate(product["remoteImages"]):
                connection.execute(
                    """INSERT INTO remote_image_references(evidence_id,gtin,position,source_id,purpose,url,image_id,revision)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (image["id"], product["gtin"], position, source_ids[image["sourceKey"]], image["purpose"], image["url"], image["imageID"], image.get("revision")),
                )
            connection.execute("UPDATE products SET current_observation_id=?, current_assessment_id=? WHERE gtin=?", (observation_id, assessment_id, product["gtin"]))

        connection.executemany(
            "INSERT INTO basic_exclusions(gtin,market,selection_policy_version,reason) VALUES (?,?,?,?)",
            [(item["gtin"], item["market"], selection_policy_version, item["reason"]) for item in exclusions],
        )
        connection.commit()
        connection.execute("ANALYZE")
        connection.execute("VACUUM")
        connection.close()
        os.replace(temporary, database_path)
    finally:
        temporary.unlink(missing_ok=True)

    size = database_path.stat().st_size
    if size >= max_database_bytes:
        database_path.unlink(missing_ok=True)
        raise ValueError(f"catalog database size {size} exceeds reviewed budget {max_database_bytes}")
    digest = file_sha256(database_path)
    status_counts = Counter(item["assessment"]["status"] for item in products)
    counts = {
        "products": len(products),
        "ingredientObservations": sum(1 for item in products if item.get("ingredients") is not None),
        "assessments": len(products),
        "assessmentReasons": sum(len(item["assessment"]["reasons"]) for item in products),
        "certifications": sum(len(item["certifications"]) for item in products),
        "retailerEvidence": sum(len(item["retailerEvidence"]) for item in products),
        "remoteImageReferences": sum(len(item["remoteImages"]) for item in products),
        "basicExclusions": len(exclusions),
    }
    used_keys = {source["sourceKey"] for source in projection["sources"]}
    manifest = {
        "manifestSchemaVersion": 2,
        "catalogVersion": catalog_version,
        "schemaVersion": SCHEMA_VERSION,
        "methodologyVersion": methodology_version,
        "selectionPolicyVersion": selection_policy_version,
        "generatedAt": generated_at,
        "sourceCommit": source_commit,
        "workflowRun": workflow_run,
        "builderVersion": BUILDER_VERSION,
        "recordCount": len(products),
        "databaseBytes": size,
        "sha256": digest,
        "evidence": {"schemaVersion": projection["evidenceSchemaVersion"], "sha256": file_sha256(evidence_path)},
        "sourcePolicies": [{"sourceKey": key, **policies[key]} for key in sorted(used_keys)],
        "counts": counts,
        "statusDistribution": dict(sorted(status_counts.items())),
        "compatibility": {"minimumAppSchemaVersion": SCHEMA_VERSION, "maximumAppSchemaVersion": SCHEMA_VERSION},
        "budgets": {"databaseBytesLessThan": max_database_bytes},
        "rights": {
            "licenses": sorted({policies[key]["license"] for key in used_keys}),
            "attributions": sorted({policies[key]["attribution"] for key in used_keys}),
        },
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if logical_dump_path is not None:
        logical_dump_path.parent.mkdir(parents=True, exist_ok=True)
        logical_dump_path.write_text(canonical_json({"projection": projection, "basicExclusions": exclusions}) + "\n", encoding="utf-8")
    if release_notes_path is not None:
        previous = load_json(previous_manifest_path, "previous manifest") if previous_manifest_path else None
        delta = len(products) - int(previous.get("recordCount", 0)) if previous else len(products)
        lines = [
            f"# Catalog {catalog_version}", "", f"- Products: {len(products):,} ({delta:+,} vs previous accepted manifest)",
            f"- Ingredient observations: {counts['ingredientObservations']:,}", f"- Missing-ingredient unknown products: {len(products)-counts['ingredientObservations']:,}",
            f"- Basic exclusions: {len(exclusions):,}", f"- SQLite size: {size:,} bytes", f"- SQLite SHA-256: `{digest}`",
            f"- Methodology: `{methodology_version}`", f"- Selection policy: `{selection_policy_version}`", "", "## Status distribution",
        ]
        lines.extend(f"- {status}: {count:,}" for status, count in sorted(status_counts.items()))
        release_notes_path.parent.mkdir(parents=True, exist_ok=True)
        release_notes_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return manifest


def validate_catalog(database_path: Path, manifest_path: Path) -> None:
    manifest = load_json(manifest_path, "manifest")
    if manifest.get("schemaVersion") != SCHEMA_VERSION:
        raise ValueError("manifest schemaVersion is unsupported")
    if manifest.get("sha256") != file_sha256(database_path):
        raise ValueError("manifest/database SHA-256 mismatch")
    uri = f"file:{database_path.resolve()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        if connection.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
            raise ValueError("unexpected SQLite application_id")
        if connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise ValueError("unexpected SQLite user_version")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite foreign_key_check failed")
        counts = manifest["counts"]
        table_counts = {
            "products": "products", "ingredientObservations": "product_observations", "assessments": "product_assessments",
            "assessmentReasons": "assessment_reasons", "certifications": "certification_evidence", "retailerEvidence": "retailer_evidence",
            "remoteImageReferences": "remote_image_references", "basicExclusions": "basic_exclusions",
        }
        for key, table in table_counts.items():
            actual = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            if actual != counts[key]:
                raise ValueError(f"manifest count mismatch for {key}: {actual} != {counts[key]}")
        if connection.execute("SELECT COUNT(*) FROM products WHERE current_assessment_id IS NULL").fetchone()[0]:
            raise ValueError("product without current assessment")
        if connection.execute("SELECT COUNT(*) FROM products p JOIN product_assessments a ON a.id=p.current_assessment_id WHERE p.current_observation_id IS NULL AND a.status <> 'unknown'").fetchone()[0]:
            raise ValueError("missing ingredient evidence has a non-unknown assessment")
        if connection.execute("SELECT COUNT(*) FROM product_assessments a LEFT JOIN certification_evidence c ON c.assessment_id=a.id WHERE a.status='halal-certified' GROUP BY a.id HAVING COUNT(c.id)=0").fetchall():
            raise ValueError("halal-certified assessment lacks certification")
        sample = connection.execute("SELECT gtin FROM products ORDER BY gtin LIMIT 1").fetchone()[0]
        for sql, params, label in (
            ("SELECT * FROM products WHERE gtin=?", (sample,), "GTIN"),
            ("SELECT * FROM assessment_reasons WHERE assessment_id=? ORDER BY position", (1,), "assessment reasons"),
            ("SELECT * FROM retailer_evidence WHERE gtin=? ORDER BY observed_at DESC", (sample,), "retailer evidence"),
        ):
            plan = " ".join(row[3].upper() for row in connection.execute("EXPLAIN QUERY PLAN " + sql, params))
            if "SEARCH" not in plan or ("INDEX" not in plan and "PRIMARY KEY" not in plan):
                raise ValueError(f"{label} query is not indexed: {plan}")
        if connection.execute("SELECT COUNT(*) FROM basic_exclusions b JOIN products p ON p.gtin=b.gtin AND p.market=b.market").fetchone()[0]:
            raise ValueError("basic-exclusion index overlaps detailed products")
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("build")
    build.add_argument("--evidence", type=Path, required=True)
    build.add_argument("--database", type=Path, required=True)
    build.add_argument("--manifest", type=Path, required=True)
    build.add_argument("--source-policy", type=Path, action="append", default=[], required=True)
    build.add_argument("--basic-exclusions", type=Path)
    build.add_argument("--catalog-version", required=True)
    build.add_argument("--selection-policy-version", required=True)
    build.add_argument("--generated-at", required=True)
    build.add_argument("--source-commit", required=True)
    build.add_argument("--workflow-run", default="local")
    build.add_argument("--logical-dump", type=Path)
    build.add_argument("--release-notes", type=Path)
    build.add_argument("--previous-manifest", type=Path)
    build.add_argument("--max-database-bytes", type=int, default=250 * 1024 * 1024)
    validate = sub.add_parser("validate")
    validate.add_argument("--database", type=Path, required=True)
    validate.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "validate":
        validate_catalog(args.database, args.manifest)
        print(f"Validated production catalog {args.database}")
        return
    manifest = build_catalog(
        evidence_path=args.evidence, database_path=args.database, manifest_path=args.manifest,
        policy_paths=args.source_policy, basic_exclusions_path=args.basic_exclusions,
        catalog_version=args.catalog_version, selection_policy_version=args.selection_policy_version,
        generated_at=args.generated_at, source_commit=args.source_commit, workflow_run=args.workflow_run,
        logical_dump_path=args.logical_dump, release_notes_path=args.release_notes,
        previous_manifest_path=args.previous_manifest, max_database_bytes=args.max_database_bytes,
    )
    validate_catalog(args.database, args.manifest)
    print(f"Built production catalog {manifest['catalogVersion']} with {manifest['recordCount']} products; sha256={manifest['sha256']}")


if __name__ == "__main__":
    main()
