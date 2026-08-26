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

from catalog_builder import (
    APPLICATION_ID,
    ALLOWED_SEVERITIES,
    ALLOWED_STATUSES,
    load_source,
    normalize_gtin,
)


def parse_utc(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{field} is not ISO-8601: {value}") from error
    if parsed.tzinfo is None:
        raise ValueError(f"{field} must include a timezone: {value}")
    return parsed


def _expected_ingredient_text(product: dict[str, Any]) -> str:
    value = product["ingredients"].get("text")
    return "" if value is None else str(value).strip()


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
                   o.ingredients_hash, s.source_key, s.name AS source_name, s.kind,
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
        if not products:
            raise ValueError("catalog contains no products")

        source_by_gtin = {
            normalize_gtin(product["barcode"]): product for product in source["products"]
        }
        if len(source_by_gtin) != len(source["products"]):
            raise ValueError("source contains duplicate normalized GTINs")

        for row in products:
            gtin = row["gtin"]
            if gtin not in source_by_gtin:
                raise ValueError(f"database contains unreviewed GTIN {gtin}")
            expected = source_by_gtin[gtin]
            expected_assessment = expected["assessment"]
            expected_ingredients = expected["ingredients"]
            expected_text = _expected_ingredient_text(expected)

            if row["name"] != expected["name"]:
                raise ValueError(f"name mismatch for {gtin}")
            if row["brand"] != expected.get("brand"):
                raise ValueError(f"brand mismatch for {gtin}")
            if row["ingredients_text"] != expected_text:
                raise ValueError(f"ingredient text mismatch for {gtin}")
            if row["language_code"] != expected_ingredients["languageCode"]:
                raise ValueError(f"ingredient language mismatch for {gtin}")
            if row["observed_at"] != expected_ingredients["observedAt"]:
                raise ValueError(f"ingredient observation date mismatch for {gtin}")
            if row["source_key"] != expected["sourceKey"]:
                raise ValueError(f"ingredient source mismatch for {gtin}")
            if row["status"] != expected_assessment["status"]:
                raise ValueError(f"assessment status mismatch for {gtin}")
            if row["summary"] != expected_assessment["summary"]:
                raise ValueError(f"assessment summary mismatch for {gtin}")
            if row["reviewed_at"] != expected_assessment["reviewedAt"]:
                raise ValueError(f"assessment review date mismatch for {gtin}")
            if row["status"] not in ALLOWED_STATUSES:
                raise ValueError(f"unsupported status for {gtin}")
            if row["methodology_version"] != manifest["methodologyVersion"]:
                raise ValueError(f"methodology mismatch for {gtin}")
            if not row["license"] or not row["reference"] or not row["source_name"]:
                raise ValueError(f"missing provenance/license for {gtin}")

            if not expected_text:
                if row["status"] != "unknown":
                    raise ValueError(f"missing ingredients require unknown status for {gtin}")
                if row["language_code"] != "und":
                    raise ValueError(f"missing ingredients require language code 'und' for {gtin}")

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
            expected_reasons = expected_assessment.get("reasons", [])
            if not reasons:
                raise ValueError(f"assessment has no reasons for {gtin}")
            if len(reasons) != len(expected_reasons):
                raise ValueError(f"reason count mismatch for {gtin}")
            if [reason["position"] for reason in reasons] != list(range(len(reasons))):
                raise ValueError(f"reason positions are not contiguous for {gtin}")
            for position, reason in enumerate(reasons):
                expected_reason = expected_reasons[position]
                if reason["severity"] not in ALLOWED_SEVERITIES:
                    raise ValueError(f"invalid reason severity for {gtin}")
                if not reason["code"] or not reason["title"] or not reason["detail"]:
                    raise ValueError(f"empty structured reason for {gtin}")
                for database_field, source_field in (
                    ("code", "code"),
                    ("title", "title"),
                    ("detail", "detail"),
                    ("ingredient", "ingredient"),
                    ("severity", "severity"),
                ):
                    if reason[database_field] != expected_reason.get(source_field):
                        raise ValueError(
                            f"reason {position} field {database_field} mismatch for {gtin}"
                        )

            certifications = connection.execute(
                """
                SELECT c.position, c.certifying_body, c.certificate_reference,
                       c.scope, c.valid_from, c.valid_until,
                       s.source_key, s.name AS source_name, s.reference,
                       s.license, s.retrieved_at
                FROM certification_evidence AS c
                JOIN sources AS s ON s.id = c.source_id
                WHERE c.assessment_id = ?
                ORDER BY c.position, c.id
                """,
                (row["assessment_id"],),
            ).fetchall()
            expected_certifications = expected_assessment.get("certifications", [])
            if len(certifications) != len(expected_certifications):
                raise ValueError(f"certification evidence count mismatch for {gtin}")
            if row["status"] == "halal-certified" and not certifications:
                raise ValueError(f"halal-certified assessment lacks evidence for {gtin}")
            if [item["position"] for item in certifications] != list(
                range(len(certifications))
            ):
                raise ValueError(f"certification positions are not contiguous for {gtin}")

            for position, certification in enumerate(certifications):
                expected_certification = expected_certifications[position]
                expected_values = {
                    "certifying_body": expected_certification["certifyingBody"],
                    "certificate_reference": expected_certification["certificateReference"],
                    "scope": expected_certification["scope"],
                    "valid_from": expected_certification.get("validFrom"),
                    "valid_until": expected_certification.get("validUntil"),
                    "source_key": expected_certification["sourceKey"],
                }
                for field, expected_value in expected_values.items():
                    if certification[field] != expected_value:
                        raise ValueError(
                            f"certification {position} field {field} mismatch for {gtin}"
                        )
                if not certification["source_name"] or not certification["reference"]:
                    raise ValueError(f"certification source is incomplete for {gtin}")
                if not certification["license"]:
                    raise ValueError(f"certification source license is missing for {gtin}")
                parse_utc(
                    certification["retrieved_at"],
                    f"{gtin}.certification[{position}].retrieved_at",
                )
                valid_from = (
                    parse_utc(
                        certification["valid_from"],
                        f"{gtin}.certification[{position}].valid_from",
                    )
                    if certification["valid_from"]
                    else None
                )
                valid_until = (
                    parse_utc(
                        certification["valid_until"],
                        f"{gtin}.certification[{position}].valid_until",
                    )
                    if certification["valid_until"]
                    else None
                )
                if valid_from and valid_until and valid_until < valid_from:
                    raise ValueError(f"certificate validity interval is inverted for {gtin}")

        plans = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM products AS p WHERE p.gtin = ?",
            (products[0]["gtin"],),
        ).fetchall()
        plan_text = " ".join(str(row[3]).upper() for row in plans)
        if "SEARCH" not in plan_text or (
            "PRIMARY KEY" not in plan_text and "INDEX" not in plan_text
        ):
            raise ValueError(f"exact GTIN lookup is not indexed: {plan_text}")

        certification_plans = connection.execute(
            """
            EXPLAIN QUERY PLAN
            SELECT * FROM certification_evidence WHERE assessment_id = ? ORDER BY position
            """,
            (products[0]["assessment_id"],),
        ).fetchall()
        certification_plan_text = " ".join(
            str(plan[3]).upper() for plan in certification_plans
        )
        if "INDEX" not in certification_plan_text:
            raise ValueError(
                "certification evidence lookup is not indexed: "
                f"{certification_plan_text}"
            )

        dangling = connection.execute(
            "SELECT COUNT(*) FROM products WHERE current_observation_id IS NULL"
        ).fetchone()[0]
        if dangling:
            raise ValueError(f"{dangling} products have no current observation")

        parse_utc(manifest["generatedAt"], "manifest.generatedAt")
        if len(manifest["sources"]) != len(source["sources"]):
            raise ValueError("manifest source count does not match reviewed input")
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
    except (OSError, ValueError, KeyError, TypeError, sqlite3.DatabaseError) as error:
        print(f"catalog validation failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
