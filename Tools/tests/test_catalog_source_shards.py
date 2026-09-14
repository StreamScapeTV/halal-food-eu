from __future__ import annotations
import csv
import gzip
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import catalog_source_shards as module

ROOT = Path(__file__).resolve().parents[2]


def valid_gtin(seed: int) -> str:
    body = f"{seed:013d}"
    total = sum((3 if i % 2 == 0 else 1) * int(ch) for i, ch in enumerate(body))
    return body + str((10 - total % 10) % 10)


def base_row(gtin: str) -> dict[str, str]:
    row = {key: "" for key in module.CSV_COLUMNS}
    row.update({
        "gtin": gtin,
        "original_barcode": gtin.lstrip("0") or "0",
        "market": "DE",
        "brand": "Test",
        "product_name": f"Product {gtin}",
        "identity_confidence": "medium",
        "identity_source_key": "open-food-facts",
        "identity_source_record_id": gtin,
        "identity_retrieved_at": "2026-09-02T20:24:55Z",
        "ingredients_text": "water, salt",
        "ingredients_language": "en",
        "ingredients_source_key": "open-food-facts",
        "ingredients_source_record_id": gtin,
        "ingredients_retrieved_at": "2026-09-02T20:24:55Z",
        "retailer_kind": "community-store-report",
        "retailer_key": "lidl-de",
        "retailer_confidence": "low",
        "retailer_source_key": "open-food-facts",
        "retailer_source_record_id": f"gtin:{gtin}:community-store-report",
        "retailer_retrieved_at": "2026-09-02T20:24:55Z",
        "retailer_scope": "community-reported Lidl association",
        "retailer_limitations": "Community metadata only; not current stock.",
    })
    return row


def write_source_set(root: Path, rows: list[dict[str, str]], *, bucket_count: int, compression: str = "none", shard_order: list[int] | None = None) -> Path:
    bundle = root / "Data/catalog/bundled/de"
    shard_dir = bundle / "shards"
    shard_dir.mkdir(parents=True)
    by_bucket = [[] for _ in range(bucket_count)]
    for row in rows:
        by_bucket[module._bucket_for(row["gtin"], bucket_count)].append(row)
    shard_entries = []
    for bucket, bucket_rows in enumerate(by_bucket):
        bucket_rows.sort(key=lambda item: item["gtin"])
        csv_path = shard_dir / f"products-{bucket:02d}.csv"
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=module.CSV_COLUMNS, lineterminator="\n")
            writer.writeheader(); writer.writerows(bucket_rows)
        if compression == "gzip":
            raw = csv_path.read_bytes()
            path = csv_path.with_suffix(".csv.gz")
            with path.open("wb") as handle:
                with gzip.GzipFile(filename="", mode="wb", fileobj=handle, mtime=0, compresslevel=9) as gz:
                    gz.write(raw)
            csv_path.unlink()
        elif compression == "xz":
            raw = csv_path.read_bytes()
            path = csv_path.with_suffix(".csv.xz")
            path.write_bytes(lzma.compress(raw, preset=6))
            csv_path.unlink()
        else:
            path = csv_path
        shard_entries.append({
            "bucket": bucket, "path": f"shards/{path.name}", "compression": compression,
            "recordCount": len(bucket_rows), "byteCount": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        })
    if shard_order is not None:
        shard_entries = [shard_entries[index] for index in shard_order]
    manifest = {
        "schemaVersion": 1, "csvSchemaVersion": 1, "datasetID": "test-source", "market": "DE",
        "catalogVersion": "test.1", "selectionPolicyVersion": "1.0.0", "generatedAt": "2026-09-14T00:00:00Z",
        "columns": module.CSV_COLUMNS,
        "selectionPolicy": {"path": "Data/selection/catalog-selection-policy-v1.json", "sha256": module.file_sha256(ROOT / "Data/selection/catalog-selection-policy-v1.json")},
        "qualityPolicy": {"path": "Data/quality/catalog-quality-policy-v1.json", "sha256": module.file_sha256(ROOT / "Data/quality/catalog-quality-policy-v1.json")},
        "primaryQualitySourceKey": "open-food-facts",
        "sources": [{
            "sourceKey": "open-food-facts", "policyPath": "Data/sources/open-food-facts/source-policy-v1.json",
            "policySha256": module.file_sha256(ROOT / "Data/sources/open-food-facts/source-policy-v1.json"),
            "snapshotID": "test-snapshot", "revision": "test", "reference": "https://static.openfoodfacts.org/test.jsonl.gz",
            "retrievedAt": "2026-09-02T20:24:55Z",
        }],
        "shardStrategy": {"kind": "sha256-mod", "bucketCount": bucket_count, "key": "gtin"},
        "shards": shard_entries, "recordCount": len(rows), "logicalSha256": module.logical_sha256(rows),
    }
    manifest_path = bundle / "source-manifest-v1.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest_path


