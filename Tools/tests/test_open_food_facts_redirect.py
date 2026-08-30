from __future__ import annotations

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

SOURCE_POLICY = ROOT / "Data/sources/open-food-facts/source-policy-v1.json"
STATIC_EXPORT = "https://static.openfoodfacts.org/data/openfoodfacts-products.jsonl.gz"
CURRENT_STORAGE = "https://openfoodfacts-ds.s3.eu-west-3.amazonaws.com/openfoodfacts-products.jsonl.gz"


class OpenFoodFactsRedirectPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = COMMON.load_source_policy(SOURCE_POLICY)
        self.handler = ACQUIRE._AllowlistedRedirectHandler(self.policy.acquisition_hosts)
        self.request = urllib.request.Request(STATIC_EXPORT)

    def test_current_exact_bulk_storage_redirect_is_admitted(self) -> None:
        self.assertEqual(self.policy.export_url, STATIC_EXPORT)
        self.assertIn("static.openfoodfacts.org", self.policy.acquisition_hosts)
        self.assertIn("openfoodfacts-ds.s3.eu-west-3.amazonaws.com", self.policy.acquisition_hosts)
        redirected = self.handler.redirect_request(
            self.request,
            None,
            302,
            "Found",
            {},
            CURRENT_STORAGE,
        )
        self.assertIsNotNone(redirected)
        assert redirected is not None
        self.assertEqual(redirected.full_url, CURRENT_STORAGE)

    def test_arbitrary_s3_redirect_remains_rejected(self) -> None:
        with self.assertRaisesRegex(COMMON.AdapterError, "not an admitted HTTPS OFF host"):
            self.handler.redirect_request(
                self.request,
                None,
                302,
                "Found",
                {},
                "https://attacker-bucket.s3.eu-west-3.amazonaws.com/openfoodfacts-products.jsonl.gz",
            )

    def test_credentials_and_non_https_remain_rejected(self) -> None:
        for target in (
            "http://openfoodfacts-ds.s3.eu-west-3.amazonaws.com/openfoodfacts-products.jsonl.gz",
            "https://user:pass@openfoodfacts-ds.s3.eu-west-3.amazonaws.com/openfoodfacts-products.jsonl.gz",
        ):
            with self.subTest(target=target):
                with self.assertRaisesRegex(COMMON.AdapterError, "not an admitted HTTPS OFF host"):
                    self.handler.redirect_request(self.request, None, 302, "Found", {}, target)


class OpenFoodFactsCandidateNormalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = COMMON.load_source_policy(SOURCE_POLICY)

    def test_punctuation_only_packaging_tags_do_not_emit_blank_package_signals(self) -> None:
        candidate = COMMON.record_to_candidate(
            {
                "code": "00200000000004",
                "countries_tags": ["en:germany"],
                "packaging_tags": ["---", "en:carton"],
            },
            self.policy,
        )
        self.assertEqual(candidate["packageSignals"], ["en-carton"])

    def test_only_punctuation_packaging_tags_omit_package_signals(self) -> None:
        candidate = COMMON.record_to_candidate(
            {
                "code": "00200000000004",
                "countries_tags": ["en:germany"],
                "packaging_tags": ["---", ":::", "..."],
            },
            self.policy,
        )
        self.assertNotIn("packageSignals", candidate)


if __name__ == "__main__":
    unittest.main()
