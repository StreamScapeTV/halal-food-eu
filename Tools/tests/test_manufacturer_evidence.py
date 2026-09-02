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

import manufacturer_evidence as MFG
import open_food_facts_acquire as ACQUIRE
import open_food_facts_common as COMMON
from open_food_facts_normalize import normalize_snapshot

SOURCE_POLICY = ROOT / "Data/sources/open-food-facts/source-policy-v1.json"
SELECTION_POLICY = ROOT / "Data/selection/catalog-selection-policy-v1.json"
FIXTURE = ROOT / "Data/sources/open-food-facts/fixture-products.jsonl"
RETRIEVED_AT = "2026-09-02T00:00:00Z"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


class ManufacturerProjectionTests(unittest.TestCase):
    def test_projection_keeps_only_reviewed_bounded_producer_metadata(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8").splitlines()[0])
        projected = MFG.project_source_record(raw)
        self.assertNotIn("owner", projected)
        self.assertNotIn("owner_fields", projected)
        self.assertNotIn("sources", projected)
        provenance = projected[MFG.PROVENANCE_KEY]
        self.assertEqual(provenance["owner"], "fixture-producer-org")
        self.assertEqual(
            provenance["ownerFieldSha256"]["ingredients_text_de"],
            MFG.sha256_text(raw["ingredients_text_de"]),
        )
        self.assertNotIn("unreviewed_private_blob", provenance["ownerFieldSha256"])
        source = provenance["manufacturerSources"][0]
        self.assertEqual(source["sourceID"], "fixture-producer-pim")
        self.assertNotIn("unreviewed_private_blob", source["fields"])
        self.assertNotIn("url", source)

    def test_source_only_manufacturer_metadata_does_not_become_exact_owner_field(self):
        raw = json.loads(FIXTURE.read_text(encoding="utf-8").splitlines()[6])
        provenance = MFG.sanitize_producer_provenance(raw)
        self.assertIsNotNone(provenance)
        assert provenance is not None
        self.assertNotIn("ownerFieldSha256", provenance)
        self.assertEqual(provenance["manufacturerSources"][0]["fields"], ["ingredients_text_en"])

    def test_unsafe_or_unbounded_source_metadata_is_omitted(self):
        record = {
            "owner": "x" * (MFG.MAX_OWNER_LENGTH + 1),
            "owner_fields": {"ingredients_text_en": "x" * (MFG.MAX_FIELD_VALUE_LENGTH + 1)},
            "sources": [
                {
                    "manufacturer": 1,
                    "id": "safe-id",
                    "fields": ["ingredients_text_en", "private-field"],
                    "source_licence_url": "http://not-https.invalid/license",
                }
            ],
        }
        provenance = MFG.sanitize_producer_provenance(record)
        self.assertIsNotNone(provenance)
        assert provenance is not None
        self.assertNotIn("owner", provenance)
        self.assertNotIn("ownerFieldSha256", provenance)
        self.assertNotIn("sourceLicenceURL", provenance["manufacturerSources"][0])


class ManufacturerEvidenceIntegrationTests(unittest.TestCase):
    def setUp(self):
        COMMON.PROJECTED_FIXED_FIELDS.add("ingredients_text")
        self.policy = COMMON.load_source_policy(SOURCE_POLICY)
        self.selection_policy = load(SELECTION_POLICY)

    def _pipeline(self, root: Path):
        snapshot = root / "snapshot.jsonl"
        ACQUIRE.acquire(
            output=snapshot,
            snapshot_id="manufacturer-fixture-v1",
            mode="fixture",
            policy=self.policy,
            fixture=FIXTURE,
            retrieved_at=RETRIEVED_AT,
        )
        evidence, _reports, changes = normalize_snapshot(
            snapshot=snapshot,
            policy=self.policy,
            selection_policy=self.selection_policy,
        )
        evidence_path = root / "evidence.json"
        changes_path = root / "changes.json"
        evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        changes_path.write_text(json.dumps(changes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        provenance, target = MFG.analyze(
            snapshot_path=snapshot,
            evidence_path=evidence_path,
            change_report_path=changes_path,
        )
        return snapshot, evidence, provenance, target

    def test_exact_owner_field_produces_confirmed_sidecar_without_runtime_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot, evidence, provenance, target = self._pipeline(Path(temporary))

        self.assertEqual(provenance["metrics"]["confirmedProducerFormulations"], 1)
        self.assertEqual(provenance["metrics"]["producerProvenanceCandidates"], 1)
        self.assertEqual(provenance["metrics"]["freshnessEvidenceGain"], 0)
        record = provenance["records"][0]
        self.assertEqual(record["producerID"], "fixture-producer-org")
        self.assertEqual(record["fieldName"], "ingredients_text_de")
        ingredient = next(item for item in evidence["ingredients"] if item["id"] == record["targetEvidenceID"])
        self.assertEqual(record["fieldValueSha256"], MFG.sha256_text(ingredient["ingredientsText"]))
        self.assertEqual(ingredient["verificationState"], "unverified")
        self.assertNotIn("observedAt", ingredient)
        self.assertEqual(evidence["assessments"], [])
        self.assertTrue(all(item["kind"] == "community-store-report" for item in evidence["retailerEvidence"]))

        reasons = [item["reason"] for item in target["items"]]
        self.assertIn("producer-formulation-confirmed", reasons)
        self.assertIn("producer-provenance-candidate", reasons)
        self.assertIn("ingredients-missing", reasons)

        projected_lines = [json.loads(line) for line in snapshot.read_text(encoding="utf-8").splitlines() if line.strip()]
        producer_record = next(item for item in projected_lines if item.get("code") == "4006381333931")
        self.assertNotIn("owner_fields", producer_record)
        self.assertNotIn("sources", producer_record)
        self.assertIn(MFG.PROVENANCE_KEY, producer_record)
        self.assertNotIn(
            "WEIZENMEHL, Zucker, Kakaobutter, Emulgator: Lecithine.",
            json.dumps(producer_record[MFG.PROVENANCE_KEY], ensure_ascii=False),
        )

    def test_reports_are_deterministic_for_same_snapshot_and_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            snapshot, evidence, first_provenance, first_target = self._pipeline(root)
            evidence_path = root / "evidence-repeat.json"
            change_path = root / "changes-repeat.json"
            evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            _, _reports, changes = normalize_snapshot(
                snapshot=snapshot,
                policy=self.policy,
                selection_policy=self.selection_policy,
            )
            change_path.write_text(json.dumps(changes, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            second_provenance, second_target = MFG.analyze(
                snapshot_path=snapshot,
                evidence_path=evidence_path,
                change_report_path=change_path,
            )
        self.assertEqual(first_provenance, second_provenance)
        self.assertEqual(first_target, second_target)

    def test_manufacturer_source_only_candidate_cannot_be_promoted_to_confirmed(self):
        with tempfile.TemporaryDirectory() as temporary:
            _, _, provenance, target = self._pipeline(Path(temporary))
        candidate = next(item for item in target["items"] if item["reason"] == "producer-provenance-candidate")
        self.assertNotIn("producerProvenanceID", candidate)
        self.assertFalse(any(record["sourceRecordID"] == candidate["sourceRecordID"] for record in provenance["records"]))

    def test_ambiguous_manufacturer_sources_remain_review_only(self):
        raw = {
            "owner": "producer-x",
            "sources": [
                {"manufacturer": 1, "id": "source-a", "fields": ["ingredients_text_en"]},
                {"manufacturer": 1, "id": "source-b", "fields": ["ingredients_text_en"]},
            ],
        }
        provenance = MFG.sanitize_producer_provenance(raw)
        assert provenance is not None
        self.assertEqual(len(provenance["manufacturerSources"]), 2)
        self.assertNotIn("ownerFieldSha256", provenance)


if __name__ == "__main__":
    unittest.main()
