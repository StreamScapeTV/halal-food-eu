#!/usr/bin/env python3
"""Bind and verify a lineage-independent logical identity for a production catalog.

The physical SQLite bytes intentionally carry build lineage such as catalog version,
generation timestamp, source commit, and quality-evaluation metadata. Those fields
must not manufacture a catalog update when the runtime catalog is otherwise
unchanged. This module projects the runtime/evidence semantics through stable
natural identities (never SQLite surrogate row IDs), canonicalizes them, and hashes
the result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable

LOGICAL_SCHEMA_VERSION = 1
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SEMANTIC_METADATA_KEYS = (
    "schemaVersion",
    "methodologyVersion",
    "selectionPolicyVersion",
    "evidenceSchemaVersion",
)

# Every query deliberately resolves integer SQLite foreign keys to stable semantic
# identities. This makes physical insertion order irrelevant while retaining all
# fields that can affect runtime lookup, evidence interpretation, freshness,
# certification, retailer support, source rights/policies, remote image references,
# and the basic-product exclusion set.
SEMANTIC_QUERIES: tuple[tuple[str, str], ...] = (
    (
        "sources",
        """
        SELECT source_key, operator, source_class, reference, license, attribution,
               retrieved_at, source_snapshot_id, policy_schema_version, policy_sha256
        FROM sources
        """,
    ),
    (
        "products",
        """
        SELECT p.gtin, p.market, p.selection_id, p.identity_evidence_id, p.name,
               p.brand, p.brand_owner, p.quantity, identity_source.source_key,
               p.identity_source_record_id, current_observation.evidence_id,
               current_assessment.evidence_id, p.conflict_flags_json
        FROM products p
        JOIN sources identity_source ON identity_source.id = p.identity_source_id
        LEFT JOIN product_observations current_observation
               ON current_observation.id = p.current_observation_id
        LEFT JOIN product_assessments current_assessment
               ON current_assessment.id = p.current_assessment_id
        """,
    ),
    (
        "product_observations",
        """
        SELECT o.evidence_id, o.gtin, s.source_key, o.source_record_id,
               o.ingredients_text, o.language_code, o.allergens_text, o.traces_text,
               o.observed_at, o.retrieved_at, o.ingredients_hash,
               o.verification_state, o.freshness_state
        FROM product_observations o
        JOIN sources s ON s.id = o.source_id
        """,
    ),
    (
        "product_assessments",
        """
        SELECT a.evidence_id, a.gtin, o.evidence_id, a.status, a.summary,
               a.methodology_version, a.assessed_at, a.reviewed_at,
               a.approved_reviewer_count, a.recheck_at
        FROM product_assessments a
        LEFT JOIN product_observations o ON o.id = a.observation_id
        """,
    ),
    (
        "assessment_reasons",
        """
        SELECT a.evidence_id, r.position, r.code, r.title, r.detail, r.ingredient,
               r.severity, r.evidence_ids_json
        FROM assessment_reasons r
        JOIN product_assessments a ON a.id = r.assessment_id
        """,
    ),
    (
        "certification_evidence",
        """
        SELECT c.evidence_id, a.evidence_id, c.position, s.source_key,
               c.certifying_body, c.scheme, c.certificate_reference, c.scope,
               c.valid_from, c.valid_until, c.last_checked_at
        FROM certification_evidence c
        JOIN product_assessments a ON a.id = c.assessment_id
        JOIN sources s ON s.id = c.source_id
        """,
    ),
    (
        "retailer_evidence",
        """
        SELECT r.evidence_id, r.gtin, r.position, s.source_key, r.kind,
               r.retailer_key, r.observed_at, r.snapshot_at, r.scope,
               r.location_id, r.limitations
        FROM retailer_evidence r
        JOIN sources s ON s.id = r.source_id
        """,
    ),
    (
        "remote_image_references",
        """
        SELECT i.evidence_id, i.gtin, i.position, s.source_key, i.purpose,
               i.url, i.image_id, i.revision
        FROM remote_image_references i
        JOIN sources s ON s.id = i.source_id
        """,
    ),
    (
        "basic_exclusions",
        """
        SELECT gtin, market, selection_policy_version, reason
        FROM basic_exclusions
        """,
    ),
)
SEMANTIC_TABLES = tuple(name for name, _ in SEMANTIC_QUERIES)


class LogicalCatalogError(ValueError):
    """Raised when a catalog cannot produce a trustworthy logical identity."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LogicalCatalogError(f"failed to read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise LogicalCatalogError(f"{label} must be a JSON object")
    return value


def _normalize_cell(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float)):
        return value
    if isinstance(value, bytes):
        return {"blobSha256": hashlib.sha256(value).hexdigest(), "byteCount": len(value)}
    raise LogicalCatalogError(f"unsupported SQLite value type {type(value).__name__}")


