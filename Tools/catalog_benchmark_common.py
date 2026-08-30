from __future__ import annotations
import gzip, hashlib, json, math, os, sqlite3, statistics, tempfile
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

APP_ID = 1212564821

GROWTH_FACTORS = (1, 2, 5)

WARM_LOOKUPS = 1000

COLD_LOOKUPS = 25

DETAIL_LOOKUPS = 500

BENCHMARK_METHOD = 'benchmark-storage-model-v1'

ASSESSMENT_STATUSES = ('halal-certified', 'halal-reviewed', 'not-halal', 'questionable')

def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding='utf-8'))

def canon(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')

def digest_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()

def pct(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, math.ceil(quantile * len(ordered)) - 1))
    return ordered[index]

def gtin_ok(value: str) -> bool:
    if len(value) != 14 or not value.isascii() or (not value.isdigit()):
        return False
    total = sum((int(character) * (3 if index % 2 == 0 else 1) for index, character in enumerate(reversed(value[:-1]))))
    return (10 - total % 10) % 10 == int(value[-1])

valid_gtin = gtin_ok

def modeled_gtin(number: int) -> str:
    body = f'99{number:011d}'
    total = sum((int(character) * (3 if index % 2 == 0 else 1) for index, character in enumerate(reversed(body))))
    return body + str((10 - total % 10) % 10)

def gz_size(path: Path) -> int:
    with path.open('rb') as source, tempfile.TemporaryFile() as output:
        with gzip.GzipFile(fileobj=output, mode='wb', compresslevel=9, mtime=0) as archive:
            for chunk in iter(lambda: source.read(1 << 20), b''):
                archive.write(chunk)
        return output.tell()

def db_bytes(connection: sqlite3.Connection) -> int:
    page_count = int(connection.execute('PRAGMA page_count').fetchone()[0])
    page_size = int(connection.execute('PRAGMA page_size').fetchone()[0])
    return page_count * page_size

def prep(connection: sqlite3.Connection) -> None:
    connection.execute('PRAGMA journal_mode=OFF')
    connection.execute('PRAGMA synchronous=OFF')
    connection.execute('PRAGMA temp_store=FILE')
    connection.execute('PRAGMA page_size=4096')
    connection.execute(f'PRAGMA application_id={APP_ID}')
    connection.execute('PRAGMA user_version=1')

def products(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    identities = {item['id']: item for item in evidence.get('identities', [])}
    ingredients = {item['id']: item for item in evidence.get('ingredients', [])}
    output: list[dict[str, Any]] = []
    for selection in evidence.get('currentSelections', []):
        identity = identities.get(selection.get('identityObservationID'))
        ingredient = ingredients.get(selection.get('ingredientObservationID'))
        code = str(selection.get('gtin', ''))
        if not identity or selection.get('market') != 'DE' or (not gtin_ok(code)):
            continue
        output.append({'gtin': code, 'market': 'DE', 'name': str(identity.get('name', '')), 'brand': identity.get('brand'), 'quantity': identity.get('quantity'), 'freshnessState': 'unassessed', 'sourceKey': str(identity.get('sourceKey', '')), 'sourceRecordID': str(identity.get('sourceRecordID', '')), 'retrievedAt': str(identity.get('retrievedAt', '')), 'sourceModifiedAt': identity.get('sourceModifiedAt'), 'ingredientsText': '' if not ingredient else str(ingredient.get('ingredientsText', '')), 'languageCode': 'und' if not ingredient else str(ingredient.get('languageCode', 'und')), 'contentHash': None if not ingredient else ingredient.get('contentHash'), 'ingredientObservedAt': None if not ingredient else ingredient.get('observedAt'), 'ingredientRetrievedAt': None if not ingredient else ingredient.get('retrievedAt'), 'identityEvidenceID': identity.get('id'), 'ingredientEvidenceID': None if not ingredient else ingredient.get('id')})
    return sorted(output, key=lambda item: item['gtin'])

def all_germany_retailers(evidence: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for item in evidence.get('retailerEvidence', []):
        code = str(item.get('gtin', ''))
        if item.get('market') != 'DE' or not gtin_ok(code):
            continue
        output.append({key: item.get(key) for key in ('id', 'gtin', 'retailerKey', 'kind', 'observedAt', 'retrievedAt', 'sourceKey', 'sourceRecordID')})
    return sorted(output, key=lambda item: (str(item['gtin']), str(item['retailerKey']), str(item['observedAt']), str(item['id'])))

def retailers_for_selected(rows: list[dict[str, Any]], selected: set[str]) -> list[dict[str, Any]]:
    return [row for row in rows if row['gtin'] in selected]

def latest(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: dict[tuple[Any, Any], dict[str, Any]] = {}
    for row in rows:
        key = (row['gtin'], row['retailerKey'])
        stamp = str(row.get('observedAt') or row.get('retrievedAt') or '')
        existing = output.get(key)
        existing_key = (str(existing.get('observedAt') or existing.get('retrievedAt') or ''), str(existing['id'])) if existing else ('', '')
        if existing is None or (stamp, str(row['id'])) > existing_key:
            output[key] = row
    return sorted(output.values(), key=lambda item: (str(item['gtin']), str(item['retailerKey'])))

def semantic(rows: list[dict[str, Any]]) -> str:
    keys = ('gtin', 'market', 'name', 'brand', 'quantity', 'freshnessState', 'sourceKey', 'sourceRecordID', 'ingredientsText', 'languageCode', 'contentHash')
    return hashlib.sha256(canon([{key: row[key] for key in keys} for row in rows])).hexdigest()

def ingredient_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    sizes = [len(row['ingredientsText'].encode('utf-8')) for row in rows if row['ingredientsText']]
    languages = Counter((row['languageCode'] for row in rows if row['ingredientsText']))
    return {'withIngredients': len(sizes), 'missingIngredients': len(rows) - len(sizes), 'coveragePercent': round(100 * len(sizes) / len(rows), 3), 'languageCounts': dict(sorted(languages.items())), 'ingredientTextBytes': {'average': round(statistics.fmean(sizes), 2) if sizes else 0, 'p50': pct([float(value) for value in sizes], 0.5), 'p95': pct([float(value) for value in sizes], 0.95)}}

def weekly(rows: list[dict[str, Any]], retailer_rows: list[dict[str, Any]], anchor_raw: str) -> dict[str, Any]:
    try:
        anchor = datetime.fromisoformat(anchor_raw.replace('Z', '+00:00'))
    except ValueError:
        return {'method': 'unavailable', 'productRows': 0, 'retailerObservations': 0, 'canonicalBytes': 0}
    cutoff = anchor - timedelta(days=7)
    changed: list[tuple[Any, ...]] = []
    observed: list[tuple[Any, ...]] = []
    for row in rows:
        raw = row.get('sourceModifiedAt')
        try:
            stamp = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        except ValueError:
            continue
        if stamp >= cutoff:
            changed.append((row['gtin'], raw, row['contentHash']))
    for row in retailer_rows:
        raw = row.get('observedAt') or row.get('retrievedAt')
        try:
            stamp = datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        except ValueError:
            continue
        if stamp >= cutoff:
            observed.append((row['gtin'], row['retailerKey'], raw))
    return {'method': 'single-snapshot 7-day sourceModifiedAt/observedAt proxy', 'windowStart': cutoff.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'), 'windowEnd': anchor.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z'), 'productRows': len(changed), 'retailerObservations': len(observed), 'canonicalBytes': len(canon({'products': changed, 'retailerObservations': observed}))}