class CatalogSourceShardTests(unittest.TestCase):
    def test_single_and_multi_shard_have_identical_logical_evidence(self):
        rows = [base_row(valid_gtin(i)) for i in range(100, 106)]
        with tempfile.TemporaryDirectory() as one, tempfile.TemporaryDirectory() as many:
            a = module.validate_source_set(write_source_set(Path(one), rows, bucket_count=1))
            b = module.validate_source_set(write_source_set(Path(many), rows, bucket_count=4, compression="gzip"))
            self.assertEqual(module.logical_sha256(a.rows), module.logical_sha256(b.rows))
            self.assertEqual(module.canonical_json(module.evidence_envelope(a)), module.canonical_json(module.evidence_envelope(b)))

    def test_tampered_shard_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_source_set(Path(tmp), [base_row(valid_gtin(101))], bucket_count=1)
            shard = next((path.parent / "shards").glob("*.csv")); shard.write_text(shard.read_text() + "x", encoding="utf-8")
            with self.assertRaises(module.CatalogSourceError): module.validate_source_set(path)

    def test_invalid_gtin_is_rejected(self):
        row = base_row(valid_gtin(101)); row["gtin"] = row["gtin"][:-1] + str((int(row["gtin"][-1]) + 1) % 10)
        with tempfile.TemporaryDirectory() as tmp:
            path = write_source_set(Path(tmp), [row], bucket_count=1)
            with self.assertRaises(module.CatalogSourceError): module.validate_source_set(path)

    def test_no_gtin_is_rejected(self):
        row = base_row(valid_gtin(101)); row["gtin"] = ""
        with tempfile.TemporaryDirectory() as tmp:
            # Write manually into bucket zero because bucketing empty is irrelevant to validation under test.
            row["gtin"] = valid_gtin(101); path = write_source_set(Path(tmp), [row], bucket_count=1)
            shard = next((path.parent / "shards").glob("*.csv"))
            text = shard.read_text(); text = text.replace(valid_gtin(101), "", 1); shard.write_text(text)
            manifest=json.loads(path.read_text()); manifest["shards"][0]["byteCount"]=shard.stat().st_size; manifest["shards"][0]["sha256"]=module.file_sha256(shard); path.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
            with self.assertRaises(module.CatalogSourceError): module.validate_source_set(path)

    def test_missing_product_name_is_rejected(self):
        row = base_row(valid_gtin(101)); row["product_name"] = ""
        with tempfile.TemporaryDirectory() as tmp:
            path = write_source_set(Path(tmp), [row], bucket_count=1)
            with self.assertRaises(module.CatalogSourceError): module.validate_source_set(path)

    def test_manifest_shard_order_does_not_change_logical_evidence(self):
        rows = [base_row(valid_gtin(i)) for i in range(100, 120)]
        with tempfile.TemporaryDirectory() as a_tmp, tempfile.TemporaryDirectory() as b_tmp:
            a = module.validate_source_set(write_source_set(Path(a_tmp), rows, bucket_count=4, compression="gzip"))
            b = module.validate_source_set(write_source_set(Path(b_tmp), rows, bucket_count=4, compression="gzip", shard_order=[3,1,0,2]))
            self.assertEqual(module.logical_sha256(a.rows), module.logical_sha256(b.rows))
            self.assertEqual(module.canonical_json(module.evidence_envelope(a)), module.canonical_json(module.evidence_envelope(b)))

    def test_duplicate_gtin_is_rejected(self):
        row = base_row(valid_gtin(101))
        with tempfile.TemporaryDirectory() as tmp:
            path = write_source_set(Path(tmp), [row, dict(row)], bucket_count=1)
            with self.assertRaises(module.CatalogSourceError): module.validate_source_set(path)

    def test_non_odbl_policy_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_source_set(Path(tmp), [base_row(valid_gtin(101))], bucket_count=1)
            manifest=json.loads(path.read_text())
            fake=ROOT/'Data/catalog/bundled/de/test-non-odbl-policy.json'
            fake.parent.mkdir(parents=True,exist_ok=True)
            fake.write_text(json.dumps({"sourceKey":"open-food-facts","accessMethod":"public-bulk","databaseLicense":{"identifier":"Proprietary"},"attribution":"test"}))
            try:
                manifest["sources"][0]["policyPath"] = fake.relative_to(ROOT).as_posix(); manifest["sources"][0]["policySha256"] = module.file_sha256(fake); path.write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n")
                with self.assertRaises(module.CatalogSourceError): module.validate_source_set(path)
            finally: fake.unlink(missing_ok=True)

    def test_repository_source_set_is_valid(self):
        source = module.validate_source_set(ROOT / "Data/catalog/bundled/de/source-manifest-v1.json")
        self.assertEqual(len(source.rows), 7440)
        self.assertEqual(len(source.manifest["shards"]), 32)
        self.assertEqual(source.manifest["logicalSha256"], "b272c4bc1cf82f5e10b3afcd4c468a07099b0895fb8d64defae5968b9e68f0a7")


if __name__ == "__main__": unittest.main()
