from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import open_prices_common as common
from open_prices_acquire import acquire
from open_prices_normalize import normalize_snapshot


class OpenPricesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.policy = common.load_source_policy(ROOT / common.DEFAULT_SOURCE_POLICY)
        self.aliases = common.load_alias_registry(ROOT / common.DEFAULT_ALIAS_REGISTRY)

    def _fixture(self, temp: Path, snapshot_id: str = "op-test") -> tuple[Path, Path]:
        snapshot = temp / "source.jsonl"
        metadata = temp / "metadata.json"
        acquire(
            output=snapshot,
            metadata_output=metadata,
            snapshot_id=snapshot_id,
            mode="fixture",
            policy=self.policy,
            fixtures={key: ROOT / path for key, path in common.DEFAULT_FIXTURES.items()},
            retrieved_at="2026-08-30T00:00:00Z",
        )
        return snapshot, metadata

    def test_policy_is_odbl_observational_and_proof_bytes_are_forbidden(self) -> None:
        raw = self.policy.raw
        self.assertEqual(raw["databaseLicense"]["identifier"], "ODbL")
        self.assertEqual(raw["evidenceKind"], "retailer-observation")
        self.assertFalse(raw["proofBinaryPolicy"]["downloadBinaries"])
        self.assertFalse(raw["proofBinaryPolicy"]["redistributeBinaries"])
        self.assertFalse(raw["completenessClaimAllowed"])

    def test_workflow_registry_and_ci_admit_open_prices_without_credentials(self) -> None:
        contract = json.loads((ROOT / "Data/workflows/catalog-workflow-contract-v1.json").read_text(encoding="utf-8"))
        source = next(item for item in contract["sourceRegistry"] if item["key"] == "open-prices")
        self.assertFalse(source["credentialsRequired"])
        self.assertEqual(source["sourceClass"], "open-database")
        self.assertEqual(source["accessMethod"], "https-export")
        self.assertEqual(source["allowedHosts"], ["prices.openfoodfacts.org"])
        acquire_workflow = (ROOT / ".github/workflows/acquire-catalog.yml").read_text(encoding="utf-8")
        normalize_workflow = (ROOT / ".github/workflows/normalize-and-diff.yml").read_text(encoding="utf-8")
        quality_workflow = (ROOT / ".github/workflows/catalog-quality.yml").read_text(encoding="utf-8")
        scheduled_workflow = (ROOT / ".github/workflows/scheduled-catalog-refresh.yml").read_text(encoding="utf-8")
        ios_workflow = (ROOT / ".github/workflows/ios-ci.yml").read_text(encoding="utf-8")
        self.assertIn('SOURCE_KEY" == "open-prices"', acquire_workflow)
        self.assertIn('SOURCE_KEY" == "open-prices"', normalize_workflow)
        self.assertIn('SOURCE_KEY"] == "open-prices"', quality_workflow)
        self.assertIn('cron: "41 3 * * 5"', scheduled_workflow)
        self.assertIn("Validate Open Prices retailer-observation adapter", ios_workflow)

    def test_gtin_validation_preserves_canonical_leading_zeroes(self) -> None:
        self.assertEqual(common.canonical_gtin("0200000000028"), "00200000000028")
        self.assertEqual(common.canonical_gtin("0200000000004"), "00200000000004")
        with self.assertRaises(common.AdapterError):
            common.canonical_gtin("0200000000029")

    def test_stable_aliases_match_rewe_lidl_and_generic_aldi_is_not_guessed(self) -> None:
        rewe = {"osm_brand": "REWE", "osm_tag_value": "REWE", "osm_name": "Some REWE"}
        lidl = {"osm_brand": "Lidl", "osm_tag_value": "Lidl", "osm_name": "Lidl"}
        aldi = {"osm_brand": "ALDI", "osm_tag_value": "ALDI", "osm_name": "ALDI"}
        self.assertEqual(common.match_retailer(rewe, self.aliases)[0], "rewe")
        self.assertEqual(common.match_retailer(lidl, self.aliases)[0], "lidl")
        self.assertEqual(common.match_retailer(aldi, self.aliases)[1], "unmatched")

    def test_fixture_normalizes_only_dated_retailer_observations(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            snapshot, metadata = self._fixture(temp)
            evidence, quality, changes = normalize_snapshot(
                snapshot=snapshot,
                metadata_path=metadata,
                policy=self.policy,
                aliases=self.aliases,
            )
            self.assertEqual({item["retailerKey"] for item in evidence["retailerEvidence"]}, {"rewe", "lidl"})
            self.assertTrue(all(item["kind"] == "retailer-observation" for item in evidence["retailerEvidence"]))
            self.assertEqual(evidence["currentSelections"], [])
            self.assertEqual(evidence["assessments"], [])
            self.assertTrue(quality["observationalOnly"])
            self.assertTrue(changes["noCompletenessClaim"])
            scopes = [item["scope"] for item in evidence["retailerEvidence"]]
            self.assertTrue(any("1.99 EUR" in scope and "per UNIT" in scope for scope in scopes))
            self.assertTrue(any("2.49 EUR" in scope and "per UNIT" in scope for scope in scopes))
            serialized = json.dumps(evidence, sort_keys=True).lower()
            self.assertNotIn("currently stocks", serialized)
            self.assertNotIn("nationwide availability.", serialized.replace("not current stock, nationwide availability", ""))

    def test_acquisition_projection_strips_contributor_coordinates_and_proof_paths(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            snapshot, metadata_path = self._fixture(temp)
            raw = snapshot.read_text(encoding="utf-8")
            self.assertNotIn("owner", raw)
            self.assertNotIn("file_path", raw)
            self.assertNotIn("osm_lat", raw)
            self.assertNotIn("osm_lon", raw)
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertFalse(metadata["proofImageBinariesIncluded"])
            self.assertFalse(metadata["personalContributorFieldsIncluded"])

    def test_missing_join_invalid_gtin_future_date_and_unmatched_retailer_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            snapshot, metadata = self._fixture(temp)
            lines = [json.loads(line) for line in snapshot.read_text(encoding="utf-8").splitlines()]
            prices = [line for line in lines if line["kind"] == "price"]
            bad = dict(prices[0])
            bad["record"] = dict(bad["record"])
            bad["record"]["id"] = 2001
            bad["record"]["product_code"] = "0200000000029"
            lines.append(bad)
            future = dict(prices[0])
            future["record"] = dict(future["record"])
            future["record"]["id"] = 2002
            future["record"]["date"] = "2099-01-01"
            lines.append(future)
            missing = dict(prices[0])
            missing["record"] = dict(missing["record"])
            missing["record"]["id"] = 2003
            missing["record"]["proof_id"] = 999999
            lines.append(missing)
            unknown_location = {
                "kind": "location",
                "record": {"id": 77, "osm_brand": "Unknown Shop", "osm_name": "Unknown Shop", "osm_address_country_code": "DE"},
            }
            unknown_proof = {"kind": "proof", "record": {"id": 177, "location_id": 77, "type": "RECEIPT", "date": "2026-08-20"}}
            unknown_price = {"kind": "price", "record": {"id": 2077, "product_code": "0200000000028", "location_id": 77, "proof_id": 177, "date": "2026-08-20", "type": "PRODUCT"}}
            lines.extend([unknown_location, unknown_proof, unknown_price])
            snapshot.write_text("\n".join(json.dumps(line, sort_keys=True) for line in lines) + "\n", encoding="utf-8")
            meta = json.loads(metadata.read_text(encoding="utf-8"))
            import hashlib
            payload = snapshot.read_bytes()
            meta["payloadSha256"] = hashlib.sha256(payload).hexdigest()
            meta["payloadBytes"] = len(payload)
            metadata.write_text(json.dumps(meta, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            evidence, quality, _ = normalize_snapshot(snapshot=snapshot, metadata_path=metadata, policy=self.policy, aliases=self.aliases)
            self.assertEqual(len(evidence["retailerEvidence"]), 2)
            self.assertEqual(quality["counts"]["invalidGTIN"], 1)
            self.assertEqual(quality["counts"]["invalidOrFutureDate"], 1)
            self.assertEqual(quality["counts"]["missingProof"], 1)
            self.assertEqual(quality["counts"]["unmatchedRetailer"], 1)

    def test_same_snapshot_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            snapshot, metadata = self._fixture(temp)
            first, _, first_change = normalize_snapshot(snapshot=snapshot, metadata_path=metadata, policy=self.policy, aliases=self.aliases)
            previous = temp / "previous.json"
            previous.write_text(json.dumps(first, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            second, _, second_change = normalize_snapshot(snapshot=snapshot, metadata_path=metadata, policy=self.policy, aliases=self.aliases, previous_evidence_path=previous)
            self.assertEqual(first, second)
            self.assertEqual(second_change["additions"], 0)
            self.assertEqual(second_change["unchanged"], 2)
            self.assertEqual(second_change["removals"], 0)
            self.assertEqual(first_change["additions"], 2)


    def test_duplicate_price_ids_do_not_double_count_and_previous_other_sources_are_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            snapshot, metadata = self._fixture(temp)
            lines = snapshot.read_text(encoding="utf-8").splitlines()
            price_line = next(line for line in lines if '"kind":"price"' in line)
            snapshot.write_text("\n".join(lines + [price_line]) + "\n", encoding="utf-8")
            meta = json.loads(metadata.read_text(encoding="utf-8"))
            import hashlib
            payload = snapshot.read_bytes()
            meta["payloadSha256"] = hashlib.sha256(payload).hexdigest()
            meta["payloadBytes"] = len(payload)
            metadata.write_text(json.dumps(meta, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            evidence, quality, _ = normalize_snapshot(snapshot=snapshot, metadata_path=metadata, policy=self.policy, aliases=self.aliases)
            self.assertEqual(len(evidence["retailerEvidence"]), 2)
            self.assertEqual(quality["counts"]["duplicatePriceIDs"], 1)

            previous = temp / "previous.json"
            prior = dict(evidence)
            prior["retailerEvidence"] = list(evidence["retailerEvidence"]) + [{"id": "external-retailer-id", "sourceKey": "other-source"}]
            previous.write_text(json.dumps(prior, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            _, _, changes = normalize_snapshot(snapshot=snapshot, metadata_path=metadata, policy=self.policy, aliases=self.aliases, previous_evidence_path=previous)
            self.assertNotIn("external-retailer-id", changes["removedRetailerEvidenceIDs"])

    def test_partial_snapshot_never_turns_absence_into_removal(self) -> None:
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            snapshot, metadata = self._fixture(temp)
            full, _, _ = normalize_snapshot(snapshot=snapshot, metadata_path=metadata, policy=self.policy, aliases=self.aliases)
            previous = temp / "previous.json"
            previous.write_text(json.dumps(full, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            lines = snapshot.read_text(encoding="utf-8").splitlines()
            partial_lines = [line for line in lines if '"id":1002' not in line]
            snapshot.write_text("\n".join(partial_lines) + "\n", encoding="utf-8")
            meta = json.loads(metadata.read_text(encoding="utf-8"))
            import hashlib
            payload = snapshot.read_bytes()
            meta["downloadComplete"] = False
            meta["mode"] = "sample"
            meta["payloadSha256"] = hashlib.sha256(payload).hexdigest()
            meta["payloadBytes"] = len(payload)
            metadata.write_text(json.dumps(meta, sort_keys=True, indent=2) + "\n", encoding="utf-8")
            _, _, change = normalize_snapshot(snapshot=snapshot, metadata_path=metadata, policy=self.policy, aliases=self.aliases, previous_evidence_path=previous)
            self.assertEqual(change["inputCompleteness"], "partial")
            self.assertEqual(change["removals"], 0)
            self.assertEqual(change["removedRetailerEvidenceIDs"], [])


if __name__ == "__main__":
    unittest.main()
