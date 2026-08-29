from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import open_food_facts_common as COMMON
from open_food_facts_acquire import acquire
from open_food_facts_normalize import normalize_snapshot

SOURCE_POLICY = ROOT / "Data/sources/open-food-facts/source-policy-v1.json"
SELECTION_POLICY = ROOT / "Data/selection/catalog-selection-policy-v1.json"
FIXTURE = ROOT / "Data/sources/open-food-facts/fixture-products.jsonl"
RETRIEVED_AT = "2026-08-29T18:00:00Z"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def acquire_fixture(path: Path, snapshot_id: str, fixture: Path = FIXTURE, retrieved_at: str = RETRIEVED_AT):
    COMMON.PROJECTED_FIXED_FIELDS.add("ingredients_text")
    return acquire(
        output=path,
        snapshot_id=snapshot_id,
        mode="fixture",
        policy=COMMON.load_source_policy(SOURCE_POLICY),
        fixture=fixture,
        retrieved_at=retrieved_at,
    )


class OpenFoodFactsPolicyTests(unittest.TestCase):
    def test_committed_source_policy_keeps_database_contents_and_images_distinct(self):
        policy = COMMON.load_source_policy(SOURCE_POLICY)
        self.assertEqual(policy.raw["databaseLicense"]["identifier"], "ODbL")
        self.assertTrue(policy.raw["databaseLicense"]["shareAlikeRequired"])
        self.assertEqual(policy.raw["databaseContentsLicense"]["identifier"], "Database Contents License")
        self.assertEqual(policy.raw["imageLicense"]["identifier"], "CC BY-SA")
        self.assertEqual(policy.raw["imageLicense"]["redistributionMode"], "references-only")
        self.assertFalse(policy.raw["imageLicense"]["downloadBinaries"])
        self.assertFalse(policy.raw["completenessClaimAllowed"])

    def test_reserved_200_prefix_is_not_treated_as_source_assigned_without_provenance(self):
        legitimate = {"code": "2001234567800"}
        source_assigned = {"code": "2001234567893", "_hfeu_source_assigned_no_barcode": True}
        self.assertFalse(COMMON.source_assigned_no_barcode(legitimate))
        self.assertTrue(COMMON.reserved_prefix_ambiguity(legitimate))
        self.assertTrue(COMMON.source_assigned_no_barcode(source_assigned))


class OpenFoodFactsIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.policy = COMMON.load_source_policy(SOURCE_POLICY)
        self.selection_policy = load(SELECTION_POLICY)

    def test_fixture_acquisition_selection_and_evidence_projection_are_end_to_end(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary) / "snapshot.jsonl"
            metadata = acquire_fixture(snapshot, "fixture-v1")
            evidence, reports, changes = normalize_snapshot(
                snapshot=snapshot,
                policy=self.policy,
                selection_policy=self.selection_policy,
            )

        self.assertEqual(metadata["recordsEmitted"], 8)
        selection = reports["selection"]
        selected = {item["sourceRecordID"]: item for item in selection["selected"]}
        invalid = {item["sourceRecordID"]: item["reasonCode"] for item in selection["invalidExclusions"]}
        self.assertEqual(set(selected), {"4006381333931", "4260123456788", "2001234567800"})
        self.assertEqual(invalid["7612345678900"], "non-food")
        self.assertEqual(invalid["4006381333932"], "invalid-or-unsupported-barcode")
        self.assertEqual(invalid["2001234567893"], "source-assigned-no-barcode")
        self.assertEqual(invalid["5901234123457"], "wrong-market")
        self.assertEqual(selection["basicExclusions"][0]["reasonCode"], "basic-fresh-produce")

        self.assertEqual(len(evidence["currentSelections"]), 3)
        self.assertEqual(evidence["assessments"], [])
        self.assertEqual(evidence["reviews"], [])
        self.assertEqual(reports["quality"]["evidence"]["positiveAssessmentsCreated"], 0)
        self.assertTrue(evidence["retailerEvidence"])
        self.assertTrue(all(item["kind"] == "community-store-report" for item in evidence["retailerEvidence"]))
        self.assertTrue(all(item["confidence"] == "low" for item in evidence["retailerEvidence"]))
        self.assertTrue(evidence["remoteImages"])
        self.assertTrue(all(item["url"].startswith("https://") for item in evidence["remoteImages"]))
        self.assertFalse(any("bytes" in item or "data" in item for item in evidence["remoteImages"]))
        self.assertIn(
            "2001234567800",
            reports["quality"]["warnings"]["restrictedPrefixProvenanceAmbiguities"],
        )
        self.assertEqual(changes["formulationChanges"], 0)
        self.assertTrue(changes["noCompletenessClaim"])

    def test_formulation_change_supersedes_prior_observation_and_enters_review_queue(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_snapshot = root / "first.jsonl"
            acquire_fixture(first_snapshot, "fixture-v1", retrieved_at="2026-08-29T18:00:00Z")
            first_evidence, _, _ = normalize_snapshot(
                snapshot=first_snapshot,
                policy=self.policy,
                selection_policy=self.selection_policy,
            )

            fixture_lines = FIXTURE.read_text(encoding="utf-8").splitlines()
            changed = json.loads(fixture_lines[0])
            changed["ingredients_text_de"] += " Salz."
            changed["ingredients_n"] = 5
            changed["rev"] = 13
            fixture_lines[0] = json.dumps(changed, ensure_ascii=False, separators=(",", ":"))
            changed_fixture = root / "changed-fixture.jsonl"
            changed_fixture.write_text("\n".join(fixture_lines) + "\n", encoding="utf-8")

            second_snapshot = root / "second.jsonl"
            acquire_fixture(
                second_snapshot,
                "fixture-v2",
                fixture=changed_fixture,
                retrieved_at="2026-08-30T18:00:00Z",
            )
            second_evidence, _, changes = normalize_snapshot(
                snapshot=second_snapshot,
                policy=self.policy,
                selection_policy=self.selection_policy,
                previous_evidence=first_evidence,
            )

        self.assertEqual(changes["formulationChanges"], 1)
        self.assertEqual(len(changes["reviewQueue"]), 1)
        self.assertEqual(changes["reviewQueue"][0]["gtin"], "04006381333931")
        current = next(item for item in second_evidence["currentSelections"] if item["gtin"] == "04006381333931")
        new_ingredient = next(item for item in second_evidence["ingredients"] if item["id"] == current["ingredientObservationID"])
        self.assertIn("supersedesID", new_ingredient)
        self.assertNotIn("assessmentID", current)

    def test_partial_sample_never_reports_upstream_deletions(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first_snapshot = root / "first.jsonl"
            acquire_fixture(first_snapshot, "fixture-v1")
            first_evidence, _, _ = normalize_snapshot(
                snapshot=first_snapshot,
                policy=self.policy,
                selection_policy=self.selection_policy,
            )

            lines = first_snapshot.read_text(encoding="utf-8").splitlines()
            metadata = json.loads(lines[-1])
            metadata["_hfeu_open_food_facts_metadata"]["mode"] = "sample"
            metadata["_hfeu_open_food_facts_metadata"]["downloadComplete"] = False
            partial = root / "partial.jsonl"
            partial.write_text(lines[0] + "\n" + json.dumps(metadata, sort_keys=True) + "\n", encoding="utf-8")
            _, _, changes = normalize_snapshot(
                snapshot=partial,
                policy=self.policy,
                selection_policy=self.selection_policy,
                previous_evidence=first_evidence,
            )

        self.assertFalse(changes["deletionComparisonAllowed"])
        self.assertEqual(changes["removals"], 0)
        self.assertEqual(changes["removedSelections"], [])

    def test_unlocalized_base_ingredient_text_survives_bounded_acquisition_projection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            fixture = root / "base-only.jsonl"
            record = {
                "code": "4006381333931",
                "schema_version": 1004,
                "product_type": "food",
                "product_name": "Base text only",
                "lang": "de",
                "countries_tags": ["en:germany"],
                "categories_tags": ["en:snacks"],
                "ingredients_text": "Mehl, Zucker",
                "ingredients_n": 2,
            }
            fixture.write_text(json.dumps(record, ensure_ascii=False) + "\n", encoding="utf-8")
            snapshot = root / "snapshot.jsonl"
            acquire_fixture(snapshot, "base-only", fixture=fixture)
            projected = json.loads(snapshot.read_text(encoding="utf-8").splitlines()[0])
        self.assertEqual(projected["ingredients_text"], "Mehl, Zucker")


if __name__ == "__main__":
    unittest.main()
