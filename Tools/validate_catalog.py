#!/usr/bin/env python3
"""Validate catalog integrity, provenance, index use, and reviewed source parity."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from catalog_builder import APPLICATION_ID, ALLOWED_SEVERITIES, ALLOWED_STATUSES, load_source, normalize_gtin


def parse_utc(value: str, field: str) -> None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} is not ISO-8601: {value}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone: {value}")


def validate(database_path: Path, manifest_path: Path, source_path: Path) -> None:
    manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    source = load_source(source_path)

    digest = hashlib.sha256(database_path.read_bytes()).hexdigest()
    if manifest["sha256"] != digest:
        raise ValueError(f"manifest SHA-256 mismatch: expected {manifest['sha256']}, got {digest}")

    uri = f"file:{database_path.resolve()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row

    try:
        application_id = connection.execute("PRAGMA application_id").fetchone()[0]
        if application_id != APPLICATION_ID:
            raise ValueError(f"unexpected application_id {application_id}")

        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        if schema_version != manifest["schemaVersion"]:
            raise ValueError("manifest and SQLite schema versions differ")

        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise ValueError(f"SQLite integrity_check failed: {integrity}")

        foreign_key_failures = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_failures:
            raise ValueError(f"foreign-key violations: {foreign_key_failures}")

        metadata = dict(connection.execute("SELECT key, value FROM catalog_metadata"))
        expected_catalog = source["catalog"]
        for key in (
            "catalogVersion",
            "methodologyVersion",
            "generatedAt",
            "dataLicense",
            "attribution",
        ):
            if metadata.get(key) != str(expected_catalog[key]):
                raise ValueError(f"metadata mismatch for {key}")

        products = connection.execute(
            """
            SELECT p.gtin, p.name, p.brand, p.current_observation_id,
                   o.ingredients_text, o.language_code, o.observed_at,
                   o.ingredients_hash, s.name AS source_name, s.kind,
                   s.reference, s.license, s.retrieved_at,
                   a.status, a.summary, a.methodology_version, a.reviewed_at,
                   a.id AS assessment_id
            FROM products AS p
            JOIN product_observations AS o ON o.id = p.current_observation_id
            JOIN sources AS s ON s.id = o.source_id
            JOIN product_assessments AS a ON a.observation_id = o.id
            ORDER BY p.gtin
            """
        ).fetchall()

        if len(products) != manifest["recordCount"]:
            raise ValueError("manifest recordCount does not match SQLite")
        if len(products) != len(source["products"]):
            raise ValueError("source product count does not match SQLite")

        source_by_gtin = {
            normalize_gtin(product["barcode"]): product for product in source["products"]
        }
        for row in products:
            gtin = row["gtin"]
            if gtin not in source_by_gtin:
                raise ValueError(f"database contains unreviewed GTIN {gtin}")
            expected = source_by_gtin[gtin]
            if row["name"] != expected["name"]:
                raise ValueError(f"name mismatch for {gtin}")
            if row["ingredients_text"] != expected["ingredients"]["text"].strip():
                raise ValueError(f"ingredient text mismatch for {gtin}")
            if row["status"] != expected["assessment"]["status"]:
                raise ValueError(f"assessment status mismatch for {gtin}")
            if row["status"] not in ALLOWED_STATUSES:
                raise ValueError(f"unsupported status for {gtin}")
            if row["methodology_version"] != manifest["methodologyVersion"]:
                raise ValueError(f"methodology mismatch for {gtin}")
            if not row["license"] or not row["reference"] or not row["source_name"]:
                raise ValueError(f"missing provenance/license for {gtin}")

            parse_utc(row["observed_at"], f"{gtin}.observed_at")
            parse_utc(row["retrieved_at"], f"{gtin}.retrieved_at")
            parse_utc(row["reviewed_at"], f"{gtin}.reviewed_at")

            expected_hash = hashlib.sha256(row["ingredients_text"].encode("utf-8")).hexdigest()
            if row["ingredients_hash"] != expected_hash:
                raise ValueError(f"ingredient hash mismatch for {gtin}")

            reasons = connection.execute(
                """
                SELECT position, code, title, detail, ingredient, severity
                FROM assessment_reasons
                WHERE assessment_id = ?
                ORDER BY position, id
                """,
                (row["assessment_id"],),
            ).fetchall()
            if not reasons:
                raise ValueError(f"assessment has no reasons for {gtin}")
            if [reason["position"] for reason in reasons] != list(range(len(reasons))):
                raise ValueError(f"reason positions are not contiguous for {gtin}")
            for reason in reasons:
                if reason["severity"] not in ALLOWED_SEVERITIES:
                    raise ValueError(f"invalid reason severity for {gtin}")
                if not reason["code"] or not reason["title"] or not reason["detail"]:
                    raise ValueError(f"empty structured reason for {gtin}")

        plans = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM products AS p WHERE p.gtin = ?",
            (products[0]["gtin"],),
        ).fetchall()
        plan_text = " ".join(str(row[3]).upper() for row in plans)
        if "SEARCH" not in plan_text or ("PRIMARY KEY" not in plan_text and "INDEX" not in plan_text):
            raise ValueError(f"exact GTIN lookup is not indexed: {plan_text}")

        dangling = connection.execute(
            "SELECT COUNT(*) FROM products WHERE current_observation_id IS NULL"
        ).fetchone()[0]
        if dangling:
            raise ValueError(f"{dangling} products have no current observation")

        parse_utc(manifest["generatedAt"], "manifest.generatedAt")
        for index, source_entry in enumerate(manifest["sources"]):
            for field in ("name", "kind", "reference", "license", "retrievedAt"):
                if not source_entry.get(field):
                    raise ValueError(f"manifest.sources[{index}].{field} is required")
            parse_utc(source_entry["retrievedAt"], f"manifest.sources[{index}].retrievedAt")

        print(
            f"Validated catalog {manifest['catalogVersion']}: "
            f"{len(products)} records, schema {schema_version}, indexed GTIN lookup, digest {digest}"
        )
    finally:
        connection.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    try:
        validate(arguments.database, arguments.manifest, arguments.source)
    except (OSError, ValueError, KeyError, sqlite3.DatabaseError) as error:
        print(f"catalog validation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
