import copy
import json
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import catalog_selection_contract as CONTRACT
import catalog_selection_engine as MODULE

POLICY_PATH = ROOT / "Data" / "selection" / "catalog-selection-policy-v1.json"
INPUT_PATH = ROOT / "Data" / "selection" / "sample-selection-candidates-v1.json"
POLICY_SCHEMA_PATH = ROOT / "Data" / "selection" / "catalog-selection-policy-v1.schema.json"
INPUT_SCHEMA_PATH = ROOT / "Data" / "selection" / "selection-candidates-v1.schema.json"


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def output_by_source(result):
    selected = {item["sourceRecordID"]: ("include-detailed", item["reasonCode"]) for item in result["selected"]}
    invalid = {item["sourceRecordID"]: ("exclude-invalid", item["reasonCode"]) for item in result["invalidExclusions"]}
    # Basic exclusion index is intentionally source-ID free; recover by GTIN from fixture.
    fixture = load(INPUT_PATH)
    source_by_gtin = {}
    for candidate in fixture["candidates"]:
        gtin = MODULE.normalize_gtin(candidate["barcode"])
        if gtin:
            source_by_gtin[gtin] = candidate["sourceRecordID"]
    basic = {
        source_by_gtin[item["gtin"]]: ("exclude-basic", item["reasonCode"])
        for item in result["basicExclusions"]
    }
    return selected | invalid | basic


