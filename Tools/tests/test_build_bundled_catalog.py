from __future__ import annotations
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import build_bundled_catalog

ROOT = Path(__file__).resolve().parents[2]


class BuildBundledCatalogTests(unittest.TestCase):
    def test_repository_source_builds_production_catalog(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); database=root/'catalog.sqlite3'; manifest=root/'catalog-manifest.json'
            built=build_bundled_catalog.build_bundle(
                source_manifest_path=ROOT/'Data/catalog/bundled/de/source-manifest-v1.json', database_path=database, manifest_path=manifest,
                source_commit='a'*40, workflow_run='unit-test')
            self.assertEqual(built['recordCount'],7440)
            self.assertEqual(built['sourceShardSet']['recordCount'],7440)
            self.assertEqual(built['sourceShardSet']['shardCount'],32)
            self.assertEqual(built['sourceShardSet']['logicalSha256'],'b272c4bc1cf82f5e10b3afcd4c468a07099b0895fb8d64defae5968b9e68f0a7')
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute('SELECT COUNT(*) FROM products').fetchone()[0],7440)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM product_search').fetchone()[0],7440)


if __name__ == '__main__': unittest.main()
