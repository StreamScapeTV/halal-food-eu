#!/usr/bin/env python3
"""Materialize one deterministic production catalog receipt branch and pull request.

This is the narrow repository-write adapter for the trusted proposal workflow. It
never uploads catalog databases, manifests, raw source data, or product images. The
only repository mutation it can make is the fixed metadata receipt path
``Data/catalog/production-catalog-release-input-v1.json`` on the deterministic branch
already derived by the reviewed production proposal gate.

The adapter is deliberately fail-closed: an existing deterministic branch may be
reused only when its receipt bytes are identical and its diff from the exact reviewed
base contains no other path. Material changes are never merged automatically.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Protocol

from production_catalog_release_input import ReleaseInputError, validate_release_input

RECEIPT_PATH = "Data/catalog/production-catalog-release-input-v1.json"
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")


class ProposalMutationError(ValueError):
    """Raised when deterministic proposal materialization cannot proceed safely."""


class Client(Protocol):
    def get_optional(self, path: str) -> dict[str, Any] | list[Any] | None: ...
    def get(self, path: str) -> dict[str, Any] | list[Any]: ...
    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]: ...
    def put(self, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


class GitHubClient:
    def __init__(self, *, token: str, repository: str) -> None:
        if not token:
            raise ProposalMutationError("GITHUB_TOKEN is required for proposal materialization")
        if not REPOSITORY.fullmatch(repository):
            raise ProposalMutationError("GITHUB_REPOSITORY is invalid")
        self._token = token
        self._base = f"https://api.github.com/repos/{repository}"

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self._base + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "halal-food-eu-catalog-proposal",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise ProposalMutationError(f"GitHub API {method} {path} failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ProposalMutationError(f"GitHub API {method} {path} failed: {exc.reason}") from exc
        if not payload:
            return {}
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ProposalMutationError(f"GitHub API {method} {path} returned invalid JSON") from exc

    def get_optional(self, path: str) -> dict[str, Any] | list[Any] | None:
        try:
            return self._request("GET", path)
        except ProposalMutationError as exc:
            if "HTTP 404" in str(exc):
                return None
            raise

    def get(self, path: str) -> dict[str, Any] | list[Any]:
        return self._request("GET", path)

    def post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        result = self._request("POST", path, body)
        if not isinstance(result, dict):
            raise ProposalMutationError("GitHub API POST returned an unexpected response")
        return result

    def put(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        result = self._request("PUT", path, body)
        if not isinstance(result, dict):
            raise ProposalMutationError("GitHub API PUT returned an unexpected response")
        return result


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProposalMutationError(f"failed to read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProposalMutationError(f"{label} must be a JSON object")
    return value


def _repository_owner(repository: str) -> str:
    if not REPOSITORY.fullmatch(repository):
        raise ProposalMutationError("repository must be owner/name")
    return repository.split("/", 1)[0]


def _receipt_bytes(receipt: dict[str, Any]) -> bytes:
    return (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode("utf-8")


def proposal_copy(receipt: dict[str, Any], proposal: dict[str, Any]) -> tuple[str, str]:
    record_count = proposal.get("recordCount")
    if not isinstance(record_count, int) or isinstance(record_count, bool) or record_count < 0:
        raise ProposalMutationError("proposal report recordCount is invalid")
    title = f"Catalog update {receipt['catalogVersion']} ({receipt['snapshotId']})"
    body = "\n".join(
        (
            "## Production catalog proposal",
            "",
            f"- Source: `{receipt['sourceKey']}`",
            f"- Snapshot: `{receipt['snapshotId']}`",
            f"- Catalog version: `{receipt['catalogVersion']}`",
            f"- Detailed records: `{record_count}`",
            f"- Proposed catalog SHA-256: `{receipt['proposedCatalogSha256']}`",
            f"- Proposed manifest SHA-256: `{receipt['proposedManifestSha256']}`",
            f"- Selection policy: `{receipt['selectionPolicyVersion']}`",
            f"- Immutable source run: `{receipt['sourceRunId']}`",
            "",
            "This generated branch contains only the bounded post-merge release-input receipt. "
            "The SQLite database and product image binaries are intentionally not committed.",
            "",
            "Material changes require human review and are never auto-merged.",
        )
    )
    return title, body


def _content_path(branch: str) -> str:
    return (
        "/contents/"
        + urllib.parse.quote(RECEIPT_PATH, safe="/")
        + "?ref="
        + urllib.parse.quote(branch, safe="")
    )


def _ref_path(branch: str) -> str:
    return "/git/ref/" + urllib.parse.quote(f"heads/{branch}", safe="")


def _compare_path(base_sha: str, branch: str) -> str:
    return "/compare/" + urllib.parse.quote(base_sha, safe="") + "..." + urllib.parse.quote(branch, safe="")


def materialize(
    *,
    client: Client,
    repository: str,
    base_ref: str,
    base_sha: str,
    receipt: dict[str, Any],
    proposal: dict[str, Any],
) -> dict[str, Any]:
    validated = validate_release_input(receipt)
    if base_ref != "main":
        raise ProposalMutationError("production proposals may target only main")
    if not SHA40.fullmatch(base_sha):
        raise ProposalMutationError("proposal base SHA is invalid")
    if validated["reviewedSourceCommit"] != base_sha:
        raise ProposalMutationError("proposal base SHA differs from reviewed immutable source commit")
    if proposal.get("proposalKey") != validated["proposalKey"]:
        raise ProposalMutationError("proposal report key differs from release receipt")
    if proposal.get("catalogSha256") != validated["proposedCatalogSha256"]:
        raise ProposalMutationError("proposal report catalog digest differs from release receipt")
    if proposal.get("manifestSha256") != validated["proposedManifestSha256"]:
        raise ProposalMutationError("proposal report manifest digest differs from release receipt")
    if proposal.get("requiresHumanReview") is not True or proposal.get("materialChangeAutoMergeAllowed") is not False:
        raise ProposalMutationError("proposal report does not require fail-closed human review")

    owner = _repository_owner(repository)
    branch = validated["proposalKey"]
    desired = _receipt_bytes(validated)

    ref = client.get_optional(_ref_path(branch))
    if ref is None:
        client.post("/git/refs", {"ref": f"refs/heads/{branch}", "sha": base_sha})

    existing = client.get_optional(_content_path(branch))
    if existing is None:
        client.put(
            "/contents/" + urllib.parse.quote(RECEIPT_PATH, safe="/"),
            {
                "message": f"Record catalog {validated['catalogVersion']} release input",
                "content": base64.b64encode(desired).decode("ascii"),
                "branch": branch,
            },
        )
    else:
        if not isinstance(existing, dict) or existing.get("type") != "file":
            raise ProposalMutationError("existing proposal receipt path is not a regular file")
        encoded = existing.get("content")
        if not isinstance(encoded, str):
            raise ProposalMutationError("existing proposal receipt content is unavailable")
        try:
            current = base64.b64decode(encoded.replace("\n", ""), validate=True)
        except ValueError as exc:
            raise ProposalMutationError("existing proposal receipt content is invalid base64") from exc
        if current != desired:
            raise ProposalMutationError("existing deterministic proposal branch contains a different release receipt")

    comparison = client.get(_compare_path(base_sha, branch))
    if not isinstance(comparison, dict):
        raise ProposalMutationError("proposal branch comparison returned an unexpected response")
    files = comparison.get("files")
    if not isinstance(files, list):
        raise ProposalMutationError("proposal branch comparison did not include files")
    paths = [item.get("filename") for item in files if isinstance(item, dict)]
    if not paths:
        return {"branch": branch, "pullRequest": None, "unchanged": True}
    if paths != [RECEIPT_PATH]:
        raise ProposalMutationError("deterministic proposal branch contains changes outside the release receipt")

    title, body = proposal_copy(validated, proposal)
    query = urllib.parse.urlencode({"state": "open", "head": f"{owner}:{branch}", "base": base_ref})
    pulls = client.get(f"/pulls?{query}")
    if not isinstance(pulls, list):
        raise ProposalMutationError("open pull request lookup returned an unexpected response")
    if len(pulls) > 1:
        raise ProposalMutationError("multiple open pull requests target the deterministic proposal branch")
    if pulls:
        number = pulls[0].get("number") if isinstance(pulls[0], dict) else None
        if not isinstance(number, int):
            raise ProposalMutationError("existing proposal pull request number is invalid")
        return {"branch": branch, "pullRequest": number, "unchanged": False}

    created = client.post(
        "/pulls",
        {
            "title": title,
            "head": branch,
            "base": base_ref,
            "body": body,
            "maintainer_can_modify": False,
        },
    )
    number = created.get("number")
    if not isinstance(number, int):
        raise ProposalMutationError("created proposal pull request number is invalid")
    return {"branch": branch, "pullRequest": number, "unchanged": False}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--proposal-report", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base-ref", default="main")
    parser.add_argument("--base-sha", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        receipt = validate_release_input(_load_object(args.receipt, "release receipt"))
        proposal = _load_object(args.proposal_report, "proposal report")
        client = GitHubClient(token=os.environ.get("GITHUB_TOKEN", ""), repository=args.repository)
        result = materialize(
            client=client,
            repository=args.repository,
            base_ref=args.base_ref,
            base_sha=args.base_sha,
            receipt=receipt,
            proposal=proposal,
        )
    except (ProposalMutationError, ReleaseInputError) as exc:
        raise SystemExit(f"catalog proposal materialization failed: {exc}") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