class ContractTests(unittest.TestCase):
    def test_committed_policy_and_fixture_validate(self):
        MODULE.validate_policy(load(POLICY_PATH))
        MODULE.validate_bundle(load(INPUT_PATH))

    def test_json_schema_field_sets_match_semantic_contract(self):
        policy_schema = load(POLICY_SCHEMA_PATH)
        input_schema = load(INPUT_SCHEMA_PATH)

        required, optional = CONTRACT.POLICY_FIELDS
        self.assertEqual(set(policy_schema["properties"]), required | optional)
        self.assertEqual(set(policy_schema["required"]), required)

        mappings = [
            (CONTRACT.BUNDLE_FIELDS, input_schema, "$input"),
            (CONTRACT.SOURCE_SNAPSHOT_FIELDS, input_schema["$defs"]["sourceSnapshot"], "sourceSnapshot"),
            (CONTRACT.CANDIDATE_FIELDS, input_schema["$defs"]["candidate"], "candidate"),
            (CONTRACT.REMOTE_IMAGE_FIELDS, input_schema["$defs"]["remoteImage"], "remoteImage"),
            (CONTRACT.BASIC_RULE_FIELDS, policy_schema["$defs"]["basicRule"], "basicRule"),
        ]
        for fields, schema, name in mappings:
            required, optional = fields
            self.assertEqual(set(schema["properties"]), required | optional, name)
            self.assertEqual(set(schema["required"]), required, name)

    def test_source_snapshot_timestamp_must_be_real_and_timezone_aware(self):
        bundle = load(INPUT_PATH)
        bundle["sourceSnapshot"]["retrievedAt"] = "2026-99-99T00:00:00Z"
        with self.assertRaisesRegex(CONTRACT.SelectionValidationError, "invalid ISO-8601 timestamp"):
            MODULE.validate_bundle(bundle)

        bundle = load(INPUT_PATH)
        bundle["sourceSnapshot"]["retrievedAt"] = "2026-08-29T10:00:00"
        with self.assertRaisesRegex(CONTRACT.SelectionValidationError, "explicit timezone"):
            MODULE.validate_bundle(bundle)

    def test_future_schema_versions_fail_closed(self):
        policy = load(POLICY_PATH)
        policy["schemaVersion"] = 2
        with self.assertRaisesRegex(CONTRACT.SelectionValidationError, "unsupported schema version"):
            MODULE.validate_policy(policy)

        bundle = load(INPUT_PATH)
        bundle["schemaVersion"] = 2
        with self.assertRaisesRegex(CONTRACT.SelectionValidationError, "unsupported schema version"):
            MODULE.validate_bundle(bundle)

    def test_remote_images_are_https_metadata_only(self):
        bundle = load(INPUT_PATH)
        snack = next(item for item in bundle["candidates"] if item["sourceRecordID"] == "snack-image")
        snack["remoteImages"][0]["bytes"] = "base64-not-allowed"
        with self.assertRaisesRegex(CONTRACT.SelectionValidationError, "unknown fields"):
            MODULE.validate_bundle(bundle)

        bundle = load(INPUT_PATH)
        snack = next(item for item in bundle["candidates"] if item["sourceRecordID"] == "snack-image")
        snack["remoteImages"][0]["url"] = "data:image/jpeg;base64,AAAA"
        with self.assertRaisesRegex(CONTRACT.SelectionValidationError, "HTTPS"):
            MODULE.validate_bundle(bundle)

        bundle = load(INPUT_PATH)
        snack = next(item for item in bundle["candidates"] if item["sourceRecordID"] == "snack-image")
        snack["remoteImages"][0]["purpose"] = "marketing"
        with self.assertRaisesRegex(CONTRACT.SelectionValidationError, "unsupported image purpose"):
            MODULE.validate_bundle(bundle)


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.policy = load(POLICY_PATH)
        self.bundle = load(INPUT_PATH)
        self.result = MODULE.evaluate_bundle(self.policy, self.bundle)
        self.decisions = output_by_source(self.result)

    def test_basic_whole_foods_are_excluded_only_by_explicit_rules(self):
        for source in ("apple", "cucumber", "tomato"):
            self.assertEqual(self.decisions[source], ("exclude-basic", "basic-fresh-produce"))
        self.assertEqual(self.decisions["plain-milk"], ("exclude-basic", "basic-plain-milk"))
        self.assertEqual(self.decisions["plain-water"], ("exclude-basic", "basic-plain-water"))

    def test_processed_and_high_value_products_override_basic_ancestors(self):
        self.assertEqual(self.decisions["flavoured-milk"][0], "include-detailed")
        self.assertEqual(self.decisions["flavoured-water"][0], "include-detailed")
        self.assertEqual(self.decisions["apple-sauce"][0], "include-detailed")
        self.assertEqual(self.decisions["bread-enzymes"][0], "include-detailed")
        self.assertEqual(self.decisions["bakery-missing-ingredients"][0], "include-detailed")

    def test_missing_ingredients_and_unknown_category_fail_open_to_detailed_catalog(self):
        self.assertEqual(
            self.decisions["unknown-category"],
            ("include-detailed", "conservative-unknown"),
        )
        selected = next(item for item in self.result["selected"] if item["sourceRecordID"] == "unknown-category")
        self.assertNotIn("ingredientsText", selected)

    def test_plain_milk_without_single_ingredient_evidence_stays_detailed(self):
        self.assertEqual(
            self.decisions["plain-milk-missing-ingredients"],
            ("include-detailed", "conservative-unknown"),
        )

    def test_review_evidence_overrides_basic_but_retailer_presence_alone_does_not(self):
        self.assertEqual(
            self.decisions["fresh-herb-with-retailer-evidence"],
            ("exclude-basic", "basic-fresh-produce"),
        )
        self.assertEqual(
            self.decisions["fresh-herb-with-review-evidence"],
            ("include-detailed", "existing-evidence"),
        )

    def test_invalid_reasons_are_distinct(self):
        self.assertEqual(self.decisions["non-food"], ("exclude-invalid", "non-food"))
        self.assertEqual(
            self.decisions["invalid-barcode"],
            ("exclude-invalid", "invalid-or-unsupported-barcode"),
        )
        self.assertEqual(
            self.decisions["source-assigned-no-barcode"],
            ("exclude-invalid", "source-assigned-no-barcode"),
        )
        self.assertEqual(self.decisions["wrong-market"], ("exclude-invalid", "wrong-market"))

    def test_basic_exclusion_index_is_minimal_and_never_a_halal_verdict(self):
        expected_keys = {"gtin", "market", "policyVersion", "reasonCode"}
        for record in self.result["basicExclusions"]:
            self.assertEqual(set(record), expected_keys)
        encoded = MODULE.canonical_json(self.result["basicExclusions"])
        for forbidden in ("halal-certified", "halal-reviewed", "ingredientsText", "remoteImages"):
            self.assertNotIn(forbidden, encoded)

    def test_remote_image_reference_is_preserved_without_download_payload(self):
        snack = next(item for item in self.result["selected"] if item["sourceRecordID"] == "snack-image")
        self.assertEqual(len(snack["remoteImages"]), 1)
        image = snack["remoteImages"][0]
        self.assertEqual(image["url"], "https://images.example.invalid/products/snack-front.jpg")
        self.assertEqual(set(image), {"purpose", "url", "sourceKey", "imageID", "revision", "width", "height"})

    def test_normalizes_supported_barcode_lengths_to_gtin14(self):
        self.assertEqual(MODULE.normalize_gtin("4012345678901"), "04012345678901")
        self.assertIsNone(MODULE.normalize_gtin("1234567890123"))


