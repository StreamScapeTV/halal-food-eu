from __future__ import annotations

import io
import json
import sys
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import open_food_facts_acquire as ACQUIRE
import open_food_facts_common as COMMON


class OpenFoodFactsAcquisitionHardeningTests(unittest.TestCase):
    def test_redirect_handler_rejects_non_https_and_non_allowlisted_targets(self):
        handler = ACQUIRE._AllowlistedRedirectHandler(("static.openfoodfacts.org",))
        request = urllib.request.Request(
            "https://static.openfoodfacts.org/data/openfoodfacts-products.jsonl.gz"
        )
        for target in (
            "http://static.openfoodfacts.org/data/export.jsonl.gz",
            "https://127.0.0.1/internal",
            "https://example.invalid/export.jsonl.gz",
            "https://user:password@static.openfoodfacts.org/export.jsonl.gz",
        ):
            with self.subTest(target=target):
                with self.assertRaisesRegex(COMMON.AdapterError, "redirect target"):
                    handler.redirect_request(request, io.BytesIO(), 302, "Found", {}, target)

    def test_json_line_reader_bounds_expanded_bytes(self):
        record = json.dumps({"code": "4006381333931"}).encode("utf-8") + b"\n"
        counters = ACQUIRE.Counters()
        with self.assertRaisesRegex(COMMON.AdapterError, "expanded export exceeded"):
            list(
                ACQUIRE._iter_json_lines(
                    io.BytesIO(record + record),
                    counters,
                    max_expanded_bytes=len(record),
                    max_records=10,
                )
            )

    def test_json_line_reader_bounds_logical_records(self):
        record = json.dumps({"code": "4006381333931"}).encode("utf-8") + b"\n"
        counters = ACQUIRE.Counters()
        with self.assertRaisesRegex(COMMON.AdapterError, "logical records"):
            list(
                ACQUIRE._iter_json_lines(
                    io.BytesIO(record + record),
                    counters,
                    max_expanded_bytes=1024,
                    max_records=1,
                )
            )

    def test_oversized_line_is_drained_in_bounded_chunks_and_counted_once(self):
        oversized = b"x" * (ACQUIRE.MAX_LINE_BYTES + 32) + b"\n"
        valid = json.dumps({"code": "4006381333931"}).encode("utf-8") + b"\n"
        counters = ACQUIRE.Counters()
        records = list(
            ACQUIRE._iter_json_lines(
                io.BytesIO(oversized + valid),
                counters,
                max_expanded_bytes=len(oversized) + len(valid),
                max_records=2,
            )
        )
        self.assertEqual(records, [{"code": "4006381333931"}])
        self.assertEqual(counters.oversized, 1)
        self.assertEqual(counters.malformed, 1)
        self.assertEqual(counters.lines_seen, 2)
        self.assertEqual(counters.expanded_bytes, len(oversized) + len(valid))


if __name__ == "__main__":
    unittest.main()
