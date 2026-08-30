from __future__ import annotations
import sqlite3, time
from pathlib import Path
from typing import Any
from catalog_benchmark_common import db_bytes, gz_size, prep

def build_audit(path: Path, rows: list[dict[str, Any]], retailer_rows: list[dict[str, Any]], fixture: dict[str, Any]) -> dict[str, Any]:
    """Build a current/audit sizing projection without fabricating missing ingredients."""
    started = time.perf_counter()
    connection = sqlite3.connect(path)
    prep(connection)
    connection.executescript('\n        CREATE TABLE products(\n          gtin TEXT PRIMARY KEY,\n          market TEXT,\n          name TEXT,\n          brand TEXT,\n          quantity TEXT,\n          freshness_state TEXT NOT NULL,\n          source_key TEXT,\n          source_record_id TEXT,\n          identity_id TEXT\n        );\n        CREATE TABLE ingredients(\n          id TEXT PRIMARY KEY,\n          gtin TEXT,\n          text TEXT,\n          language TEXT,\n          content_hash TEXT,\n          observed_at TEXT,\n          retrieved_at TEXT\n        );\n        CREATE TABLE retailer_observations(\n          id TEXT PRIMARY KEY,\n          gtin TEXT,\n          retailer_key TEXT,\n          kind TEXT,\n          observed_at TEXT,\n          retrieved_at TEXT,\n          source_key TEXT,\n          source_record_id TEXT\n        );\n        CREATE TABLE representative_assessments(\n          id TEXT PRIMARY KEY,\n          gtin TEXT,\n          status TEXT,\n          methodology TEXT,\n          assessed_at TEXT,\n          synthetic INTEGER CHECK(synthetic=1)\n        );\n        CREATE TABLE representative_reasons(\n          assessment_id TEXT,\n          position INTEGER,\n          code TEXT,\n          severity TEXT,\n          title TEXT,\n          detail TEXT,\n          synthetic INTEGER CHECK(synthetic=1),\n          PRIMARY KEY(assessment_id, position)\n        );\n        ')
    connection.executemany('INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?)', [(row['gtin'], row['market'], row['name'], row['brand'], row['quantity'], row['freshnessState'], row['sourceKey'], row['sourceRecordID'], row['identityEvidenceID']) for row in rows])
    connection.executemany('INSERT INTO ingredients VALUES(?,?,?,?,?,?,?)', [(row['ingredientEvidenceID'], row['gtin'], row['ingredientsText'], row['languageCode'], row['contentHash'], row['ingredientObservedAt'], row['ingredientRetrievedAt']) for row in rows if row['ingredientEvidenceID'] is not None])
    connection.executemany('INSERT INTO retailer_observations VALUES(?,?,?,?,?,?,?,?)', [(row['id'], row['gtin'], row['retailerKey'], row['kind'], row['observedAt'], row['retrievedAt'], row['sourceKey'], row['sourceRecordID']) for row in retailer_rows])
    assessments = fixture.get('assessments', [])
    connection.executemany('INSERT INTO representative_assessments VALUES(?,?,?,?,?,1)', [(assessment['id'], assessment['gtin'], assessment['status'], assessment['methodologyVersion'], assessment['assessedAt']) for assessment in assessments])
    reasons: list[tuple[Any, ...]] = []
    for assessment in assessments:
        reasons.extend(((assessment['id'], index, reason['code'], reason['severity'], reason['title'], reason['detail']) for index, reason in enumerate(assessment.get('reasons', []))))
    connection.executemany('INSERT INTO representative_reasons VALUES(?,?,?,?,?,?,1)', reasons)
    before = db_bytes(connection)
    connection.executescript('\n        CREATE INDEX idx_products_source ON products(source_key, source_record_id);\n        CREATE INDEX idx_ingredients_gtin ON ingredients(gtin);\n        CREATE INDEX idx_retailer_gtin ON retailer_observations(gtin, retailer_key, observed_at DESC);\n        ')
    after = db_bytes(connection)
    connection.commit()
    connection.execute('VACUUM')
    connection.close()
    return {'beforeIndexBytes': before, 'afterIndexBytesBeforeVacuum': after, 'indexOverheadBytes': after - before, 'vacuumBytes': path.stat().st_size, 'gzipBytes': gz_size(path), 'buildSeconds': round(time.perf_counter() - started, 3), 'realIngredientRows': sum((1 for row in rows if row['ingredientEvidenceID'] is not None)), 'missingIngredientRowsFabricated': 0, 'representativeAssessments': len(assessments), 'representativeReasons': len(reasons)}
