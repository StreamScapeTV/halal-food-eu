#!/usr/bin/env python3
"""Install and validate bounded offline-search indexes in a production catalog.

The search structures are auxiliary to production SQLite schema v2: they do not
change product/evidence semantics, foreign keys, or application/user versions.
They are separately versioned and digest-bound through the production manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Iterable

SEARCH_INDEX_SCHEMA_VERSION = 1
MAX_PAGE_SIZE = 50
FTS_TABLE = "product_search"
BARCODE_ALIAS_TABLE = "product_barcode_aliases"
TOKENIZER = "unicode61 remove_diacritics 2"
PREFIX_INDEXES = [2, 3, 4]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"failed to read production manifest: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("production manifest must be a JSON object")
    return value


def search_manifest() -> dict:
    return {
        "schemaVersion": SEARCH_INDEX_SCHEMA_VERSION,
        "engine": "sqlite-fts5",
        "ftsTable": FTS_TABLE,
        "barcodeAliasTable": BARCODE_ALIAS_TABLE,
        "tokenizer": TOKENIZER,
        "prefixIndexes": PREFIX_INDEXES,
        "maxPageSize": MAX_PAGE_SIZE,
    }


def barcode_aliases(gtin: str) -> list[str]:
    if len(gtin) != 14 or not gtin.isascii() or not gtin.isdigit():
        raise ValueError(f"invalid canonical GTIN-14 {gtin!r}")
    aliases = {gtin}
    for display_length in (13, 12, 8):
        padding = 14 - display_length
        if gtin.startswith("0" * padding):
            aliases.add(gtin[padding:])
    return sorted(aliases, key=lambda value: (len(value), value))


def _update_release_notes(path: Path, *, database_bytes: int, sha256: str) -> None:
    if not path.is_file():
        raise ValueError("release-notes output is missing while installing search index")
    lines = path.read_text(encoding="utf-8").splitlines()
    size_seen = False
    sha_seen = False
    for index, line in enumerate(lines):
        if line.startswith("- SQLite size: "):
            lines[index] = f"- SQLite size: {database_bytes:,} bytes"
            size_seen = True
        elif line.startswith("- SQLite SHA-256: "):
            lines[index] = f"- SQLite SHA-256: `{sha256}`"
            sha_seen = True
    if not size_seen or not sha_seen:
        raise ValueError("release notes do not expose the production SQLite size/SHA fields")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def install_search_index(
    *,
    database_path: Path,
    manifest_path: Path,
    release_notes_path: Path | None = None,
) -> dict:
    if not database_path.is_file():
        raise ValueError("production catalog database is missing")
    manifest = _load_manifest(manifest_path)
    current_digest = file_sha256(database_path)
    if manifest.get("sha256") != current_digest:
        raise ValueError("production manifest/database SHA-256 mismatch before search indexing")
    if "searchIndex" in manifest:
        validate_search_index(database_path=database_path, manifest_path=manifest_path)
        return manifest

    connection = sqlite3.connect(database_path)
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=OFF")
        connection.execute("PRAGMA synchronous=OFF")
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("production SQLite integrity_check failed before search indexing")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("production SQLite foreign_key_check failed before search indexing")
        try:
            connection.execute(
                f"""
                CREATE VIRTUAL TABLE {FTS_TABLE} USING fts5(
                    gtin UNINDEXED,
                    name,
                    brand,
                    tokenize='{TOKENIZER}',
                    prefix='2 3 4'
                )
                """
            )
        except sqlite3.Error as exc:
            raise ValueError(f"SQLite FTS5 is unavailable for production product search: {exc}") from exc
        connection.execute(
            f"""
            CREATE TABLE {BARCODE_ALIAS_TABLE} (
                alias TEXT NOT NULL CHECK(length(alias) BETWEEN 1 AND 14 AND alias NOT GLOB '*[^0-9]*'),
                gtin TEXT NOT NULL,
                PRIMARY KEY(alias, gtin),
                FOREIGN KEY(gtin) REFERENCES products(gtin) ON DELETE CASCADE
            ) WITHOUT ROWID
            """
        )
        rows = connection.execute(
            "SELECT gtin, name, COALESCE(brand, '') FROM products ORDER BY gtin"
        ).fetchall()
        connection.executemany(
            f"INSERT INTO {FTS_TABLE}(gtin,name,brand) VALUES (?,?,?)",
            rows,
        )
        aliases: list[tuple[str, str]] = []
        for gtin, _, _ in rows:
            aliases.extend((alias, gtin) for alias in barcode_aliases(gtin))
        connection.executemany(
            f"INSERT INTO {BARCODE_ALIAS_TABLE}(alias,gtin) VALUES (?,?)",
            sorted(aliases),
        )
        connection.execute(f"INSERT INTO {FTS_TABLE}({FTS_TABLE}) VALUES ('optimize')")
        connection.commit()
        connection.execute("ANALYZE")
        connection.execute("VACUUM")
    finally:
        connection.close()

    digest = file_sha256(database_path)
    database_bytes = database_path.stat().st_size
    manifest["databaseBytes"] = database_bytes
    manifest["sha256"] = digest
    manifest["searchIndex"] = search_manifest()
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    if release_notes_path is not None:
        _update_release_notes(release_notes_path, database_bytes=database_bytes, sha256=digest)
    validate_search_index(database_path=database_path, manifest_path=manifest_path)
    return manifest


def validate_search_index(*, database_path: Path, manifest_path: Path) -> None:
    manifest = _load_manifest(manifest_path)
    if manifest.get("sha256") != file_sha256(database_path):
        raise ValueError("production manifest/database SHA-256 mismatch")
    if manifest.get("databaseBytes") != database_path.stat().st_size:
        raise ValueError("production manifest databaseBytes differs from SQLite")
    if manifest.get("searchIndex") != search_manifest():
        raise ValueError("production manifest search-index binding is missing or unsupported")

    uri = f"file:{database_path.resolve()}?mode=ro&immutable=1"
    connection = sqlite3.connect(uri, uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("production SQLite integrity_check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("production SQLite foreign_key_check failed")
        product_count = connection.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        fts_count = connection.execute(f"SELECT COUNT(*) FROM {FTS_TABLE}").fetchone()[0]
        if fts_count != product_count:
            raise ValueError(f"search index product count mismatch: {fts_count} != {product_count}")
        canonical_alias_count = connection.execute(
            f"SELECT COUNT(*) FROM {BARCODE_ALIAS_TABLE} WHERE length(alias)=14"
        ).fetchone()[0]
        if canonical_alias_count != product_count:
            raise ValueError(
                f"search barcode canonical-alias count mismatch: {canonical_alias_count} != {product_count}"
            )
        if product_count:
            sample_gtin, sample_name = connection.execute(
                "SELECT gtin, name FROM products ORDER BY gtin LIMIT 1"
            ).fetchone()
            token = next((part for part in sample_name.split() if len(part) >= 2), sample_name)
            fts_query = '"' + token.replace('"', '""') + '"*'
            fts_plan = " ".join(
                row[3].upper()
                for row in connection.execute(
                    f"""
                    EXPLAIN QUERY PLAN
                    SELECT p.gtin
                    FROM {FTS_TABLE} AS search
                    JOIN products AS p ON p.gtin=search.gtin
                    WHERE {FTS_TABLE} MATCH ?1
                    ORDER BY bm25({FTS_TABLE}), p.gtin
                    LIMIT 10
                    """,
                    (fts_query,),
                )
            )
            if "VIRTUAL TABLE INDEX" not in fts_plan or "SCAN P" in fts_plan:
                raise ValueError(f"name/brand search is not FTS-indexed: {fts_plan}")
            prefix = barcode_aliases(sample_gtin)[0][:4]
            alias_plan = " ".join(
                row[3].upper()
                for row in connection.execute(
                    f"""
                    EXPLAIN QUERY PLAN
                    SELECT a.gtin
                    FROM {BARCODE_ALIAS_TABLE} AS a
                    WHERE a.alias >= ?1 AND a.alias < ?2
                    ORDER BY a.alias, a.gtin
                    LIMIT 10
                    """,
                    (prefix, prefix + ":"),
                )
            )
            if (
                "SEARCH A USING PRIMARY KEY" not in alias_plan
                and "SEARCH A USING COVERING INDEX" not in alias_plan
            ):
                raise ValueError(f"barcode-prefix search is not primary-key/index backed: {alias_plan}")
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("install", "validate"):
        action = sub.add_parser(name)
        action.add_argument("--database", type=Path, required=True)
        action.add_argument("--manifest", type=Path, required=True)
        if name == "install":
            action.add_argument("--release-notes", type=Path)
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = build_parser().parse_args(list(argv) if argv is not None else None)
    if args.command == "install":
        manifest = install_search_index(
            database_path=args.database,
            manifest_path=args.manifest,
            release_notes_path=args.release_notes,
        )
        print(
            json.dumps(
                {"sha256": manifest["sha256"], "searchIndex": manifest["searchIndex"]},
                sort_keys=True,
            )
        )
    else:
        validate_search_index(database_path=args.database, manifest_path=args.manifest)
        print(json.dumps({"valid": True, "searchIndex": search_manifest()}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
