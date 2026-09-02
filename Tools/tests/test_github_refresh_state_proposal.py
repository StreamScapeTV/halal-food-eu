import base64
import copy
import json
import sys
import unittest
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Tools"))

from catalog_refresh import digest_without
from github_refresh_state_proposal import (
    RECEIPT_PATH,
    STATE_PATHS,
    RefreshStateMutationError,
    materialize,
)

BASE_SHA = "a" * 40
POLICY = json.loads((ROOT / "Data/refresh/catalog-refresh-policy-v1.json").read_text(encoding="utf-8"))


def encoded(value, sha="blob-sha"):
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return {
        "type": "file",
        "sha": sha,
        "content": base64.b64encode(raw).decode("ascii"),
    }


def base_state(source_key):
    value = {
        "schemaVersion": 1,
        "sourceKey": source_key,
        "market": "DE",
        "policyVersion": "1.0.0",
        "evaluatedAt": "2026-09-02T00:00:00Z",
        "acceptedComplete": None,
        "candidateComplete": None,
        "lastAttempt": None,
        "lastSuccessfulFullAcquisitionAt": None,
        "lastSuccessfulFullSnapshotID": None,
        "nextFullDueAt": "2026-09-02T00:00:00Z",
        "candidateEligible": False,
        "candidateChangedFromAccepted": False,
    }
    value["stateSha256"] = digest_without(value, "stateSha256")
    return value


def candidate_state(source_key, snapshot_id, digest_char, *, changed=True):
    attempt = {
        "snapshotID": snapshot_id,
        "mode": "full",
        "status": "complete",
        "retrievedAt": "2026-09-05T00:00:00Z",
        "contentSha256": digest_char * 64,
        "recordCount": 100,
        "upstream": {},
        "adapterVersion": "1.0.0",
        "sourcePolicySha256": "c" * 64,
        "qualityStatus": "pass",
    }
    value = base_state(source_key)
    value.update(
        evaluatedAt="2026-09-05T00:00:00Z",
        candidateComplete=copy.deepcopy(attempt) if changed else None,
        lastAttempt=copy.deepcopy(attempt),
        lastSuccessfulFullAcquisitionAt="2026-09-05T00:00:00Z",
        lastSuccessfulFullSnapshotID=snapshot_id,
        nextFullDueAt="2026-09-12T00:00:00Z",
        candidateEligible=changed,
        candidateChangedFromAccepted=changed,
    )
    value["stateSha256"] = digest_without(value, "stateSha256")
    return value


def release_input(logical="d" * 64):
    def entry(name, kind, workflow, digest):
        return {
            "artifactName": name,
            "artifactKind": kind,
            "producerWorkflow": workflow,
            "payloadSha256": digest,
            "payloadByteCount": 123,
            "recordCount": 1,
            "contentSchemaVersion": "v1",
        }

    return {
        "schemaVersion": 1,
        "sourceKey": "open-food-facts",
        "snapshotId": "off-full-2",
        "catalogVersion": "1.2.3",
        "proposalKey": "catalog-update/open-food-facts-0123456789abcdef",
        "reviewedSourceCommit": BASE_SHA,
        "sourceRunId": "12345",
        "proposedCatalogSha256": "e" * 64,
        "proposedManifestSha256": "f" * 64,
        "logicalCatalogSha256": logical,
        "selectionPolicyVersion": "1.0.0",
        "qualityEvaluatedAt": "2026-09-05T01:00:00Z",
        "inputs": {
            "normalizedEvidence": entry(
                "normalized-open-food-facts-off-full-2-12345-aggregate",
                "normalized-evidence",
                "normalize-and-diff.yml",
                "1" * 64,
            ),
            "qualityReport": entry(
                "quality-open-food-facts-off-full-2-12345-aggregate",
                "quality-report",
                "catalog-quality.yml",
                "2" * 64,
            ),
            "basicExclusions": entry(
                "basic-exclusions-open-food-facts-off-full-2-12345",
                "basic-exclusions",
                "normalize-and-diff.yml",
                "3" * 64,
            ),
        },
    }


def normalized_evidence(*, retailer_snapshot="op-full-2"):
    return {
        "schemaVersion": 1,
        "sources": [
            {"sourceKey": "open-food-facts", "sourceSnapshotID": "off-full-2"},
            {"sourceKey": "open-prices", "sourceSnapshotID": retailer_snapshot},
        ],
    }


