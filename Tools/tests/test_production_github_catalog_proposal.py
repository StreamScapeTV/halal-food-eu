from __future__ import annotations

import base64
import importlib.util
import json
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
MODULE_PATH = TOOLS / "github_catalog_proposal.py"
SPEC = importlib.util.spec_from_file_location("github_catalog_proposal", MODULE_PATH)
assert SPEC and SPEC.loader
proposal_module = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = proposal_module
SPEC.loader.exec_module(proposal_module)


class FakeClient:
    def __init__(self, *, branch_exists: bool = False, receipt: bytes | None = None, compare_files: list[str] | None = None, pull: int | None = None) -> None:
        self.branch_exists = branch_exists
        self.receipt = receipt
        self.compare_files = [proposal_module.RECEIPT_PATH] if compare_files is None else compare_files
        self.pull = pull
        self.posts: list[tuple[str, dict[str, object]]] = []
        self.puts: list[tuple[str, dict[str, object]]] = []

    def get_optional(self, path: str):
        if path.startswith("/git/ref/"):
            return {"ref": "refs/heads/x"} if self.branch_exists else None
        if path.startswith("/contents/"):
            if self.receipt is None:
                return None
            return {"type": "file", "content": base64.b64encode(self.receipt).decode("ascii")}
        raise AssertionError(path)

    def get(self, path: str):
        if path.startswith("/compare/"):
            return {"files": [{"filename": filename} for filename in self.compare_files]}
        if path.startswith("/pulls?"):
            query = parse_qs(urlsplit(path).query)
            assert query["base"] == ["main"]
            return [] if self.pull is None else [{"number": self.pull}]
        raise AssertionError(path)

    def post(self, path: str, body: dict[str, object]):
        self.posts.append((path, body))
        if path == "/git/refs":
            self.branch_exists = True
            return {"ref": body["ref"]}
        if path == "/pulls":
            return {"number": 55}
        raise AssertionError(path)

    def put(self, path: str, body: dict[str, object]):
        self.puts.append((path, body))
        self.receipt = base64.b64decode(str(body["content"]))
        return {"content": {"path": proposal_module.RECEIPT_PATH}}


class GitHubCatalogProposalTests(unittest.TestCase):
    def _receipt(self) -> dict[str, object]:
        input_base = {
            "artifactName": "artifact",
            "payloadSha256": "3" * 64,
            "payloadByteCount": 10,
            "recordCount": 1,
            "contentSchemaVersion": "v1",
        }
        return {
            "schemaVersion": 1,
            "sourceKey": "open-food-facts",
            "snapshotId": "off-2026-08-30",
            "catalogVersion": "1.2.0",
            "proposalKey": "catalog-update/open-food-facts-0123456789abcdef",
            "reviewedSourceCommit": "a" * 40,
            "sourceRunId": "33300000123",
            "proposedCatalogSha256": "1" * 64,
            "proposedManifestSha256": "2" * 64,
            "selectionPolicyVersion": "1.0.0",
            "qualityEvaluatedAt": "2026-08-30T15:00:00Z",
            "inputs": {
                "normalizedEvidence": input_base | {
                    "artifactName": "normalized-off-33300000123",
                    "artifactKind": "normalized-evidence",
                    "producerWorkflow": "normalize-and-diff.yml",
                },
                "qualityReport": input_base | {
                    "artifactName": "quality-off-33300000123",
                    "artifactKind": "quality-report",
                    "producerWorkflow": "catalog-quality.yml",
                },
                "basicExclusions": input_base | {
                    "artifactName": "basic-exclusions-off-33300000123",
                    "artifactKind": "basic-exclusions",
                    "producerWorkflow": "normalize-and-diff.yml",
                },
            },
        }

    def _proposal(self) -> dict[str, object]:
        return {
            "proposalKey": "catalog-update/open-food-facts-0123456789abcdef",
            "catalogSha256": "1" * 64,
            "manifestSha256": "2" * 64,
            "recordCount": 53774,
            "requiresHumanReview": True,
            "materialChangeAutoMergeAllowed": False,
        }

    def test_new_proposal_creates_one_receipt_branch_and_pr(self) -> None:
        client = FakeClient()
        result = proposal_module.materialize(
            client=client,
            repository="StreamScapeTV/halal-food-eu",
            base_ref="main",
            base_sha="a" * 40,
            receipt=self._receipt(),
            proposal=self._proposal(),
        )
        self.assertEqual(result["pullRequest"], 55)
        self.assertFalse(result["unchanged"])
        self.assertEqual(client.posts[0][0], "/git/refs")
        self.assertEqual(client.puts[0][0], "/contents/Data/catalog/production-catalog-release-input-v1.json")
        self.assertEqual(client.posts[-1][0], "/pulls")
        serialized_writes = json.dumps(client.posts + client.puts, sort_keys=True)
        self.assertNotIn("catalog.sqlite3", serialized_writes)
        self.assertNotIn("HalalFoodEU/Resources", serialized_writes)

    def test_identical_existing_branch_reuses_open_pr_without_write(self) -> None:
        receipt = self._receipt()
        desired = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
        client = FakeClient(branch_exists=True, receipt=desired, pull=41)
        result = proposal_module.materialize(
            client=client,
            repository="StreamScapeTV/halal-food-eu",
            base_ref="main",
            base_sha="a" * 40,
            receipt=receipt,
            proposal=self._proposal(),
        )
        self.assertEqual(result["pullRequest"], 41)
        self.assertEqual(client.puts, [])
        self.assertEqual(client.posts, [])

    def test_existing_branch_with_different_receipt_fails_closed(self) -> None:
        client = FakeClient(branch_exists=True, receipt=b"{}\n")
        with self.assertRaisesRegex(proposal_module.ProposalMutationError, "different release receipt"):
            proposal_module.materialize(
                client=client,
                repository="StreamScapeTV/halal-food-eu",
                base_ref="main",
                base_sha="a" * 40,
                receipt=self._receipt(),
                proposal=self._proposal(),
            )

    def test_branch_with_any_extra_path_fails_closed(self) -> None:
        client = FakeClient(compare_files=[proposal_module.RECEIPT_PATH, "README.md"])
        with self.assertRaisesRegex(proposal_module.ProposalMutationError, "outside the release receipt"):
            proposal_module.materialize(
                client=client,
                repository="StreamScapeTV/halal-food-eu",
                base_ref="main",
                base_sha="a" * 40,
                receipt=self._receipt(),
                proposal=self._proposal(),
            )

    def test_integrated_identical_receipt_creates_no_pull_request(self) -> None:
        receipt = self._receipt()
        desired = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")
        client = FakeClient(branch_exists=True, receipt=desired, compare_files=[])
        result = proposal_module.materialize(
            client=client,
            repository="StreamScapeTV/halal-food-eu",
            base_ref="main",
            base_sha="a" * 40,
            receipt=receipt,
            proposal=self._proposal(),
        )
        self.assertTrue(result["unchanged"])
        self.assertIsNone(result["pullRequest"])
        self.assertEqual(client.posts, [])

    def test_proposal_body_truthfully_requires_review(self) -> None:
        title, body = proposal_module.proposal_copy(self._receipt(), self._proposal())
        self.assertIn("Catalog update 1.2.0", title)
        self.assertIn("never auto-merged", body)
        self.assertIn("not committed", body)
        self.assertIn("53774", body)


if __name__ == "__main__":
    unittest.main()
