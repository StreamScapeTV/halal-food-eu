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

    def test_benchmark_preserves_semantics_and_measures_complete_runtime_overhead(self) -> None:
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
                {"id":"re3","gtin":"00200000000042","market":"DE","retailerKey":"lidl","kind":"retailer-observation","observedAt":"2026-08-29T00:00:00Z","retrievedAt":"2026-08-30T02:00:00Z","sourceKey":"open-prices","sourceRecordID":"p3"},
            ]}
            op_quality = {"aliasVersion":"1.0.0"}
            op_metadata = {"snapshotID":"op-real","retrievedAt":"2026-08-30T02:00:00Z","payloadBytes":2000,"upstreamExports":{"prices":{"compressedBytes":500},"proofs":{"compressedBytes":200},"locations":{"compressedBytes":300}}}
            reviews = {
                "assessments":[{"id":"a1","gtin":"00200000000004","status":"questionable","methodologyVersion":"demo","assessedAt":"2026-08-29T00:00:00Z","reasons":[{"code":"UNKNOWN","severity":"caution","title":"Unknown","detail":"Needs review"}]}],
                "certifications":[{"id":"cert1","gtin":"00200000000004","certifier":"Synthetic Body","scheme":"demo","certificateReference":"DEMO-1","effectiveAt":"2026-08-01T00:00:00Z","expiryAt":"2027-08-01T00:00:00Z","lastCheckedAt":"2026-08-29T00:00:00Z","retrievedAt":"2026-08-29T00:00:00Z","sourceKey":"synthetic-certifier","sourceRecordID":"demo-cert"}],
            }
            off_policy = self.write(root, "off-policy.json", {"schemaVersion":1,"sourceKey":"open-food-facts","operator":"Open Food Facts","sourceClass":"open-database","accessMethod":"public-bulk","databaseLicense":{"identifier":"ODbL"},"attribution":"OFF attribution"})
            op_policy = self.write(root, "op-policy.json", {"schemaVersion":1,"sourceKey":"open-prices","operator":"Open Food Facts","sourceClass":"open-database","accessMethod":"public-bulk","databaseLicense":{"identifier":"ODbL"},"attribution":"Open Prices attribution"})
            generic_policy = self.write(root, "generic.json", {"version":1})
            args = argparse.Namespace(
                off_evidence=self.write(root,"off-evidence.json",off_evidence),
                off_selection=self.write(root,"off-selection.json",off_selection),
                off_quality=self.write(root,"off-quality.json",off_quality),
                off_metadata=self.write(root,"off-metadata.json",off_metadata),
                open_prices_evidence=self.write(root,"op-evidence.json",op_evidence),
                open_prices_quality=self.write(root,"op-quality.json",op_quality),
                open_prices_metadata=self.write(root,"op-metadata.json",op_metadata),
                review_fixture=self.write(root,"reviews.json",reviews),
                off_source_policy=off_policy,
                open_prices_source_policy=op_policy,
                selection_policy=generic_policy,
                retailer_aliases=generic_policy,
                work_dir=root / "work",
                report=root / "report.json",
            )
            report = catalog_benchmark.run(args)
            self.assertEqual(report["realCatalog"]["uniqueValidSelectedGTINs"], 2)
            self.assertEqual(report["realCatalog"]["openPricesGermanyObservationRows"], 3)
            self.assertEqual(report["realCatalog"]["openPricesGermanyUniqueGTINs"], 2)
            self.assertEqual(report["realCatalog"]["retailerObservationRowsForSelectedProducts"], 2)
            self.assertEqual(report["realCatalog"]["retailerSummaryRows"], 1)
            self.assertEqual(report["realCatalog"]["basicExclusionRows"], 1)
            self.assertEqual(report["realCatalog"]["commonSemanticSha256"], report["realCatalog"]["roundTripSemanticSha256"])
            self.assertEqual(report["realCatalog"]["commonSemanticSha256"], report["realCatalog"]["auditRoundTripSemanticSha256"])
            self.assertEqual([item["marketCount"] for item in report["projectionMeasurements"]["partitionedMarketGrowthModel"]], [1, 2, 5])

            measurements = report["projectionMeasurements"]["minimalRuntime"]
            self.assertEqual([item["productRows"] for item in measurements], [2,4,10])
            self.assertEqual([item["ingredientObservationRows"] for item in measurements], [1,2,5])
            self.assertTrue(all(item["missingIngredientRowsFabricated"] == 0 for item in measurements))
            self.assertEqual([item["assessmentRows"] for item in measurements], [2,4,10])
            self.assertTrue(all(item["reasonRows"] >= item["assessmentRows"] for item in measurements))
            self.assertTrue(all(item["sourceMetadataRows"] == 3 for item in measurements))
            self.assertTrue(any("PRIMARY KEY" in entry or "products" in entry for entry in measurements[0]["queryPlan"]))
            self.assertEqual(measurements[0]["productDetailQueryCount"], 2)
            self.assertGreaterEqual(measurements[0]["productDetailLookupMs"]["p95"], 0)
            self.assertGreater(measurements[0]["vacuumBytes"], 0)
            self.assertGreater(measurements[0]["gzipBytes"], 0)
            self.assertEqual(report["representativeWeeklyRefresh"]["productRows"], 1)
            self.assertFalse(report["projectionMeasurements"]["semanticRuntimeModel"]["missingIngredientsCreateObservationRows"])

            database = sqlite3.connect(root / "work/catalog-runtime-1x.sqlite3")
            exclusion = database.execute("SELECT gtin,reason FROM basic_exclusion").fetchone()
            ingredient_rows = database.execute("SELECT id,gtin FROM ingredient_observations ORDER BY gtin").fetchall()
            missing = database.execute("SELECT current_ingredient_id,current_assessment_id FROM products WHERE gtin=?", ("00200000000028",)).fetchone()
            missing_assessment = database.execute("SELECT status,ingredient_observation_id,is_benchmark_model FROM product_assessments WHERE id=?", (missing[1],)).fetchone()
            database.close()
            self.assertEqual(exclusion, (catalog_benchmark.modeled_gtin(100), "basic-plain-water"))
            self.assertEqual(ingredient_rows, [("g1", "00200000000004")])
            self.assertIsNone(missing[0])
            self.assertEqual(missing_assessment, ("unknown", None, 1))

            audit = sqlite3.connect(root / "work/catalog-current-audit.sqlite3")
            self.assertEqual(audit.execute("SELECT COUNT(*) FROM ingredients").fetchone()[0], 1)
            audit.close()
            self.assertEqual(report["projectionMeasurements"]["currentAudit"]["missingIngredientRowsFabricated"], 0)

    def test_modeled_gtins_are_valid_and_deterministic(self) -> None:
        first = catalog_benchmark.modeled_gtin(1)
        self.assertEqual(first, catalog_benchmark.modeled_gtin(1))
        self.assertTrue(catalog_benchmark.valid_gtin(first))
        self.assertEqual(len(first), 14)


if __name__ == "__main__":
    unittest.main()