def _query_projection(connection: sqlite3.Connection, name: str, query: str) -> list[list[Any]]:
    try:
        rows = [
            [_normalize_cell(value) for value in row]
            for row in connection.execute(query).fetchall()
        ]
    except sqlite3.Error as exc:
        raise LogicalCatalogError(f"failed to project logical table {name!r}: {exc}") from exc
    rows.sort(key=canonical_json)
    return rows


def logical_projection(connection: sqlite3.Connection) -> dict[str, Any]:
    try:
        metadata_rows = connection.execute(
            "SELECT key,value FROM catalog_metadata WHERE key IN (?,?,?,?) ORDER BY key",
            SEMANTIC_METADATA_KEYS,
        ).fetchall()
    except sqlite3.Error as exc:
        raise LogicalCatalogError(f"failed to read catalog semantic metadata: {exc}") from exc
    metadata = {key: value for key, value in metadata_rows}
    missing = [key for key in SEMANTIC_METADATA_KEYS if key not in metadata]
    if missing:
        raise LogicalCatalogError(
            "catalog semantic metadata is incomplete: " + ", ".join(missing)
        )
    return {
        "logicalSchemaVersion": LOGICAL_SCHEMA_VERSION,
        "sqliteUserVersion": connection.execute("PRAGMA user_version").fetchone()[0],
        "metadata": metadata,
        "tables": {
            name: _query_projection(connection, name, query)
            for name, query in SEMANTIC_QUERIES
        },
    }


def compute_identity(database_path: Path) -> dict[str, Any]:
    if not database_path.is_file():
        raise LogicalCatalogError("production catalog database is missing")
    uri = f"file:{database_path.resolve()}?mode=ro&immutable=1"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise LogicalCatalogError(f"failed to open production catalog read-only: {exc}") from exc
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise LogicalCatalogError("production catalog integrity_check failed")
        projection = logical_projection(connection)
    except sqlite3.Error as exc:
        raise LogicalCatalogError(f"failed to project production catalog semantics: {exc}") from exc
    finally:
        connection.close()
    digest = hashlib.sha256(canonical_json(projection).encode("utf-8")).hexdigest()
    return {"schemaVersion": LOGICAL_SCHEMA_VERSION, "sha256": digest}


def _validate_manifest_database_binding(manifest: dict[str, Any], database_path: Path) -> None:
    physical = manifest.get("sha256")
    if not isinstance(physical, str) or not SHA256_RE.fullmatch(physical):
        raise LogicalCatalogError("production manifest database SHA-256 is invalid")
    if physical != file_sha256(database_path):
        raise LogicalCatalogError("production manifest/database SHA-256 mismatch")


def bind_manifest(
    *,
    database_path: Path,
    manifest_path: Path,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    manifest = _load_object(manifest_path, "production catalog manifest")
    _validate_manifest_database_binding(manifest, database_path)
    identity = compute_identity(database_path)
    if expected_sha256 is not None:
        if not SHA256_RE.fullmatch(expected_sha256):
            raise LogicalCatalogError("expected logical catalog SHA-256 is invalid")
        if identity["sha256"] != expected_sha256:
            raise LogicalCatalogError(
                "rematerialized logical catalog differs from the reviewed proposal"
            )
    manifest["logicalCatalog"] = identity
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return identity


def validate_manifest(*, database_path: Path, manifest_path: Path) -> dict[str, Any]:
    manifest = _load_object(manifest_path, "production catalog manifest")
    _validate_manifest_database_binding(manifest, database_path)
    bound = manifest.get("logicalCatalog")
    if not isinstance(bound, dict) or set(bound) != {"schemaVersion", "sha256"}:
        raise LogicalCatalogError("production manifest logical-catalog identity is missing")
    if bound.get("schemaVersion") != LOGICAL_SCHEMA_VERSION:
        raise LogicalCatalogError("production manifest logical-catalog schema is unsupported")
    digest = bound.get("sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise LogicalCatalogError("production manifest logical-catalog SHA-256 is invalid")
    actual = compute_identity(database_path)
    if actual != bound:
        raise LogicalCatalogError("production manifest logical-catalog identity mismatch")
    return actual


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("compute", "bind", "validate"):
        child = sub.add_parser(command)
        child.add_argument("--database", type=Path, required=True)
        if command != "compute":
            child.add_argument("--manifest", type=Path, required=True)
        if command == "bind":
            child.add_argument("--expected-sha256")
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "compute":
            identity = compute_identity(args.database)
        elif args.command == "bind":
            identity = bind_manifest(
                database_path=args.database,
                manifest_path=args.manifest,
                expected_sha256=args.expected_sha256,
            )
        else:
            identity = validate_manifest(
                database_path=args.database,
                manifest_path=args.manifest,
            )
    except LogicalCatalogError as exc:
        raise SystemExit(f"logical catalog validation failed: {exc}") from exc
    print(json.dumps(identity, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