class FakeClient:
    def __init__(self, *, receipt, states, accepted_receipt=None, diff_paths=None, branch_exists=True):
        self.receipt = receipt
        self.states = copy.deepcopy(states)
        self.branch_states = copy.deepcopy(states)
        self.accepted_receipt = accepted_receipt
        self.diff_paths = diff_paths
        self.branch_exists = branch_exists
        self.puts = []

    def _state_for_path(self, path):
        for source_key, state_path in STATE_PATHS.items():
            if state_path in path:
                return source_key
        return None

    def get_optional(self, path):
        branch = self.receipt["proposalKey"]
        if path.startswith("/git/ref/"):
            return {"ref": f"refs/heads/{branch}"} if self.branch_exists else None
        source_key = self._state_for_path(path)
        if source_key and f"ref={BASE_SHA}" in path:
            return encoded(self.states[source_key], f"base-{source_key}-sha")
        if RECEIPT_PATH in path and f"ref={BASE_SHA}" in path:
            return encoded(self.accepted_receipt, "base-receipt-sha") if self.accepted_receipt else None
        if RECEIPT_PATH in path and urllib.parse.quote(branch, safe="") in path:
            return encoded(self.receipt, "branch-receipt-sha")
        if source_key and urllib.parse.quote(branch, safe="") in path:
            return encoded(self.branch_states[source_key], f"branch-{source_key}-sha")
        return None

    def get(self, path):
        if path.startswith("/compare/"):
            if self.diff_paths is not None:
                paths = self.diff_paths
            else:
                paths = [RECEIPT_PATH]
                for source_key, state_path in STATE_PATHS.items():
                    if source_key in self.states and self.branch_states[source_key] != self.states[source_key]:
                        paths.append(state_path)
            return {"files": [{"filename": item} for item in paths]}
        raise AssertionError(path)

    def put(self, path, body):
        self.puts.append((path, body))
        source_key = self._state_for_path(path)
        if source_key is None:
            raise AssertionError(path)
        self.branch_states[source_key] = json.loads(base64.b64decode(body["content"]).decode("utf-8"))
        return {"content": {"sha": f"new-{source_key}-sha"}}


def states():
    return {
        "open-food-facts": base_state("open-food-facts"),
        "open-prices": base_state("open-prices"),
    }


def call(client, candidate_states, receipt=None, evidence=None):
    receipt = receipt or release_input()
    return materialize(
        client=client,
        policy=copy.deepcopy(POLICY),
        candidate_states=copy.deepcopy(candidate_states),
        release_input=receipt,
        normalized_evidence=copy.deepcopy(evidence or normalized_evidence()),
        base_sha=BASE_SHA,
    )


