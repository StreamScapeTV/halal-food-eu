from __future__ import annotations
import hashlib, sqlite3
from typing import Any, Iterable
from catalog_benchmark_common import ASSESSMENT_STATUSES, canon, modeled_gtin

def _source_row(policy: dict[str, Any], metadata: dict[str, Any], policy_sha256: str) -> tuple[Any, ...]:
    license_data = policy.get('databaseLicense') or {}
    return (str(policy.get('sourceKey', '')), str(policy.get('operator', '')), str(policy.get('sourceClass', '')), str(policy.get('accessMethod', '')), str(license_data.get('identifier', '')), str(policy.get('attribution', '')), str(metadata.get('snapshotID', '')), str(metadata.get('retrievedAt', '')), policy_sha256, 0)

def _benchmark_certifier_source(fixture: dict[str, Any]) -> tuple[Any, ...] | None:
    certifications = fixture.get('certifications', [])
    if not certifications:
        return None
    certification = certifications[0]
    return (str(certification.get('sourceKey', 'synthetic-certifier')), str(certification.get('certifier', 'Synthetic benchmark certifier')), 'benchmark-model', 'local-fixture', 'benchmark-only', 'Synthetic benchmark-only certification schema overhead; not product evidence.', 'review-overhead-fixture', str(certification.get('retrievedAt', '')), hashlib.sha256(canon(fixture)).hexdigest(), 1)

def _modeled_reason_rows(assessment_id: str, status: str, evidence_id: str | None) -> list[tuple[Any, ...]]:
    if status == 'unknown':
        count = 1
        base = 'MISSING-INGREDIENT-EVIDENCE'
        severity = 'caution'
    elif status == 'questionable':
        count = 2
        base = 'BENCHMARK-AMBIGUOUS-EVIDENCE'
        severity = 'caution'
    elif status == 'halal-certified':
        count = 2
        base = 'BENCHMARK-CERTIFICATION-EVIDENCE'
        severity = 'positive'
    elif status == 'halal-reviewed':
        count = 1
        base = 'BENCHMARK-REVIEW-EVIDENCE'
        severity = 'positive'
    else:
        count = 2
        base = 'BENCHMARK-PROHIBITED-EVIDENCE'
        severity = 'negative'
    return [(assessment_id, position, base if position == 0 else f'{base}-{position + 1}', severity, 'Benchmark schema-overhead reason', 'Synthetic benchmark-only reason text sized to exercise the runtime assessment schema without classifying the real source product.', evidence_id, 1) for position in range(count)]

def _status_for_row(row: dict[str, Any], sequence: int) -> str:
    if row['ingredientEvidenceID'] is None:
        return 'unknown'
    return ASSESSMENT_STATUSES[sequence % len(ASSESSMENT_STATUSES)]

def _certification_template(fixture: dict[str, Any]) -> dict[str, Any]:
    certifications = fixture.get('certifications', [])
    if certifications:
        return certifications[0]
    return {'certifier': 'Synthetic benchmark certifier', 'scheme': 'benchmark-only', 'certificateReference': 'BENCHMARK-REFERENCE', 'effectiveAt': '2026-01-01T00:00:00Z', 'expiryAt': '2027-01-01T00:00:00Z', 'lastCheckedAt': '2026-01-01T00:00:00Z', 'sourceKey': 'synthetic-certifier', 'sourceRecordID': 'benchmark-only'}

def _insert_batches(connection: sqlite3.Connection, sql: str, rows: Iterable[tuple[Any, ...]], batch_size: int=5000) -> int:
    batch: list[tuple[Any, ...]] = []
    total = 0
    for row in rows:
        batch.append(row)
        if len(batch) >= batch_size:
            connection.executemany(sql, batch)
            total += len(batch)
            batch.clear()
    if batch:
        connection.executemany(sql, batch)
        total += len(batch)
    return total

def _runtime_rows(rows: list[dict[str, Any]], factor: int) -> Iterable[tuple[dict[str, Any], str, int, str | None, int]]:
    existing = {row['gtin'] for row in rows}
    generated = 0
    sequence = 0
    for shard in range(factor):
        for row in rows:
            if shard == 0:
                code = row['gtin']
                is_growth = 0
            else:
                while True:
                    generated += 1
                    code = modeled_gtin(generated)
                    if code not in existing:
                        break
                is_growth = 1
            ingredient_id: str | None = None
            if row['ingredientEvidenceID'] is not None:
                ingredient_id = str(row['ingredientEvidenceID']) if not is_growth else f'benchmark:ingredient:{code}'
            yield (row, code, is_growth, ingredient_id, sequence)
            sequence += 1
