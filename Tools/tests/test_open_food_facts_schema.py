from __future__ import annotations

import gzip
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import open_food_facts_acquire as ACQUIRE
import open_food_facts_common as COMMON

SOURCE_POLICY = ROOT / "Data/sources/open-food-facts/source-policy-v1.json"
RETRIEVED_AT = "2026-08-30T00:00:00Z"


class OpenFoodFactsSchemaCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.policy = COMMON.load_source_policy(SOURCE_POLICY)

    def test_full_export_skips_unsupported_schema_records_and_reports_them(self):
        supported = {
            "code": "4006381333931",
            "schema_version": 1004,
            "product_type": "food",
            "product_name": "Supported snack",
            "countries_tags": ["en:germany"],
            "categories_tags": ["en:snacks"],
            "ingredients_text_de": "Mehl, Zucker",
            "ingredients_n": 2,
        }
        unsupported = dict(supported)
        unsupported["code"] = "4006381333948"
        unsupported["schema_version"] = 1003
        payload = (
            json.dumps(supported, separators=(",", ":"))
            + "\n"
            + json.dumps(unsupported, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        compressed = gzip.compress(payload, mtime=0)
        headers = {"content-length": str(len(compressed))}

        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "snapshot.jsonl"
            with (
                mock.patch.object(ACQUIRE, "MIN_FULL_COMPRESSED_BYTES", 1),
                mock.patch.object(
                    ACQUIRE,
                    "_open_network_export",
                    return_value=(io.BytesIO(compressed), headers, 200),
                ),
            ):
                metadata = ACQUIRE.acquire(
                    output=snapshot,
                    snapshot_id="mixed-schema-full",
                    mode="full",
                    policy=self.policy,
                    user_agent="HalalFoodEU/1.0 (tests@example.invalid)",
                    retrieved_at=RETRIEVED_AT,
                )
            lines = [json.loads(line) for line in snapshot.read_text(encoding="utf-8").splitlines()]

        self.assertEqual(metadata["recordsExamined"], 2)
        self.assertEqual(metadata["recordsEmitted"], 1)
        self.assertEqual(metadata["unsupportedSchemaRecords"], 1)
        self.assertEqual(metadata["sourceSchemaVersions"], {"1003": 1, "1004": 1})
        self.assertEqual(lines[0]["code"], supported["code"])
        self.assertEqual(len(lines), 2)

    def test_fixture_with_unsupported_schema_fails_closed_without_publishing_output(self):
        record = {
            "code": "4006381333931",
            "schema_version": 1003,
            "product_type": "food",
            "product_name": "Old schema",
            "countries_tags": ["en:germany"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "fixture.jsonl"
            fixture.write_text(json.dumps(record) + "\n", encoding="utf-8")
            output = root / "snapshot.jsonl"
            with self.assertRaisesRegex(COMMON.AdapterError, "unsupported schema"):
                ACQUIRE.acquire(
                    output=output,
                    snapshot_id="old-schema-fixture",
                    mode="fixture",
                    policy=self.policy,
                    fixture=fixture,
                    retrieved_at=RETRIEVED_AT,
                )
            self.assertFalse(output.exists())
            self.assertFalse((root / "snapshot.jsonl.tmp").exists())


if __name__ == "__main__":
    unittest.main()
