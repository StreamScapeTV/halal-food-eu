#!/usr/bin/env python3
"""Build the immutable Halal Food EU SQLite catalog from reviewed local JSON.

This command deliberately performs no network access. Acquisition and licensing
review are separate auditable steps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

APPLICATION_ID = 1_212_564_821  # ASCII "HFEU"
ALLOWED_STATUSES = {
    "halal-certified",
    "halal-reviewed",
    "not-halal",
    "questionable",
    "unknown",
}
ALLOWED_SEVERITIES = {"positive", "informational", "caution", "prohibitive"}

SCHEMA = """
CREATE TABLE catalog_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    source_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    kind TEXT NOT NULL CHECK(length(trim(kind)) > 0),
    reference TEXT NOT NULL CHECK(length(trim(reference)) > 0),
    license TEXT NOT NULL CHECK(length(trim(license)) > 0),
    retrieved_at TEXT NOT NULL
);

CREATE TABLE products (
    gtin TEXT PRIMARY KEY
        CHECK(length(gtin) = 14 AND gtin NOT GLOB '*[^0-9]*'),
    name TEXT NOT NULL CHECK(length(trim(name)) > 0),
    brand TEXT,
    current_observation_id INTEGER,
    FOREIGN KEY(current_observation_id) REFERENCES product_observations(id)
) WITHOUT ROWID;

CREATE TABLE product_observations (
    id INTEGER PRIMARY KEY,
    gtin TEXT NOT NULL,
    ingredients_text TEXT NOT NULL,
    language_code TEXT NOT NULL CHECK(length(trim(language_code)) > 0),
    observed_at TEXT NOT NULL,
    ingredients_hash TEXT NOT NULL CHECK(length(ingredients_hash) = 64),
    source_id INTEGER NOT NULL,
    source_product_id TEXT NOT NULL CHECK(length(trim(source_product_id)) > 0),
    FOREIGN KEY(gtin) REFERENCES products(gtin) ON DELETE RESTRICT,
    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE RESTRICT,
    UNIQUE(gtin, ingredients_hash, source_id, observed_at)
);

CREATE TABLE product_assessments (
    id INTEGER PRIMARY KEY,
    observation_id INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN (
        'halal-certified',
        'halal-reviewed',
        'not-halal',
        'questionable',
        'unknown'
    )),
    summary TEXT NOT NULL CHECK(length(trim(summary)) > 0),
    methodology_version TEXT NOT NULL CHECK(length(trim(methodology_version)) > 0),
    reviewed_at TEXT NOT NULL,
    FOREIGN KEY(observation_id) REFERENCES product_observations(id) ON DELETE CASCADE
);

CREATE TABLE certification_evidence (
    id INTEGER PRIMARY KEY,
    assessment_id INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    source_id INTEGER NOT NULL,
    certifying_body TEXT NOT NULL CHECK(length(trim(certifying_body)) > 0),
    certificate_reference TEXT NOT NULL CHECK(length(trim(certificate_reference)) > 0),
    scope TEXT NOT NULL CHECK(length(trim(scope)) > 0),
    valid_from TEXT,
    valid_until TEXT,
    FOREIGN KEY(assessment_id) REFERENCES product_assessments(id) ON DELETE CASCADE,
    FOREIGN KEY(source_id) REFERENCES sources(id) ON DELETE RESTRICT,
    UNIQUE(assessment_id, position)
);

CREATE TABLE assessment_reasons (
    id INTEGER PRIMARY KEY,
    assessment_id INTEGER NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 0),
    code TEXT NOT NULL CHECK(length(trim(code)) > 0),
    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
    detail TEXT NOT NULL CHECK(length(trim(detail)) > 0),
    ingredient TEXT,
    severity TEXT NOT NULL CHECK(severity IN (
        'positive', 'informational', 'caution', 'prohibitive'
    )),
    FOREIGN KEY(assessment_id) REFERENCES product_assessments(id) ON DELETE CASCADE,
    UNIQUE(assessment_id, position)
);

CREATE INDEX idx_product_observations_gtin_observed
    ON product_observations(gtin, observed_at DESC);