class RefreshStateProposalTests(unittest.TestCase):
    def test_both_changed_sources_are_promoted_on_one_catalog_branch(self):
        receipt = release_input()
        client = FakeClient(receipt=receipt, states=states())
        result = call(
            client,
            [
                candidate_state("open-food-facts", "off-full-2", "b"),
                candidate_state("open-prices", "op-full-2", "9"),
            ],
            receipt,
        )
        self.assertFalse(result["unchanged"])
        self.assertTrue(result["promotions"]["open-food-facts"]["promoted"])
        self.assertTrue(result["promotions"]["open-prices"]["promoted"])
        self.assertEqual(len(client.puts), 2)
        self.assertEqual(
            {path for path, _ in client.puts},
            {"/contents/" + STATE_PATHS["open-food-facts"], "/contents/" + STATE_PATHS["open-prices"]},
        )

    def test_retailer_only_catalog_change_does_not_fail_unchanged_primary_candidate(self):
        receipt = release_input()
        client = FakeClient(receipt=receipt, states=states())
        result = call(
            client,
            [
                candidate_state("open-food-facts", "off-full-2", "b", changed=False),
                candidate_state("open-prices", "op-full-2", "9"),
            ],
            receipt,
        )
        self.assertFalse(result["promotions"]["open-food-facts"]["promoted"])
        self.assertTrue(result["promotions"]["open-prices"]["promoted"])
        self.assertEqual(len(client.puts), 1)
        self.assertIn(STATE_PATHS["open-prices"], client.puts[0][0])

    def test_primary_only_change_leaves_retailer_state_on_protected_lineage(self):
        receipt = release_input()
        client = FakeClient(receipt=receipt, states=states())
        result = call(
            client,
            [
                candidate_state("open-food-facts", "off-full-2", "b"),
                candidate_state("open-prices", "op-full-2", "9", changed=False),
            ],
            receipt,
        )
        self.assertTrue(result["promotions"]["open-food-facts"]["promoted"])
        self.assertFalse(result["promotions"]["open-prices"]["promoted"])
        self.assertEqual(len(client.puts), 1)
        self.assertIn(STATE_PATHS["open-food-facts"], client.puts[0][0])

    def test_material_catalog_change_with_no_source_candidate_keeps_receipt_only(self):
        receipt = release_input()
        client = FakeClient(receipt=receipt, states=states())
        result = call(
            client,
            [
                candidate_state("open-food-facts", "off-full-2", "b", changed=False),
                candidate_state("open-prices", "op-full-2", "9", changed=False),
            ],
            receipt,
        )
        self.assertFalse(result["unchanged"])
        self.assertEqual(client.puts, [])
        self.assertFalse(result["promotions"]["open-food-facts"]["promoted"])
        self.assertFalse(result["promotions"]["open-prices"]["promoted"])

    def test_logical_noop_does_not_require_proposal_branch_or_promote_state(self):
        receipt = release_input("8" * 64)
        accepted = release_input("8" * 64)
        client = FakeClient(
            receipt=receipt,
            states=states(),
            accepted_receipt=accepted,
            branch_exists=False,
        )
        result = call(
            client,
            [
                candidate_state("open-food-facts", "off-full-2", "b"),
                candidate_state("open-prices", "op-full-2", "9"),
            ],
            receipt,
        )
        self.assertTrue(result["unchanged"])
        self.assertEqual(client.puts, [])

    def test_secondary_candidate_must_be_present_in_reviewed_aggregate_evidence(self):
        receipt = release_input()
        client = FakeClient(receipt=receipt, states=states())
        with self.assertRaisesRegex(RefreshStateMutationError, "open-prices.*reviewed normalized snapshot"):
            call(
                client,
                [
                    candidate_state("open-food-facts", "off-full-2", "b"),
                    candidate_state("open-prices", "op-full-2", "9"),
                ],
                receipt,
                normalized_evidence(retailer_snapshot="op-other"),
            )

    def test_primary_evidence_snapshot_must_match_catalog_receipt(self):
        receipt = release_input()
        evidence = normalized_evidence()
        evidence["sources"][0]["sourceSnapshotID"] = "off-other"
        with self.assertRaisesRegex(RefreshStateMutationError, "catalog proposal source snapshot"):
            call(
                FakeClient(receipt=receipt, states=states()),
                [candidate_state("open-food-facts", "off-full-2", "b")],
                receipt,
                evidence,
            )

    def test_candidate_accepted_lineage_must_match_protected_base(self):
        candidate = candidate_state("open-food-facts", "off-full-2", "b")
        candidate["acceptedComplete"] = copy.deepcopy(candidate["candidateComplete"])
        candidate["stateSha256"] = digest_without(candidate, "stateSha256")
        with self.assertRaisesRegex(RefreshStateMutationError, "protected accepted source lineage"):
            call(
                FakeClient(receipt=release_input(), states=states()),
                [candidate],
            )

    def test_inconsistent_empty_candidate_flags_fail_closed(self):
        candidate = candidate_state("open-food-facts", "off-full-2", "b", changed=False)
        candidate["candidateEligible"] = True
        candidate["stateSha256"] = digest_without(candidate, "stateSha256")
        with self.assertRaisesRegex(RefreshStateMutationError, "inconsistent eligibility"):
            call(FakeClient(receipt=release_input(), states=states()), [candidate])

    def test_unexpected_proposal_branch_path_fails_closed(self):
        receipt = release_input()
        client = FakeClient(
            receipt=receipt,
            states=states(),
            diff_paths=[
                RECEIPT_PATH,
                STATE_PATHS["open-food-facts"],
                STATE_PATHS["open-prices"],
                "unexpected.txt",
            ],
        )
        with self.assertRaisesRegex(RefreshStateMutationError, "unexpected paths"):
            call(
                client,
                [
                    candidate_state("open-food-facts", "off-full-2", "b"),
                    candidate_state("open-prices", "op-full-2", "9"),
                ],
                receipt,
            )

    def test_missing_catalog_proposal_branch_fails_closed(self):
        receipt = release_input()
        with self.assertRaisesRegex(RefreshStateMutationError, "does not exist"):
            call(
                FakeClient(receipt=receipt, states=states(), branch_exists=False),
                [candidate_state("open-food-facts", "off-full-2", "b")],
                receipt,
            )

    def test_duplicate_candidate_source_fails_closed(self):
        candidate = candidate_state("open-food-facts", "off-full-2", "b")
        with self.assertRaisesRegex(RefreshStateMutationError, "duplicate candidate"):
            call(
                FakeClient(receipt=release_input(), states=states()),
                [candidate, candidate],
            )


if __name__ == "__main__":
    unittest.main()
