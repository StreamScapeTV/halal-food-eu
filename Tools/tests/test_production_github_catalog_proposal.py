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
    def __init__(
        self,
        *,
        branch_exists: bool = False,
        branch_receipt: bytes | None = None,
        accepted_receipt: bytes | None = None,
        compare_files: list[str] | None = None,
        pull: int | None = None,
    ) -> None:
        self.branch_exists = branch_exists
        self.branch_receipt = branch_receipt
        self.accepted_receipt = accepted_receipt
        self.compare_files = [proposal_module.RECEIPT_PATH] if compare_files is None else compare_files
        self.pull = pull
        self.posts: list[tuple[str, dict[str, object]]] = []
        self.puts: list[tuple[str, dict[str, object]]] = []

    def get_optional(self, path: str):
        if path.startswith("/git/ref/"):
            return {"ref": "refs/heads/x"} if self.branch_exists else None
        if path.startswith("/contents/"):
            query = parse_qs(urlsplit(path).query)
            ref = query.get("ref", [None])[0]
            data = self.accepted_receipt if ref == "a" * 40 else self.branch_receipt
            if data is None:
                return None
            return {"type": "file", "content": base64.b64encode(data).decode("ascii")}
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
        self.branch_receipt = base64.b64decode(str(body["content"]))
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
            "logicalCatalogSha256": "5" * 64,
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
            "logicalCatalogSha256": "5" * 64,
            "recordCount": 53774,
            "releaseSummary": {
                "recordCount": 53774,
                "schemaVersion": 2,
                "methodologyVersion": "1.0.0",
                "changeComparison": {
                    "available": True,
                    "baseline": "none",
                    "additions": 53774,
                    "formulationChanges": 12,
                    "removals": 3,
                    "statusChangeCount": 8,
                    "reviewQueueCount": 5,
                },
                "staleRecords": 21,
                "sourceLicenseChanges": {
                    "comparisonAvailable": False,
                    "reason": "previous accepted production source-rights baseline was not supplied to this proposal",
                    "currentLicenses": ["ODbL-1.0"],
                    "currentAttributions": ["Open Food Facts"],
                    "qualitySourceLicense": "ODbL-1.0",
                    "attributionPresent": True,
                },
            },
            "requiresHumanReview": True,
            "materialChangeAutoMergeAllowed": False,
        }

    @staticmethod
    def _bytes(receipt: dict[str, object]) -> bytes:
        return (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")

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
        for path in proposal_module.REFRESH_STATE_PATHS:
            self.assertNotIn(path, serialized_writes)

    def test_same_logical_catalog_as_accepted_main_creates_no_branch_or_pr(self) -> None:
        accepted = self._receipt()
        accepted["snapshotId"] = "off-2026-08-01"
        accepted["catalogVersion"] = "1.1.0"
        accepted["sourceRunId"] = "33200000123"
        accepted["proposedCatalogSha256"] = "8" * 64
        accepted["proposedManifestSha256"] = "9" * 64
        client = FakeClient(accepted_receipt=self._bytes(accepted))
        result = proposal_module.materialize(
            client=client,
            repository="StreamScapeTV/halal-food-eu",
            base_ref="main",
            base_sha="a" * 40,
            receipt=self._receipt(),
            proposal=self._proposal(),
        )
        self.assertTrue(result["unchanged"])
        self.assertIsNone(result["pullRequest"])
        self.assertEqual(client.posts, [])
        self.assertEqual(client.puts, [])

    def test_malformed_accepted_receipt_fails_closed_before_branch_write(self) -> None:
        client = FakeClient(accepted_receipt=b"{}\n")
        with self.assertRaisesRegex(proposal_module.ProposalMutationError, "accepted main release receipt is invalid"):
            proposal_module.materialize(
                client=client,
                repository="StreamScapeTV/halal-food-eu",
                base_ref="main",
                base_sha="a" * 40,
                receipt=self._receipt(),
                proposal=self._proposal(),
            )
        self.assertEqual(client.posts, [])
        self.assertEqual(client.puts, [])

    def test_logical_digest_mismatch_fails_before_branch_write(self) -> None:
        proposal = self._proposal()
        proposal["logicalCatalogSha256"] = "6" * 64
        client = FakeClient()
        with self.assertRaisesRegex(proposal_module.ProposalMutationError, "logical catalog digest differs"):
            proposal_module.materialize(
                client=client,
                repository="StreamScapeTV/halal-food-eu",
                base_ref="main",
                base_sha="a" * 40,
                receipt=self._receipt(),
                proposal=proposal,
            )
        self.assertEqual(client.posts, [])
        self.assertEqual(client.puts, [])

    def test_identical_existing_branch_reuses_open_pr_without_write(self) -> None:
        receipt = self._receipt()
        client = FakeClient(branch_exists=True, branch_receipt=self._bytes(receipt), pull=41)
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

    def test_refresh_promoted_existing_branch_reuses_open_pr_without_write(self) -> None:
        receipt = self._receipt()
        promoted_paths = [proposal_module.RECEIPT_PATH, *sorted(proposal_module.REFRESH_STATE_PATHS)]
        client = FakeClient(
            branch_exists=True,
            branch_receipt=self._bytes(receipt),
            compare_files=promoted_paths,
            pull=41,
        )
        result = proposal_module.materialize(
            client=client,
            repository="StreamScapeTV/halal-food-eu",
            base_ref="main",
            base_sha="a" * 40,
            receipt=receipt,
            proposal=self._proposal(),
        )
        self.assertEqual(result["pullRequest"], 41)
        self.assertFalse(result["unchanged"])
        self.assertEqual(client.puts, [])
        self.assertEqual(client.posts, [])

    def test_existing_branch_with_different_receipt_fails_closed(self) -> None:
        client = FakeClient(branch_exists=True, branch_receipt=b"{}\n")
        with self.assertRaisesRegex(proposal_module.ProposalMutationError, "different release receipt"):
            proposal_module.materialize(
                client=client,
                repository="StreamScapeTV/halal-food-eu",
                base_ref="main",
                base_sha="a" * 40,
                receipt=self._receipt(),
                proposal=self._proposal(),
            )

    def test_branch_with_any_unadmitted_extra_path_fails_closed(self) -> None:
        client = FakeClient(compare_files=[proposal_module.RECEIPT_PATH, "README.md"])
        with self.assertRaisesRegex(proposal_module.ProposalMutationError, "outside admitted proposal paths"):
            proposal_module.materialize(
                client=client,
                repository="StreamScapeTV/halal-food-eu",
                base_ref="main",
                base_sha="a" * 40,
                receipt=self._receipt(),
                proposal=self._proposal(),
            )

    def test_branch_with_only_refresh_state_and_no_receipt_fails_closed(self) -> None:
        client = FakeClient(compare_files=[next(iter(proposal_module.REFRESH_STATE_PATHS))])
        with self.assertRaisesRegex(proposal_module.ProposalMutationError, "outside admitted proposal paths"):
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
        client = FakeClient(branch_exists=True, branch_receipt=self._bytes(receipt), compare_files=[])
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

    def test_proposal_body_truthfully_requires_review_and_describes_refresh_companion(self) -> None:
        title, body = proposal_module.proposal_copy(self._receipt(), self._proposal())
        self.assertIn("Catalog update 1.2.0", title)
        self.assertIn("never auto-merged", body)
        self.assertIn("not committed", body)
        self.assertIn("fixed accepted-source state checkpoints", body)
        self.assertIn("raw source data", body)
        self.assertIn("53774", body)
        self.assertIn("Logical catalog SHA-256", body)
        self.assertIn("## Release review summary", body)
        self.assertIn("Additions: `53774`", body)
        self.assertIn("formulation changes: `12`", body)
        self.assertIn("Status changes: `8`", body)
        self.assertIn("Stale formulation records: `21`", body)
        self.assertIn("Source/license change comparison: unavailable", body)

    def test_invalid_release_summary_fails_before_branch_write(self) -> None:
        proposal = self._proposal()
        proposal["releaseSummary"] = {"recordCount": 53774}
        client = FakeClient()
        with self.assertRaisesRegex(proposal_module.ProposalMutationError, "schemaVersion"):
            proposal_module.materialize(
                client=client,
                repository="StreamScapeTV/halal-food-eu",
                base_ref="main",
                base_sha="a" * 40,
                receipt=self._receipt(),
                proposal=proposal,
            )
        self.assertEqual(client.posts, [])
        self.assertEqual(client.puts, [])


if __name__ == "__main__":
    unittest.main()