CREATE INDEX idx_product_assessments_status
    ON product_assessments(status);
CREATE INDEX idx_certification_evidence_order
    ON certification_evidence(assessment_id, position);
CREATE INDEX idx_assessment_reasons_order
    ON assessment_reasons(assessment_id, position);
"""


def _digits(value: str) -> str:
    result = "".join(
        character for character in value if not character.isspace() and character != "-"
    )
    if not result or not result.isascii() or not result.isdigit():
        raise ValueError(f"barcode contains unsupported characters: {value!r}")
    return result


def has_valid_gtin_check_digit(value: str) -> bool:
    if len(value) not in {8, 12, 13, 14}:
        return False
    expected = int(value[-1])
    total = 0
    for offset, character in enumerate(reversed(value[:-1])):
        total += int(character) * (3 if offset % 2 == 0 else 1)
    return (10 - total % 10) % 10 == expected


def normalize_gtin(value: str) -> str:
    digits = _digits(value)
    if not has_valid_gtin_check_digit(digits):
        raise ValueError(f"invalid GTIN/check digit: {value}")
    return digits.zfill(14)


def load_source(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("catalog source must be a JSON object")
    return data


def _nonempty(value: Any, field: str, gtin: str) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"{field} is empty for {gtin}")
    return text


def build_catalog(source_path: Path, database_path: Path, manifest_path: Path) -> None:
    data = load_source(source_path)
    catalog = data["catalog"]
    schema_version = int(catalog["schemaVersion"])
    if schema_version != 1:
        raise ValueError(f"builder supports schemaVersion 1, got {schema_version}")

    sources = sorted(data["sources"], key=lambda item: item["key"])
    products = sorted(data["products"], key=lambda item: normalize_gtin(item["barcode"]))

    source_keys = {source["key"] for source in sources}
    if len(source_keys) != len(sources):
        raise ValueError("duplicate source key")

    database_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix="catalog-", suffix=".sqlite3", dir=database_path.parent
    )
    os.close(file_descriptor)
    temporary_path = Path(temporary_name)

    try:
        connection = sqlite3.connect(temporary_path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = OFF")
        connection.execute("PRAGMA synchronous = OFF")
        connection.execute("PRAGMA page_size = 4096")
        connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
        connection.execute(f"PRAGMA user_version = {schema_version}")
        connection.executescript(SCHEMA)

        metadata = {
            "catalogVersion": str(catalog["catalogVersion"]),
            "schemaVersion": str(schema_version),
            "methodologyVersion": str(catalog["methodologyVersion"]),
            "generatedAt": str(catalog["generatedAt"]),
            "dataLicense": str(catalog["dataLicense"]),
            "attribution": str(catalog["attribution"]),
        }
        connection.executemany(
            "INSERT INTO catalog_metadata(key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )

        source_ids: dict[str, int] = {}
        for source in sources:
            cursor = connection.execute(
                """
                INSERT INTO sources(
                    source_key, name, kind, reference, license, retrieved_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    source["key"],
                    source["name"],
                    source["kind"],
                    source["reference"],
                    source["license"],
                    source["retrievedAt"],
                ),
            )
            source_ids[source["key"]] = int(cursor.lastrowid)

        seen_gtins: set[str] = set()
        for product in products:
            gtin = normalize_gtin(product["barcode"])
            if gtin in seen_gtins:
                raise ValueError(f"duplicate normalized GTIN: {gtin}")
            seen_gtins.add(gtin)

            source_key = product["sourceKey"]
            if source_key not in source_keys:
                raise ValueError(f"unknown source key {source_key!r} for {gtin}")

            assessment = product["assessment"]
            status = assessment["status"]
            if status not in ALLOWED_STATUSES:
                raise ValueError(f"unsupported status {status!r} for {gtin}")

            reasons = assessment.get("reasons", [])
            if not reasons:
                raise ValueError(f"assessment has no reasons for {gtin}")

            certifications = assessment.get("certifications", [])
            if status == "halal-certified" and not certifications:
                raise ValueError(f"halal-certified assessment has no certificate for {gtin}")

            ingredients = product["ingredients"]
            raw_ingredient_text = ingredients.get("text")
            ingredient_text = "" if raw_ingredient_text is None else str(raw_ingredient_text).strip()
            if not ingredient_text and status != "unknown":
                raise ValueError(f"empty ingredient text is only allowed for unknown status: {gtin}")
            if not ingredient_text and ingredients.get("languageCode") != "und":
                raise ValueError(f"missing ingredient text must use languageCode 'und': {gtin}")
            ingredient_hash = hashlib.sha256(ingredient_text.encode("utf-8")).hexdigest()

            connection.execute(
                "INSERT INTO products(gtin, name, brand) VALUES (?, ?, ?)",
                (gtin, product["name"], product.get("brand")),
            )
            observation_cursor = connection.execute(
                """
                INSERT INTO product_observations(
                    gtin, ingredients_text, language_code, observed_at,
                    ingredients_hash, source_id, source_product_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gtin,
                    ingredient_text,
                    ingredients["languageCode"],
                    ingredients["observedAt"],
                    ingredient_hash,
                    source_ids[source_key],
                    product["sourceProductId"],
                ),
            )
            observation_id = int(observation_cursor.lastrowid)

            assessment_cursor = connection.execute(
                """
                INSERT INTO product_assessments(
                    observation_id, status, summary, methodology_version, reviewed_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    observation_id,
                    status,
                    assessment["summary"],
                    catalog["methodologyVersion"],
                    assessment["reviewedAt"],
                ),
            )
            assessment_id = int(assessment_cursor.lastrowid)

            for position, certification in enumerate(certifications):
                certification_source_key = certification["sourceKey"]
                if certification_source_key not in source_ids:
                    raise ValueError(
                        f"unknown certification source {certification_source_key!r} for {gtin}"
                    )
                connection.execute(
                    """
                    INSERT INTO certification_evidence(
                        assessment_id, position, source_id, certifying_body,
                        certificate_reference, scope, valid_from, valid_until
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        assessment_id,
                        position,
                        source_ids[certification_source_key],
                        _nonempty(certification["certifyingBody"], "certifyingBody", gtin),
                        _nonempty(
                            certification["certificateReference"],
                            "certificateReference",
                            gtin,
                        ),
                        _nonempty(certification["scope"], "certification.scope", gtin),
                        certification.get("validFrom"),
                        certification.get("validUntil"),
                    ),
                )

            for position, reason in enumerate(reasons):
                severity = reason["severity"]
                if severity not in ALLOWED_SEVERITIES:
                    raise ValueError(f"unsupported severity {severity!r} for {gtin}")
                connection.execute(
                    """
                    INSERT INTO assessment_reasons(
                        assessment_id, position, code, title, detail, ingredient, severity
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        assessment_id,
                        position,
                        reason["code"],
                        reason["title"],
                        reason["detail"],
                        reason.get("ingredient"),
                        severity,
                    ),
                )

            connection.execute(
                "UPDATE products SET current_observation_id = ? WHERE gtin = ?",
                (observation_id, gtin),
            )

        connection.commit()
        connection.execute("ANALYZE")
        connection.execute("VACUUM")
        connection.close()

        os.replace(temporary_path, database_path)
        digest = hashlib.sha256(database_path.read_bytes()).hexdigest()

        manifest = {
            "catalogVersion": catalog["catalogVersion"],
            "schemaVersion": schema_version,
            "methodologyVersion": catalog["methodologyVersion"],
            "generatedAt": catalog["generatedAt"],
            "recordCount": len(products),
            "sha256": digest,
            "dataLicense": catalog["dataLicense"],
            "attribution": catalog["attribution"],
            "sources": [
                {
                    "name": source["name"],
                    "kind": source["kind"],
                    "reference": source["reference"],
                    "license": source["license"],
                    "retrievedAt": source["retrievedAt"],
                }
                for source in sources
            ],
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    finally:
        temporary_path.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    build_catalog(arguments.input, arguments.database, arguments.manifest)
    print(f"Built {arguments.database} and {arguments.manifest}")


if __name__ == "__main__":
    main()
