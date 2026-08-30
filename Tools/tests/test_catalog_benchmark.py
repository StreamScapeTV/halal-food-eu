from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import catalog_benchmark


class CatalogBenchmarkTests(unittest.TestCase):
    def write(self, root: Path, name: str, value: object) -> Path:
        path = root / name
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def test_benchmark_preserves_semantics_and_measures_growth_and_exclusion_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            identities = [
                {"id":"i1","gtin":"00200000000004","market":"DE","name":"Oat Drink","brand":"Demo","sourceKey":"open-food-facts","sourceRecordID":"r1","retrievedAt":"2026-08-30T02:00:00Z","sourceModifiedAt":"2026-08-29T00:00:00Z"},
                {"id":"i2","gtin":"00200000000028","market":"DE","name":"Dessert","brand":"Demo","sourceKey":"open-food-facts","sourceRecordID":"r2","retrievedAt":"2026-08-30T02:00:00Z","sourceModifiedAt":"2026-08-01T00:00:00Z"},
            ]
            ingredients = [
                {"id":"g1","gtin":"00200000000004","market":"DE","ingredientsText":"Water, oats, oil, salt.","languageCode":"en","contentHash":"a"*64,"observedAt":"2026-08-29T00:00:00Z","retrievedAt":"2026-08-30T02:00:00Z"},
            ]
            off_evidence = {
                "identities": identities,
                "ingredients": ingredients,
                "currentSelections": [
                    {"gtin":"00200000000004","market":"DE","identityObservationID":"i1","ingredientObservationID":"g1","conflictFlags":[]},
                    {"gtin":"00200000000028","market":"DE","identityObservationID":"i2","conflictFlags":["ingredients-missing"]},
                ],
            }
            off_selection = {
                "basicExclusions":[{"gtin":catalog_benchmark.modeled_gtin(100),"market":"DE","policyVersion":"1.0.0","reasonCode":"basic-plain-water"}],
                "report":{"includedProducts":2,"excludedBasicProducts":1},
            }
            off_quality = {"selectionPolicyVersion":"1.0.0"}
            off_metadata = {"snapshotID":"off-real","retrievedAt":"2026-08-30T02:00:00Z","transportSha256":"b"*64,"transportBytes":1000,"expandedBytes":4000,"recordsExamined":20,"recordsEmitted":2,"sourceSchemaVersions":{"1004":20},"expectedProductSchemaVersion":"1004","apiVersion":"3.6","tagSchema":"tags_sources"}
            op_evidence = {"retailerEvidence":[
                {"id":"re1","gtin":"00200000000004","market":"DE","retailerKey":"rewe","kind":"retailer-observation","observedAt":"2026-08-28T00:00:00Z","retrievedAt":"2026-08-30T02:00:00Z","sourceKey":"open-prices","sourceRecordID":"p1"},
                {"id":"re2","gtin":"00200000000004","market":"DE","retailerKey":"rewe","kind":"retailer-observation","observedAt":"2026-08-29T00:00:00Z","retrievedAt":"2026-08-30T02:00:00Z","sourceKey":"open-prices","sourceRecordID":"p2"},
            ]}
            op_quality = {"aliasVersion":"1.0.0"}
            op_metadata = {"snapshotID":"op-real","retrievedAt":"2026-08-30T02:00:00Z","payloadBytes":2000,"upstreamExports":{"prices":{"compressedBytes":500},"proofs":{"compressedBytes":200},"locations":{"compressedBytes":300}}}
            reviews = {"assessments":[{"id":"a1","gtin":"00200000000004","status":"questionable","methodologyVersion":"demo","assessedAt":"2026-08-29T00:00:00Z","reasons":[{"code":"UNKNOWN","severity":"caution","title":"Unknown","detail":"Needs review"}]}]}
            policy_file = self.write(root, "policy.json", {"version":1})
            args = argparse.Namespace(
                off_evidence=self.write(root,"off-evidence.json",off_evidence),
                off_selection=self.write(root,"off-selection.json",off_selection),
                off_quality=self.write(root,"off-quality.json",off_quality),
                off_metadata=self.write(root,"off-metadata.json",off_metadata),
                open_prices_evidence=self.write(root,"op-evidence.json",op_evidence),
                open_prices_quality=self.write(root,"op-quality.json",op_quality),
                open_prices_metadata=self.write(root,"op-metadata.json",op_metadata),
                review_fixture=self.write(root,"reviews.json",reviews),
                off_source_policy=policy_file,
                open_prices_source_policy=policy_file,
                selection_policy=policy_file,
                retailer_aliases=policy_file,
                work_dir=root / "work",
                report=root / "report.json",
            )
            report = catalog_benchmark.run(args)
            self.assertEqual(report["realCatalog"]["uniqueValidSelectedGTINs"], 2)
            self.assertEqual(report["realCatalog"]["retailerObservationRowsForSelectedProducts"], 2)
            self.assertEqual(report["realCatalog"]["retailerSummaryRows"], 1)
            self.assertEqual(report["realCatalog"]["basicExclusionRows"], 1)
            self.assertEqual(report["realCatalog"]["commonSemanticSha256"], report["realCatalog"]["roundTripSemanticSha256"])
            measurements = report["projectionMeasurements"]["minimalRuntime"]
            self.assertEqual([item["productRows"] for item in measurements], [2,4,10])
            self.assertTrue(any("PRIMARY KEY" in entry or "products" in entry for entry in measurements[0]["queryPlan"]))
            self.assertGreater(measurements[0]["vacuumBytes"], 0)
            self.assertGreater(measurements[0]["gzipBytes"], 0)
            self.assertEqual(report["representativeWeeklyRefresh"]["productRows"], 1)
            database = sqlite3.connect(root / "work/catalog-runtime-1x.sqlite3")
            exclusion = database.execute("SELECT gtin,reason FROM basic_exclusion").fetchone()
            database.close()
            self.assertEqual(exclusion, (catalog_benchmark.modeled_gtin(100), "basic-plain-water"))

    def test_modeled_gtins_are_valid_and_deterministic(self) -> None:
        first = catalog_benchmark.modeled_gtin(1)
        self.assertEqual(first, catalog_benchmark.modeled_gtin(1))
        self.assertTrue(catalog_benchmark.valid_gtin(first))
        self.assertEqual(len(first), 14)


if __name__ == "__main__":
    unittest.main()
