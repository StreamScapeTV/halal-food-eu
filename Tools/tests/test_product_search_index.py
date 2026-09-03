from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import product_search_index


class ProductSearchIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = root / "catalog.sqlite3"
        self.manifest = root / "catalog-manifest.json"
        self.release_notes = root / "catalog-release-notes.md"

        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA application_id=1212564821")
        connection.execute("PRAGMA user_version=2")
        connection.execute(
            """
            CREATE TABLE products(
                gtin TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                brand TEXT,
                quantity TEXT
            ) WITHOUT ROWID
            """
        )
        connection.executemany(
            "INSERT INTO products VALUES (?,?,?,?)",
            [
                ("04002971296709", "Skyr Natürlich", "Ehrmann", "450 g"),
                ("02000000000004", "Demonstration Oat Drink", "Demo Foods", "1 L"),
                ("00012345678905", "Protein Riegel Schoko", "Müller", "50 g"),
            ],
        )
        connection.commit()
        connection.close()

        digest = hashlib.sha256(self.database.read_bytes()).hexdigest()
        self.manifest.write_text(
            json.dumps(
                {
                    "databaseBytes": self.database.stat().st_size,
                    "schemaVersion": 2,
                    "sha256": digest,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.release_notes.write_text(
            "\n".join(
                [
                    "# Catalog",
                    "",
                    f"- SQLite size: {self.database.stat().st_size:,} bytes",
                    f"- SQLite SHA-256: `{digest}`",
                    "",
                ]
            ),
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_install_is_digest_bound_and_searchable(self) -> None:
        product_search_index.install_search_index(
            database_path=self.database,
            manifest_path=self.manifest,
            release_notes_path=self.release_notes,
        )
        product_search_index.validate_search_index(
            database_path=self.database,
            manifest_path=self.manifest,
        )

        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(
                connection.execute(
                    "SELECT gtin FROM product_barcode_aliases WHERE alias='4002971296709'"
                ).fetchone()[0],
                "04002971296709",
            )
            self.assertEqual(
                connection.execute(
                    """
                    SELECT gtin
                    FROM product_barcode_aliases
                    WHERE alias >= '4002' AND alias < '4002:'
                    ORDER BY alias, gtin
                    LIMIT 1
                    """
                ).fetchone()[0],
                "04002971296709",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT gtin FROM product_search WHERE product_search MATCH ?",
                    ('"natürlich"*',),
                ).fetchone()[0],
                "04002971296709",
            )
            self.assertEqual(
                connection.execute(
                    "SELECT gtin FROM product_search WHERE product_search MATCH ?",
                    ('"muller"*',),
                ).fetchone()[0],
                "00012345678905",
            )
        finally:
            connection.close()

        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(manifest["searchIndex"]["maxPageSize"], 50)
        self.assertEqual(
            manifest["sha256"],
            hashlib.sha256(self.database.read_bytes()).hexdigest(),
        )
        self.assertIn(manifest["sha256"], self.release_notes.read_text(encoding="utf-8"))

    def test_gtin14_aliases_preserve_common_retail_display_forms(self) -> None:
        self.assertEqual(
            product_search_index.barcode_aliases("04002971296709"),
            ["4002971296709", "04002971296709"],
        )
        self.assertEqual(
            product_search_index.barcode_aliases("00012345678905"),
            ["012345678905", "0012345678905", "00012345678905"],
        )
        self.assertEqual(
            product_search_index.barcode_aliases("00000012345670"),
            ["12345670", "000012345670", "0000012345670", "00000012345670"],
        )

    def test_install_refuses_unbound_database_bytes(self) -> None:
        manifest = json.loads(self.manifest.read_text(encoding="utf-8"))
        manifest["sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "SHA-256 mismatch before search indexing"):
            product_search_index.install_search_index(
                database_path=self.database,
                manifest_path=self.manifest,
            )


if __name__ == "__main__":
    unittest.main()
