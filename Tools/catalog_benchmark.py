#!/usr/bin/env python3
"""Benchmark admitted Germany catalog evidence as disposable SQLite projections."""
from __future__ import annotations
import argparse, hashlib, json, os, resource, sqlite3
from collections import Counter
from pathlib import Path
from typing import Any
from catalog_benchmark_audit import build_audit
from catalog_benchmark_common import BENCHMARK_METHOD, GROWTH_FACTORS, all_germany_retailers, canon, digest_file, gtin_ok, ingredient_metrics, latest, load, modeled_gtin, products, retailers_for_selected, semantic, valid_gtin, weekly
from catalog_benchmark_model import _benchmark_certifier_source, _source_row
from catalog_benchmark_runtime import build_runtime

def run(args: argparse.Namespace) -> dict[str, Any]:
    off_evidence = load(args.off_evidence)
    off_selection = load(args.off_selection)
    off_quality = load(args.off_quality)
    off_metadata = load(args.off_metadata)
    open_prices_evidence = load(args.open_prices_evidence)
    open_prices_quality = load(args.open_prices_quality)
    open_prices_metadata = load(args.open_prices_metadata)
    fixture = load(args.review_fixture)
    off_policy = load(args.off_source_policy)
    open_prices_policy = load(args.open_prices_source_policy)
    rows = products(off_evidence)
    if not rows:
        raise ValueError('no selected Germany products')
    all_open_prices = all_germany_retailers(open_prices_evidence)
    selected_gtins = {row['gtin'] for row in rows}
    selected_retailers = retailers_for_selected(all_open_prices, selected_gtins)
    current_retailers = latest(selected_retailers)
    basic = [item for item in off_selection.get('basicExclusions', []) if item.get('market') == 'DE' and gtin_ok(str(item.get('gtin', '')))]
    source_rows = [_source_row(off_policy, off_metadata, digest_file(args.off_source_policy)), _source_row(open_prices_policy, open_prices_metadata, digest_file(args.open_prices_source_policy))]
    benchmark_certifier = _benchmark_certifier_source(fixture)
    if benchmark_certifier is not None:
        source_rows.append(benchmark_certifier)
    args.work_dir.mkdir(parents=True, exist_ok=True)
    audit_path = args.work_dir / 'catalog-current-audit.sqlite3'
    audit_path.unlink(missing_ok=True)
    audit = build_audit(audit_path, rows, selected_retailers, fixture)
    runtime: list[dict[str, Any]] = []
    runtime_one_path: Path | None = None
    for factor in GROWTH_FACTORS:
        path = args.work_dir / f'catalog-runtime-{factor}x.sqlite3'
        path.unlink(missing_ok=True)
        runtime.append(build_runtime(path, rows, current_retailers, basic, factor, fixture, source_rows))
        if factor == 1:
            runtime_one_path = path
        else:
            path.unlink(missing_ok=True)
    no_basic_path = args.work_dir / 'catalog-runtime-1x-no-basic.sqlite3'
    no_basic_path.unlink(missing_ok=True)
    no_basic = build_runtime(no_basic_path, rows, current_retailers, [], 1, fixture, source_rows)
    no_basic_path.unlink(missing_ok=True)
    basic_cost = {'rows': len(basic), 'vacuumByteDelta': runtime[0]['vacuumBytes'] - no_basic['vacuumBytes'], 'gzipByteDelta': runtime[0]['gzipBytes'] - no_basic['gzipBytes'], 'lookupMs': runtime[0]['basicExclusionLookupMs'], 'queryPlan': runtime[0]['basicExclusionQueryPlan']}
    if runtime_one_path is None:
        raise AssertionError('1x runtime projection was not built')
    readonly = sqlite3.connect(f'file:{runtime_one_path.as_posix()}?mode=ro', uri=True)
    roundtrip = [{'gtin': item[0], 'market': item[1], 'name': item[2], 'brand': item[3], 'quantity': item[4], 'freshnessState': item[5], 'sourceKey': item[6], 'sourceRecordID': item[7], 'ingredientsText': item[8] or '', 'languageCode': item[9] or 'und', 'contentHash': item[10]} for item in readonly.execute('\n            SELECT p.gtin,p.market,p.name,p.brand,p.quantity,p.freshness_state,p.source_key,p.source_record_id,\n                   i.text,i.language_code,i.content_hash\n              FROM products p\n              LEFT JOIN ingredient_observations i ON i.id=p.current_ingredient_id\n             WHERE p.is_growth_model=0\n             ORDER BY p.gtin\n            ')]
    readonly.close()
    semantic_hash = semantic(rows)
    roundtrip_hash = hashlib.sha256(canon(roundtrip)).hexdigest()
    if semantic_hash != roundtrip_hash:
        raise AssertionError('semantic round trip mismatch')
    audit_readonly = sqlite3.connect(f'file:{audit_path.as_posix()}?mode=ro', uri=True)
    audit_roundtrip = [{'gtin': item[0], 'market': item[1], 'name': item[2], 'brand': item[3], 'quantity': item[4], 'freshnessState': item[5], 'sourceKey': item[6], 'sourceRecordID': item[7], 'ingredientsText': item[8] or '', 'languageCode': item[9] or 'und', 'contentHash': item[10]} for item in audit_readonly.execute('\n            SELECT p.gtin,p.market,p.name,p.brand,p.quantity,p.freshness_state,p.source_key,p.source_record_id,\n                   i.text,i.language,i.content_hash\n              FROM products p\n              LEFT JOIN ingredients i ON i.gtin=p.gtin\n             ORDER BY p.gtin\n            ')]
    audit_readonly.close()
    audit_roundtrip_hash = hashlib.sha256(canon(audit_roundtrip)).hexdigest()
    if semantic_hash != audit_roundtrip_hash:
        raise AssertionError('audit semantic round trip mismatch')
    open_prices_compressed = sum((int(item.get('compressedBytes', 0)) for item in open_prices_metadata.get('upstreamExports', {}).values()))
    report = {'schemaVersion': 1, 'architectureCandidate': 'A-bundled-sqlite', 'scope': 'Germany detailed catalog selected by accepted v1 policy; Open Prices is observational only', 'sourceSnapshots': {'openFoodFacts': {'snapshotID': off_metadata.get('snapshotID'), 'retrievedAt': off_metadata.get('retrievedAt'), 'transportSha256': off_metadata.get('transportSha256'), 'transportCompressedBytes': int(off_metadata.get('transportBytes', 0)), 'expandedBytesScanned': int(off_metadata.get('expandedBytes', 0)), 'recordsExamined': off_metadata.get('recordsExamined'), 'germanyRecordsEmitted': off_metadata.get('recordsEmitted'), 'sourceSchemaVersions': off_metadata.get('sourceSchemaVersions'), 'expectedProductSchemaVersion': off_metadata.get('expectedProductSchemaVersion'), 'apiVersion': off_metadata.get('apiVersion'), 'tagSchema': off_metadata.get('tagSchema'), 'selectionPolicyVersion': off_quality.get('selectionPolicyVersion'), 'sourcePolicySha256': digest_file(args.off_source_policy), 'selectionPolicySha256': digest_file(args.selection_policy)}, 'openPrices': {'snapshotID': open_prices_metadata.get('snapshotID'), 'retrievedAt': open_prices_metadata.get('retrievedAt'), 'upstreamCompressedBytes': open_prices_compressed, 'projectedPayloadBytes': int(open_prices_metadata.get('payloadBytes', 0)), 'upstreamExports': open_prices_metadata.get('upstreamExports'), 'aliasVersion': open_prices_quality.get('aliasVersion'), 'sourcePolicySha256': digest_file(args.open_prices_source_policy), 'retailerAliasesSha256': digest_file(args.retailer_aliases), 'noCompletenessClaim': True}}, 'selection': off_selection.get('report', {}), 'realCatalog': {'uniqueValidSelectedGTINs': len(rows), 'ingredientMetrics': ingredient_metrics(rows), 'openPricesGermanyObservationRows': len(all_open_prices), 'openPricesGermanyUniqueGTINs': len({row['gtin'] for row in all_open_prices}), 'retailerObservationRowsForSelectedProducts': len(selected_retailers), 'retailerSummaryRows': len(current_retailers), 'retailerCounts': dict(sorted(Counter((str(row['retailerKey']) for row in selected_retailers)).items())), 'basicExclusionRows': len(basic), 'commonSemanticSha256': semantic_hash, 'auditRoundTripSemanticSha256': audit_roundtrip_hash, 'roundTripSemanticSha256': roundtrip_hash}, 'projectionMeasurements': {'rawStaged': {'openFoodFactsCompressedBytes': int(off_metadata.get('transportBytes', 0)), 'openFoodFactsExpandedBytesScanned': int(off_metadata.get('expandedBytes', 0)), 'openPricesCompressedBytes': open_prices_compressed, 'openPricesProjectedPayloadBytes': int(open_prices_metadata.get('payloadBytes', 0)), 'note': 'raw/staged source history remains outside the iOS bundle'}, 'currentAudit': audit, 'minimalRuntime': runtime, 'basicExclusionIndexCost': basic_cost, 'partitionedMarketGrowthModel': [{'marketCount': count, 'assumption': 'independent per-market SQLite catalogs with Germany-equivalent semantic/runtime row distribution', 'vacuumBytes': runtime[0]['vacuumBytes'] * count, 'gzipBytes': runtime[0]['gzipBytes'] * count} for count in (1, 2, 5)], 'semanticRuntimeModel': {'purpose': 'Measure complete runtime assessment/reason/certification/source-metadata overhead without classifying real products.', 'assessmentRowsAreBenchmarkOnly': True, 'missingIngredientsCreateObservationRows': False, 'methodologyVersion': BENCHMARK_METHOD, 'statusModel': 'missing ingredient evidence -> unknown; ingredient-present products cycle all four non-unknown accepted statuses for storage coverage'}}, 'representativeWeeklyRefresh': weekly(rows, selected_retailers, str(off_metadata.get('retrievedAt', ''))), 'licenseBoundary': {'openDataPartition': 'ODbL-compatible Open Food Facts/Open Prices projection', 'futureIncompatibleOfficialFeeds': 'separate catalog partition unless legal review permits combination', 'unauthorizedRetailerScrapingUsed': False, 'productImageBinariesIncluded': False}, 'measurementEnvironment': {'platform': os.uname().sysname, 'machine': os.uname().machine, 'sqliteVersion': sqlite3.sqlite_version, 'pythonVersion': os.sys.version.split()[0], 'peakProcessRSSKiB': int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)}, 'interpretationGuardrails': ['2x/5x extra rows are storage/index models, not real products.', 'Weekly refresh is a single-snapshot seven-day timestamp proxy, not a two-snapshot delta.', 'Open Prices observations do not imply retailer inventory completeness or formulation freshness.', 'Benchmark product assessments, reasons and certifications are schema-overhead models only and are not halal classifications of real source products.', 'Products missing ingredient evidence keep a NULL ingredient pointer; no empty or synthetic ingredient observation is fabricated.', 'Product image binaries are not acquired or stored in any runtime projection.']}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2) + '\n', encoding='utf-8')
    return report

def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    for flag in ('off-evidence', 'off-selection', 'off-quality', 'off-metadata', 'open-prices-evidence', 'open-prices-quality', 'open-prices-metadata', 'review-fixture', 'off-source-policy', 'open-prices-source-policy', 'selection-policy', 'retailer-aliases', 'work-dir', 'report'):
        result.add_argument('--' + flag, type=Path, required=True)
    return result

def main() -> None:
    report = run(parser().parse_args())
    one = report['projectionMeasurements']['minimalRuntime'][0]
    print(json.dumps({'selectedGTINs': report['realCatalog']['uniqueValidSelectedGTINs'], 'runtimeBytes': one['vacuumBytes'], 'runtimeGzipBytes': one['gzipBytes'], 'warmP95Ms': one['warmLookupMs']['p95'], 'detailP95Ms': one['productDetailLookupMs']['p95'], 'firstOpenP95Ms': one['firstLookupAfterOpenMs']['p95']}, sort_keys=True, separators=(',', ':')))

if __name__ == '__main__':
    main()
