from __future__ import annotations
import sqlite3, time
from collections import Counter
from pathlib import Path
from typing import Any
from catalog_benchmark_common import BENCHMARK_METHOD, COLD_LOOKUPS, DETAIL_LOOKUPS, WARM_LOOKUPS, db_bytes, gz_size, pct, prep
from catalog_benchmark_model import _certification_template, _modeled_reason_rows, _runtime_rows, _status_for_row

def build_runtime(path: Path, rows: list[dict[str, Any]], retailer_rows: list[dict[str, Any]], basic: list[dict[str, Any]], factor: int, fixture: dict[str, Any], source_rows: list[tuple[Any, ...]]) -> dict[str, Any]:
    """Build a disposable runtime sizing model with complete semantic row overhead.

    The source product/ingredient rows are real admitted evidence. Assessments, reasons and
    certification rows are explicitly flagged benchmark-only and must never be consumed as
    halal decisions. Products with missing ingredient evidence keep a NULL ingredient pointer
    and receive only a modeled `unknown` assessment, never a fabricated ingredient observation.
    """
    started = time.perf_counter()
    connection = sqlite3.connect(path)
    prep(connection)
    connection.executescript('\n        CREATE TABLE catalog_metadata(\n          key TEXT PRIMARY KEY,\n          value TEXT NOT NULL\n        ) WITHOUT ROWID;\n        CREATE TABLE sources(\n          source_key TEXT PRIMARY KEY,\n          operator TEXT,\n          source_class TEXT,\n          access_method TEXT,\n          license_identifier TEXT,\n          attribution TEXT,\n          snapshot_id TEXT,\n          retrieved_at TEXT,\n          policy_sha256 TEXT,\n          is_benchmark_model INTEGER NOT NULL CHECK(is_benchmark_model IN (0,1))\n        ) WITHOUT ROWID;\n        CREATE TABLE products(\n          gtin TEXT PRIMARY KEY,\n          source_gtin TEXT,\n          market TEXT,\n          name TEXT,\n          brand TEXT,\n          quantity TEXT,\n          freshness_state TEXT NOT NULL,\n          current_ingredient_id TEXT,\n          current_assessment_id TEXT,\n          source_key TEXT,\n          source_record_id TEXT,\n          retrieved_at TEXT,\n          is_growth_model INTEGER NOT NULL CHECK(is_growth_model IN (0,1))\n        ) WITHOUT ROWID;\n        CREATE TABLE ingredient_observations(\n          id TEXT PRIMARY KEY,\n          gtin TEXT NOT NULL,\n          text TEXT NOT NULL,\n          language_code TEXT NOT NULL,\n          content_hash TEXT,\n          observed_at TEXT,\n          retrieved_at TEXT,\n          source_key TEXT NOT NULL,\n          source_record_id TEXT NOT NULL,\n          is_growth_model INTEGER NOT NULL CHECK(is_growth_model IN (0,1))\n        ) WITHOUT ROWID;\n        CREATE TABLE product_assessments(\n          id TEXT PRIMARY KEY,\n          gtin TEXT NOT NULL,\n          ingredient_observation_id TEXT,\n          status TEXT NOT NULL,\n          methodology_version TEXT NOT NULL,\n          assessed_at TEXT,\n          summary TEXT NOT NULL,\n          is_benchmark_model INTEGER NOT NULL CHECK(is_benchmark_model=1)\n        ) WITHOUT ROWID;\n        CREATE TABLE assessment_reasons(\n          assessment_id TEXT NOT NULL,\n          position INTEGER NOT NULL,\n          code TEXT NOT NULL,\n          severity TEXT NOT NULL,\n          title TEXT NOT NULL,\n          detail TEXT NOT NULL,\n          evidence_id TEXT,\n          is_benchmark_model INTEGER NOT NULL CHECK(is_benchmark_model=1),\n          PRIMARY KEY(assessment_id, position)\n        ) WITHOUT ROWID;\n        CREATE TABLE certifications(\n          id TEXT PRIMARY KEY,\n          assessment_id TEXT NOT NULL,\n          gtin TEXT NOT NULL,\n          certifier TEXT NOT NULL,\n          scheme TEXT NOT NULL,\n          certificate_reference TEXT NOT NULL,\n          effective_at TEXT,\n          expiry_at TEXT,\n          last_checked_at TEXT,\n          source_key TEXT NOT NULL,\n          source_record_id TEXT NOT NULL,\n          is_benchmark_model INTEGER NOT NULL CHECK(is_benchmark_model=1)\n        ) WITHOUT ROWID;\n        CREATE TABLE retailer_summary(\n          gtin TEXT,\n          retailer_key TEXT,\n          kind TEXT,\n          observed_at TEXT,\n          PRIMARY KEY(gtin, retailer_key)\n        ) WITHOUT ROWID;\n        CREATE TABLE basic_exclusion(\n          gtin TEXT,\n          market TEXT,\n          policy_version TEXT,\n          reason TEXT,\n          PRIMARY KEY(gtin, market)\n        ) WITHOUT ROWID;\n        ')
    connection.executemany('INSERT INTO sources VALUES(?,?,?,?,?,?,?,?,?,?)', source_rows)
    connection.executemany('INSERT INTO catalog_metadata VALUES(?,?)', [('schemaVersion', '1'), ('catalogVersion', 'benchmark-current'), ('methodologyVersion', BENCHMARK_METHOD), ('selectionPolicyVersion', '1.0.0')])
    cert_template = _certification_template(fixture)
    status_counts: Counter[str] = Counter()
    reason_count = 0
    certification_count = 0
    ingredient_count = 0
    growth_ingredient_count = 0
    product_batch: list[tuple[Any, ...]] = []
    ingredient_batch: list[tuple[Any, ...]] = []
    assessment_batch: list[tuple[Any, ...]] = []
    reason_batch: list[tuple[Any, ...]] = []
    certification_batch: list[tuple[Any, ...]] = []

    def flush() -> None:
        nonlocal product_batch, ingredient_batch, assessment_batch, reason_batch, certification_batch
        if product_batch:
            connection.executemany('INSERT INTO products VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', product_batch)
            product_batch = []
        if ingredient_batch:
            connection.executemany('INSERT INTO ingredient_observations VALUES(?,?,?,?,?,?,?,?,?,?)', ingredient_batch)
            ingredient_batch = []
        if assessment_batch:
            connection.executemany('INSERT INTO product_assessments VALUES(?,?,?,?,?,?,?,?)', assessment_batch)
            assessment_batch = []
        if reason_batch:
            connection.executemany('INSERT INTO assessment_reasons VALUES(?,?,?,?,?,?,?,?)', reason_batch)
            reason_batch = []
        if certification_batch:
            connection.executemany('INSERT INTO certifications VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', certification_batch)
            certification_batch = []
    for row, code, is_growth, ingredient_id, sequence in _runtime_rows(rows, factor):
        assessment_id = f'benchmark:assessment:{code}'
        status = _status_for_row(row, sequence)
        status_counts[status] += 1
        product_batch.append((code, row['gtin'], row['market'], row['name'], row['brand'], row['quantity'], row['freshnessState'], ingredient_id, assessment_id, row['sourceKey'], row['sourceRecordID'], row['retrievedAt'], is_growth))
        if ingredient_id is not None:
            ingredient_count += 1
            growth_ingredient_count += is_growth
            ingredient_batch.append((ingredient_id, code, row['ingredientsText'], row['languageCode'], row['contentHash'], row['ingredientObservedAt'], row['ingredientRetrievedAt'], row['sourceKey'], row['sourceRecordID'], is_growth))
        summary = 'Benchmark-only unknown assessment because ingredient evidence is absent.' if status == 'unknown' else 'Benchmark-only semantic storage model; this is not a classification of the source product.'
        assessment_batch.append((assessment_id, code, ingredient_id, status, BENCHMARK_METHOD, row['retrievedAt'], summary, 1))
        modeled_reasons = _modeled_reason_rows(assessment_id, status, ingredient_id)
        reason_count += len(modeled_reasons)
        reason_batch.extend(modeled_reasons)
        if status == 'halal-certified':
            certification_count += 1
            certification_id = f'benchmark:certification:{code}'
            certification_batch.append((certification_id, assessment_id, code, str(cert_template.get('certifier', 'Synthetic benchmark certifier')), str(cert_template.get('scheme', 'benchmark-only')), f'BENCHMARK-{code}', cert_template.get('effectiveAt'), cert_template.get('expiryAt'), cert_template.get('lastCheckedAt'), str(cert_template.get('sourceKey', 'synthetic-certifier')), str(cert_template.get('sourceRecordID', 'benchmark-only')), 1))
        if len(product_batch) >= 5000:
            flush()
    flush()
    if factor == 1:
        connection.executemany('INSERT INTO retailer_summary VALUES(?,?,?,?)', [(row['gtin'], row['retailerKey'], row['kind'], row['observedAt'] or row['retrievedAt']) for row in retailer_rows])
        connection.executemany('INSERT INTO basic_exclusion VALUES(?,?,?,?)', [(row['gtin'], row['market'], row['policyVersion'], row['reasonCode']) for row in basic])
    before = db_bytes(connection)
    connection.executescript('\n        CREATE INDEX idx_products_source ON products(source_key, source_record_id);\n        CREATE INDEX idx_ingredients_gtin ON ingredient_observations(gtin);\n        CREATE INDEX idx_assessments_gtin ON product_assessments(gtin);\n        CREATE INDEX idx_certifications_assessment ON certifications(assessment_id);\n        ')
    after = db_bytes(connection)
    connection.commit()
    connection.execute('VACUUM')
    connection.close()
    uri = f'file:{path.as_posix()}?mode=ro'
    readonly = sqlite3.connect(uri, uri=True)
    probes = [row['gtin'] for row in rows[:WARM_LOOKUPS]]
    query_plan = [str(item) for item in readonly.execute('EXPLAIN QUERY PLAN SELECT * FROM products WHERE gtin=?', (probes[0],)).fetchall()]
    warm: list[float] = []
    for index in range(WARM_LOOKUPS):
        started_lookup = time.perf_counter_ns()
        found = readonly.execute('SELECT gtin,name,brand,source_key,source_record_id FROM products WHERE gtin=?', (probes[index % len(probes)],)).fetchone()
        warm.append((time.perf_counter_ns() - started_lookup) / 1000000.0)
        if not found:
            raise AssertionError('known GTIN missing')
    basic_plan: list[str] = []
    basic_latency: list[float] = []
    if factor == 1 and basic:
        basic_plan = [str(item) for item in readonly.execute("EXPLAIN QUERY PLAN SELECT reason FROM basic_exclusion WHERE gtin=? AND market='DE'", (basic[0]['gtin'],)).fetchall()]
        for index in range(WARM_LOOKUPS):
            started_lookup = time.perf_counter_ns()
            found = readonly.execute("SELECT reason FROM basic_exclusion WHERE gtin=? AND market='DE'", (basic[index % len(basic)]['gtin'],)).fetchone()
            basic_latency.append((time.perf_counter_ns() - started_lookup) / 1000000.0)
            if not found:
                raise AssertionError('known exclusion missing')
    detail_sql = '\n        SELECT p.gtin,p.name,p.brand,p.quantity,p.freshness_state,\n               i.text,i.language_code,i.content_hash,i.observed_at,i.retrieved_at,\n               a.id,a.status,a.methodology_version,a.assessed_at,a.summary,\n               c.certifier,c.scheme,c.certificate_reference,c.expiry_at,\n               s.operator,s.license_identifier,s.attribution\n          FROM products p\n          LEFT JOIN ingredient_observations i ON i.id=p.current_ingredient_id\n          LEFT JOIN product_assessments a ON a.id=p.current_assessment_id\n          LEFT JOIN certifications c ON c.assessment_id=a.id\n          LEFT JOIN sources s ON s.source_key=p.source_key\n         WHERE p.gtin=?\n    '
    reason_sql = '\n        SELECT position,code,severity,title,detail,evidence_id\n          FROM assessment_reasons\n         WHERE assessment_id=?\n         ORDER BY position\n    '
    detail_plan = [str(item) for item in readonly.execute('EXPLAIN QUERY PLAN ' + detail_sql, (probes[0],)).fetchall()]
    first_assessment = readonly.execute('SELECT current_assessment_id FROM products WHERE gtin=?', (probes[0],)).fetchone()[0]
    reason_plan = [str(item) for item in readonly.execute('EXPLAIN QUERY PLAN ' + reason_sql, (first_assessment,)).fetchall()]
    detail_latency: list[float] = []
    for index in range(min(DETAIL_LOOKUPS, max(1, len(probes)))):
        code = probes[index % len(probes)]
        started_lookup = time.perf_counter_ns()
        detail = readonly.execute(detail_sql, (code,)).fetchone()
        if not detail:
            raise AssertionError('known product detail missing')
        assessment_id = detail[10]
        reasons = readonly.execute(reason_sql, (assessment_id,)).fetchall()
        if not reasons:
            raise AssertionError('modeled assessment has no reason')
        detail_latency.append((time.perf_counter_ns() - started_lookup) / 1000000.0)
    readonly.close()
    cold: list[float] = []
    for code in probes[:min(COLD_LOOKUPS, len(probes))]:
        started_lookup = time.perf_counter_ns()
        connection = sqlite3.connect(uri, uri=True)
        found = connection.execute('SELECT gtin,name,brand FROM products WHERE gtin=?', (code,)).fetchone()
        connection.close()
        cold.append((time.perf_counter_ns() - started_lookup) / 1000000.0)
        if not found:
            raise AssertionError('known GTIN missing after open')
    return {'growthFactor': factor, 'productRows': len(rows) * factor, 'modeledGrowthRows': len(rows) * (factor - 1), 'ingredientObservationRows': ingredient_count, 'modeledGrowthIngredientRows': growth_ingredient_count, 'missingIngredientRowsFabricated': 0, 'assessmentRows': len(rows) * factor, 'assessmentStatusCounts': dict(sorted(status_counts.items())), 'reasonRows': reason_count, 'certificationRows': certification_count, 'sourceMetadataRows': len(source_rows), 'beforeIndexBytes': before, 'afterIndexBytesBeforeVacuum': after, 'indexOverheadBytes': after - before, 'vacuumBytes': path.stat().st_size, 'gzipBytes': gz_size(path), 'buildSeconds': round(time.perf_counter() - started, 3), 'queryPlan': query_plan, 'basicExclusionQueryPlan': basic_plan, 'basicExclusionLookupMs': None if not basic_latency else {'p50': round(pct(basic_latency, 0.5), 4), 'p95': round(pct(basic_latency, 0.95), 4), 'p99': round(pct(basic_latency, 0.99), 4)}, 'warmLookupMs': {'p50': round(pct(warm, 0.5), 4), 'p95': round(pct(warm, 0.95), 4), 'p99': round(pct(warm, 0.99), 4)}, 'productDetailQueryCount': 2, 'productDetailQueryPlan': detail_plan, 'assessmentReasonQueryPlan': reason_plan, 'productDetailLookupMs': {'p50': round(pct(detail_latency, 0.5), 4), 'p95': round(pct(detail_latency, 0.95), 4), 'p99': round(pct(detail_latency, 0.99), 4)}, 'firstLookupAfterOpenMs': {'p50': round(pct(cold, 0.5), 4), 'p95': round(pct(cold, 0.95), 4), 'p99': round(pct(cold, 0.99), 4)}}