class DeterminismAndReportingTests(unittest.TestCase):
    def test_evaluation_is_independent_of_candidate_and_signal_order(self):
        policy = load(POLICY_PATH)
        bundle = load(INPUT_PATH)
        expected = MODULE.canonical_json(MODULE.evaluate_bundle(policy, bundle))

        shuffled = copy.deepcopy(bundle)
        rng = random.Random(36)
        rng.shuffle(shuffled["candidates"])
        for candidate in shuffled["candidates"]:
            for field in (
                "categoryTags",
                "categorySignals",
                "formulationSignals",
                "evidenceSignals",
                "retailerKeys",
                "packageSignals",
            ):
                if field in candidate:
                    rng.shuffle(candidate[field])
            rng.shuffle(candidate["remoteImages"])
        actual = MODULE.canonical_json(MODULE.evaluate_bundle(policy, shuffled))
        self.assertEqual(actual, expected)

    def test_report_contains_required_metrics_and_deterministic_sample(self):
        result = MODULE.evaluate_bundle(load(POLICY_PATH), load(INPUT_PATH))
        report = result["report"]
        self.assertEqual(report["sourceRecordsExamined"], 19)
        self.assertEqual(report["germanyRelevantCandidates"], 15)
        self.assertEqual(report["includedProducts"], 9)
        self.assertEqual(report["excludedBasicProducts"], 6)
        self.assertEqual(report["excludedInvalidRecords"], 4)
        self.assertEqual(report["includedWithIngredients"], 5)
        self.assertEqual(report["includedMissingIngredients"], 4)
        self.assertGreater(report["logicalDetailedPayloadBytes"], report["logicalBasicExclusionIndexBytes"])
        self.assertEqual(
            report["excludedBasicSample"],
            MODULE.evaluate_bundle(load(POLICY_PATH), load(INPUT_PATH))["report"]["excludedBasicSample"],
        )

    def test_policy_comparison_reports_decision_changes(self):
        current = load(POLICY_PATH)
        previous = copy.deepcopy(current)
        previous["policyVersion"] = "0.9.0"
        water_rule = next(rule for rule in previous["basicRules"] if rule["code"] == "basic-plain-water")
        water_rule["maxIngredientCount"] = 0
        result = MODULE.evaluate_bundle(current, load(INPUT_PATH), previous_policy_data=previous)
        comparison = result["comparison"]
        self.assertEqual(comparison["previousPolicyVersion"], "0.9.0")
        changed_ids = {item["sourceRecordID"] for item in comparison["decisionChanges"]}
        self.assertIn("plain-water", changed_ids)
        self.assertEqual(comparison["excludedBasicDelta"], 1)
        self.assertEqual(comparison["includedDelta"], -1)


if __name__ == "__main__":
    unittest.main()
