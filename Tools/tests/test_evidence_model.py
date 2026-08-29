import copy
import importlib.util
import json
import random
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "evidence_model", ROOT / "Tools" / "evidence_model.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

FIXTURE = ROOT / "Data" / "evidence" / "sample-evidence-v1.json"


def load_fixture():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class SchemaParityTests(unittest.TestCase):
    def test_json_schema_record_fields_match_semantic_validator(self):
        schema = json.loads(
            (ROOT / "Data" / "evidence" / "evidence-envelope-v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
        definitions = {
            "sources": "source",
            "identities": "identity",
            "ingredients": "ingredient",
            "retailerEvidence": "retailer",
            "remoteImages": "remoteImage",
            "packageEvidence": "packageEvidence",
            "certifications": "certification",
            "reviews": "review",
            "assessments": "assessment",
            "validityEvents": "validityEvent",
            "currentSelections": "currentSelection",
            "releases": "release",
        }
        for collection, definition in definitions.items():
            required, optional = MODULE.FIELDS[collection]
            schema_definition = schema["$defs"][definition]
            self.assertEqual(
                set(schema_definition["properties"]),
                required | optional,
                f"schema/semantic field mismatch for {collection}",
            )
            self.assertEqual(
                set(schema_definition["required"]),
                required,
                f"schema/semantic required-field mismatch for {collection}",
            )


class EvidenceFixtureTests(unittest.TestCase):
    def test_committed_fixture_validates(self):
        data = load_fixture()
        MODULE.validate_envelope(data)
        self.assertEqual(data["schemaVersion"], 1)
        self.assertEqual(len(data["currentSelections"]), 2)

    def test_runtime_projection_is_deterministic_under_collection_reordering(self):
        baseline = load_fixture()
        expected = MODULE.canonical_json(MODULE.runtime_projection(baseline))

        shuffled = copy.deepcopy(baseline)
        rng = random.Random(42)
        for collection in MODULE.COLLECTIONS:
            rng.shuffle(shuffled[collection])
        rng.shuffle(shuffled["sources"])

        actual = MODULE.canonical_json(MODULE.runtime_projection(shuffled))
        self.assertEqual(actual, expected)

    def test_runtime_projection_excludes_package_submission_payloads(self):
        projection = MODULE.runtime_projection(load_fixture())
        encoded = MODULE.canonical_json(projection)
        self.assertNotIn("packageEvidence", projection)
        self.assertNotIn("review-artifact:synthetic", encoded)
        self.assertNotIn("consentState", encoded)

    def test_multiple_markets_are_distinct(self):
        data = load_fixture()
        identity = copy.deepcopy(data["identities"][0])
        identity["market"] = "FR"
        identity["sourceRecordID"] = "demo-dessert-fr"
        identity["id"] = MODULE.derive_id("identities", identity)
        data["sources"][0]["markets"].append("FR")
        data["identities"].append(identity)
        MODULE.validate_envelope(data)
        self.assertNotEqual(identity["id"], data["identities"][0]["id"])


class IdentifierAndFormulationTests(unittest.TestCase):
    def test_changed_formulation_has_new_hash_and_new_id(self):
        data = load_fixture()
        original = copy.deepcopy(data["ingredients"][1])
        changed = copy.deepcopy(original)
        changed["ingredientsText"] += " Cocoa."
        changed["sourceRevision"] = "formula-v3"
        changed["supersedesID"] = original["id"]
        changed["contentHash"] = MODULE.formulation_hash(changed)
        changed["id"] = MODULE.derive_id("ingredients", changed)

        self.assertNotEqual(changed["contentHash"], original["contentHash"])
        self.assertNotEqual(changed["id"], original["id"])

    def test_cross_market_supersession_is_rejected(self):
        data = load_fixture()
        changed = copy.deepcopy(data["ingredients"][1])
        changed["market"] = "FR"
        changed["sourceRecordID"] = "demo-dessert-fr"
        changed["sourceRevision"] = "formula-fr-v1"
        changed["id"] = MODULE.derive_id("ingredients", changed)
        data["sources"][0]["markets"].append("FR")
        data["ingredients"].append(changed)

        with self.assertRaisesRegex(MODULE.EvidenceValidationError, "another GTIN or market"):
            MODULE.validate_envelope(data)

    def test_canonical_gtin_check_digit_is_enforced(self):
        data = load_fixture()
        bad = data["identities"][0]
        bad["gtin"] = bad["gtin"][:-1] + ("0" if bad["gtin"][-1] != "0" else "1")
        bad["id"] = MODULE.derive_id("identities", bad)

        with self.assertRaisesRegex(MODULE.EvidenceValidationError, "check digit"):
            MODULE.validate_envelope(data)


class FailClosedTests(unittest.TestCase):
    def test_future_schema_version_is_rejected(self):
        data = load_fixture()
        data["schemaVersion"] = 2
        with self.assertRaisesRegex(MODULE.EvidenceValidationError, "unsupported evidence schema"):
            MODULE.validate_envelope(data)

    def test_unknown_retailer_enum_is_rejected(self):
        data = load_fixture()
        record = data["retailerEvidence"][0]
        record["kind"] = "probably-sold-there"
        record["id"] = MODULE.derive_id("retailerEvidence", record)

        with self.assertRaisesRegex(MODULE.EvidenceValidationError, "unsupported value"):
            MODULE.validate_envelope(data)

    def test_image_must_be_https_and_may_not_embed_bytes(self):
        data = load_fixture()
        record = data["remoteImages"][0]
        record["url"] = "http://example.invalid/image.jpg"
        record["id"] = MODULE.derive_id("remoteImages", record)
        with self.assertRaisesRegex(MODULE.EvidenceValidationError, "HTTPS"):
            MODULE.validate_envelope(data)

        data = load_fixture()
        record = data["remoteImages"][0]
        record["bytes"] = "base64-not-allowed"
        with self.assertRaisesRegex(MODULE.EvidenceValidationError, "unknown fields"):
            MODULE.validate_envelope(data)

    def test_current_selection_cannot_resurrect_invalidated_superseded_assessment(self):
        data = load_fixture()
        selection = data["currentSelections"][0]
        old_ingredient = data["ingredients"][0]
        old_assessment = data["assessments"][0]
        selection["ingredientObservationID"] = old_ingredient["id"]
        selection["assessmentID"] = old_assessment["id"]
        selection["id"] = MODULE.derive_id("currentSelections", selection)

        with self.assertRaisesRegex(
            MODULE.EvidenceValidationError,
            "superseded ingredient observation|invalidated",
        ):
            MODULE.validate_envelope(data)

    def test_halal_certified_requires_certificate(self):
        data = load_fixture()
        assessment = data["assessments"][2]
        old_id = assessment["id"]
        assessment["certificationIDs"] = []
        assessment["id"] = MODULE.derive_id("assessments", assessment)
        data["reviews"] = [review for review in data["reviews"] if review["targetID"] != old_id]
        data["currentSelections"] = [
            selection
            for selection in data["currentSelections"]
            if selection.get("assessmentID") != old_id
        ]
        with self.assertRaisesRegex(MODULE.EvidenceValidationError, "requires certification"):
            MODULE.validate_envelope(data)


if __name__ == "__main__":
    unittest.main()
