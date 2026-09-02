import base64
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Tools"))

from catalog_refresh import digest_without
from github_refresh_state_proposal import (
    RefreshStateMutationError,
    materialize,
)

BASE_SHA = "a" * 40
STATE_PATH = "Data/refresh/accepted-open-food-facts-v1.json"
RECEIPT_PATH = "Data/catalog/production-catalog-release-input-v1.json"
POLICY = json.loads((ROOT / "Data/refresh/catalog-refresh-policy-v1.json").read_text(encoding="utf-8"))


def encoded(value, sha="blob-sha"):
    raw = (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")
    return {
        "type": "file",
        "sha": sha,
        "content": base64.b64encode(raw).decode("ascii"),
    }


def base_state():
    value = {
        "schemaVersion": 1,
        "sourceKey": "open-food-facts",
        "market": "DE",
        "policyVersion": "1.0.0",
        "evaluatedAt": "2026-09-02T00:00:00Z",
        "acceptedComplete": None,
        "candidateComplete": None,
        "lastAttempt": None,
        "nextFullDueAt": "2026-09-02T00:00:00Z",
        "candidateEligible": False,
        "candidateChangedFromAccepted": False,
    }
    value["stateSha256"] = digest_without(value, "stateSha256")
    return value


def candidate_state():
    attempt = {
        "snapshotID": "off-full-2",
        "mode": "full",
        "status": "complete",
        "retrievedAt": "2026-09-05T00:00:00Z",
        "contentSha256": "b" * 64,
        "recordCount": 100,
        "upstream": {"etag": '"etag-2"', "lastModified": "Sat, 05 Sep 2026 00:00:00 GMT"},
        "adapterVersion": "1.0.0",
        "sourcePolicySha256": "c" * 64,
        "qualityStatus": "pass",
    }
    value = base_state()
    value.update(
        evaluatedAt="2026-09-05T00:00:00Z",
        candidateComplete=copy.deepcopy(attempt),
        lastAttempt=copy.deepcopy(attempt),
        candidateEligible=True,
        candidateChangedFromAccepted=True,
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
                "normalized-open-food-facts-off-full-2-12345",
                "normalized-evidence",
                "normalize-and-diff.yml",
                "1" * 64,
            ),
            "qualityReport": entry(
                "quality-open-food-facts-off-full-2-12345",
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


class FakeClient:
    def __init__(self, *, receipt, state, accepted_receipt=None, diff_paths=None, branch_exists=True):
        self.receipt = receipt
        self.state = state
        self.accepted_receipt = accepted_receipt
        self.diff_paths = diff_paths or [RECEIPT_PATH, STATE_PATH]
        self.branch_exists = branch_exists
        self.puts = []

    def get_optional(self, path):
        branch = self.receipt["proposalKey"]
        if path.startswith("/git/ref/"):
            return {"ref": f"refs/heads/{branch}"} if self.branch_exists else None
        if STATE_PATH in path and f"ref={BASE_SHA}" in path:
            return encoded(self.state, "base-state-sha")
        if RECEIPT_PATH in path and f"ref={BASE_SHA}" in path:
            return encoded(self.accepted_receipt, "base-receipt-sha") if self.accepted_receipt else None
        if RECEIPT_PATH in path and urllib_quote(branch) in path:
            return encoded(self.receipt, "branch-receipt-sha")
        if STATE_PATH in path and urllib_quote(branch) in path:
            return encoded(self.state, "branch-state-sha")
        return None

    def get(self, path):
        if path.startswith("/compare/"):
            return {"files": [{"filename": item} for item in self.diff_paths]}
        raise AssertionError(path)

    def put(self, path, body):
        self.puts.append((path, body))
        return {"content": {"sha": "new-state-sha"}}


def urllib_quote(value):
    import urllib.parse
    return urllib.parse.quote(value, safe="")


class RefreshStateProposalTests(unittest.TestCase):
    def test_material_change_promotes_only_fixed_refresh_state_path(self):
        receipt = release_input()
        base = base_state()
        client = FakeClient(receipt=receipt, state=base)
        result = materialize(
            client=client,
            policy=copy.deepcopy(POLICY),
            candidate_state=candidate_state(),
            release_input=receipt,
            base_sha=BASE_SHA,
        )
        self.assertTrue(result["promoted"])
        self.assertFalse(result["unchanged"])
        self.assertEqual(result["statePath"], STATE_PATH)
        self.assertEqual(result["acceptedSnapshotID"], "off-full-2")
        self.assertEqual(len(client.puts), 1)
        path, body = client.puts[0]
        self.assertEqual(path, "/contents/" + STATE_PATH)
        self.assertEqual(body["branch"], receipt["proposalKey"])
        promoted = json.loads(base64.b64decode(body["content"]).decode("utf-8"))
        self.assertEqual(promoted["acceptedComplete"]["snapshotID"], "off-full-2")
        self.assertIsNone(promoted["candidateComplete"])
        self.assertEqual(promoted["nextFullDueAt"], "2026-09-12T00:00:00Z")

    def test_logical_noop_does_not_create_or_promote_refresh_state(self):
        receipt = release_input("9" * 64)
        accepted = release_input("9" * 64)
        client = FakeClient(
            receipt=receipt,
            state=base_state(),
            accepted_receipt=accepted,
            branch_exists=False,
        )
        result = materialize(
            client=client,
            policy=copy.deepcopy(POLICY),
            candidate_state=candidate_state(),
            release_input=receipt,
            base_sha=BASE_SHA,
        )
        self.assertTrue(result["unchanged"])
        self.assertFalse(result["promoted"])
        self.assertEqual(client.puts, [])

    def test_candidate_cannot_move_full_due_clock_before_promotion(self):
        candidate = candidate_state()
        candidate["nextFullDueAt"] = "2026-09-12T00:00:00Z"
        candidate["stateSha256"] = digest_without(candidate, "stateSha256")
        with self.assertRaisesRegex(RefreshStateMutationError, "full-refresh clock"):
            materialize(
                client=FakeClient(receipt=release_input(), state=base_state()),
                policy=copy.deepcopy(POLICY),
                candidate_state=candidate,
                release_input=release_input(),
                base_sha=BASE_SHA,
            )

    def test_candidate_snapshot_must_match_catalog_proposal(self):
        candidate = candidate_state()
        candidate["candidateComplete"]["snapshotID"] = "different-snapshot"
        candidate["stateSha256"] = digest_without(candidate, "stateSha256")
        with self.assertRaisesRegex(RefreshStateMutationError, "reviewed catalog snapshot"):
            materialize(
                client=FakeClient(receipt=release_input(), state=base_state()),
                policy=copy.deepcopy(POLICY),
                candidate_state=candidate,
                release_input=release_input(),
                base_sha=BASE_SHA,
            )

    def test_unexpected_proposal_branch_path_fails_closed(self):
        client = FakeClient(
            receipt=release_input(),
            state=base_state(),
            diff_paths=[RECEIPT_PATH, STATE_PATH, "unexpected.txt"],
        )
        with self.assertRaisesRegex(RefreshStateMutationError, "unexpected paths"):
            materialize(
                client=client,
                policy=copy.deepcopy(POLICY),
                candidate_state=candidate_state(),
                release_input=release_input(),
                base_sha=BASE_SHA,
            )

    def test_missing_catalog_proposal_branch_fails_closed(self):
        with self.assertRaisesRegex(RefreshStateMutationError, "does not exist"):
            materialize(
                client=FakeClient(receipt=release_input(), state=base_state(), branch_exists=False),
                policy=copy.deepcopy(POLICY),
                candidate_state=candidate_state(),
                release_input=release_input(),
                base_sha=BASE_SHA,
            )


if __name__ == "__main__":
    unittest.main()
