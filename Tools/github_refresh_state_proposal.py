#!/usr/bin/env python3
"""Promote a reviewed refresh candidate on an existing catalog proposal branch.

The catalog proposal mutator remains authoritative for branch/PR creation. This
companion may only update one fixed source-refresh state path on that already
existing deterministic branch, and only when the catalog proposal is materially
changed relative to protected main.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import urllib.parse
from pathlib import Path
from typing import Any, Protocol

from catalog_refresh import RefreshError, digest_without, promote_state, validate_policy
from github_catalog_proposal import RECEIPT_PATH, GitHubClient
from production_catalog_release_input import ReleaseInputError, validate_release_input

STATE_PATHS = {
    "open-food-facts": "Data/refresh/accepted-open-food-facts-v1.json",
    "open-prices": "Data/refresh/accepted-open-prices-v1.json",
}


class RefreshStateMutationError(ValueError):
    pass


class Client(Protocol):
    def get_optional(self, path: str) -> dict[str, Any] | list[Any] | None: ...
    def get(self, path: str) -> dict[str, Any] | list[Any]: ...
    def put(self, path: str, body: dict[str, Any]) -> dict[str, Any]: ...


def _load_object(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshStateMutationError(f"failed to read {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise RefreshStateMutationError(f"{label} must be a JSON object")
    return value


def _content_path(path: str, ref: str) -> str:
    return "/contents/" + urllib.parse.quote(path, safe="/") + "?ref=" + urllib.parse.quote(ref, safe="")


def _ref_path(branch: str) -> str:
    return "/git/ref/" + urllib.parse.quote(f"heads/{branch}", safe="")


def _compare_path(base_sha: str, branch: str) -> str:
    return "/compare/" + urllib.parse.quote(base_sha, safe="") + "..." + urllib.parse.quote(branch, safe="")


def _decode_content(value: Any, label: str) -> bytes:
    if not isinstance(value, dict) or value.get("type") != "file":
        raise RefreshStateMutationError(f"{label} path is not a regular file")
    encoded = value.get("content")
    if not isinstance(encoded, str):
        raise RefreshStateMutationError(f"{label} content is unavailable")
    try:
        return base64.b64decode(encoded.replace("\n", ""), validate=True)
    except ValueError as exc:
        raise RefreshStateMutationError(f"{label} content is invalid base64") from exc


def _json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _decode_json(value: Any, label: str) -> dict[str, Any]:
    try:
        decoded = json.loads(_decode_content(value, label).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshStateMutationError(f"{label} is not valid UTF-8 JSON") from exc
    if not isinstance(decoded, dict):
        raise RefreshStateMutationError(f"{label} must contain a JSON object")
    return decoded


def _validate_state_digest(state: dict[str, Any], label: str) -> None:
    digest = state.get("stateSha256")
    if not isinstance(digest, str) or digest != digest_without(state, "stateSha256"):
        raise RefreshStateMutationError(f"{label} state digest mismatch")


def materialize(
    *,
    client: Client,
    policy: dict[str, Any],
    candidate_state: dict[str, Any],
    release_input: dict[str, Any],
    base_sha: str,
) -> dict[str, Any]:
    validate_policy(policy)
    receipt = validate_release_input(release_input)
    source_key = receipt["sourceKey"]
    state_path = STATE_PATHS.get(source_key)
    if state_path is None:
        raise RefreshStateMutationError("release source has no admitted refresh-state path")
    if receipt["reviewedSourceCommit"] != base_sha:
        raise RefreshStateMutationError("refresh promotion base differs from reviewed catalog source commit")

    _validate_state_digest(candidate_state, "candidate")
    if candidate_state.get("sourceKey") != source_key or candidate_state.get("market") != policy.get("market"):
        raise RefreshStateMutationError("candidate refresh state identity differs from release input")
    candidate = candidate_state.get("candidateComplete")
    if (
        candidate_state.get("candidateEligible") is not True
        or candidate_state.get("candidateChangedFromAccepted") is not True
        or not isinstance(candidate, dict)
        or candidate.get("snapshotID") != receipt["snapshotId"]
    ):
        raise RefreshStateMutationError("candidate refresh state does not bind the reviewed catalog snapshot")

    base_state_response = client.get_optional(_content_path(state_path, base_sha))
    if base_state_response is None:
        raise RefreshStateMutationError("protected base refresh state is missing")
    base_state = _decode_json(base_state_response, "protected base refresh state")
    _validate_state_digest(base_state, "protected base")
    if base_state.get("sourceKey") != source_key or base_state.get("market") != policy.get("market"):
        raise RefreshStateMutationError("protected base refresh state identity mismatch")
    if candidate_state.get("acceptedComplete") != base_state.get("acceptedComplete"):
        raise RefreshStateMutationError("candidate was not evaluated against protected accepted source lineage")
    if candidate_state.get("nextFullDueAt") != base_state.get("nextFullDueAt"):
        raise RefreshStateMutationError("candidate changed the accepted full-refresh clock before promotion")

    accepted_receipt_response = client.get_optional(_content_path(RECEIPT_PATH, base_sha))
    if accepted_receipt_response is not None:
        accepted_receipt = validate_release_input(_decode_json(accepted_receipt_response, "protected base release receipt"))
        if accepted_receipt["logicalCatalogSha256"] == receipt["logicalCatalogSha256"]:
            return {
                "branch": receipt["proposalKey"],
                "statePath": state_path,
                "unchanged": True,
                "promoted": False,
            }

    branch = receipt["proposalKey"]
    if client.get_optional(_ref_path(branch)) is None:
        raise RefreshStateMutationError("catalog proposal branch does not exist")

    branch_receipt_response = client.get_optional(_content_path(RECEIPT_PATH, branch))
    if branch_receipt_response is None:
        raise RefreshStateMutationError("catalog proposal branch lacks reviewed release receipt")
    branch_receipt = validate_release_input(_decode_json(branch_receipt_response, "proposal branch release receipt"))
    if branch_receipt != receipt:
        raise RefreshStateMutationError("proposal branch receipt differs from reviewed release input")

    try:
        promoted = promote_state(policy, candidate_state)
    except RefreshError as exc:
        raise RefreshStateMutationError(f"candidate refresh promotion failed: {exc}") from exc
    desired = _json_bytes(promoted)

    branch_state_response = client.get_optional(_content_path(state_path, branch))
    if branch_state_response is None:
        raise RefreshStateMutationError("proposal branch did not inherit protected refresh state")
    current = _decode_content(branch_state_response, "proposal branch refresh state")
    if current != desired:
        base_bytes = _json_bytes(base_state)
        if current != base_bytes:
            raise RefreshStateMutationError("proposal branch refresh state already differs from protected base")
        blob_sha = branch_state_response.get("sha") if isinstance(branch_state_response, dict) else None
        if not isinstance(blob_sha, str) or not blob_sha:
            raise RefreshStateMutationError("proposal branch refresh state blob SHA is unavailable")
        client.put(
            "/contents/" + urllib.parse.quote(state_path, safe="/"),
            {
                "message": f"Accept {source_key} refresh state for {receipt['snapshotId']}",
                "content": base64.b64encode(desired).decode("ascii"),
                "branch": branch,
                "sha": blob_sha,
            },
        )

    comparison = client.get(_compare_path(base_sha, branch))
    if not isinstance(comparison, dict) or not isinstance(comparison.get("files"), list):
        raise RefreshStateMutationError("proposal branch comparison is unavailable")
    paths = {
        item.get("filename")
        for item in comparison["files"]
        if isinstance(item, dict) and isinstance(item.get("filename"), str)
    }
    expected = {RECEIPT_PATH, state_path}
    if paths != expected:
        raise RefreshStateMutationError("catalog proposal branch contains unexpected paths after refresh promotion")

    return {
        "branch": branch,
        "statePath": state_path,
        "unchanged": False,
        "promoted": True,
        "acceptedSnapshotID": promoted["acceptedComplete"]["snapshotID"],
        "stateSha256": promoted["stateSha256"],
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=Path("Data/refresh/catalog-refresh-policy-v1.json"))
    parser.add_argument("--candidate-state", type=Path, required=True)
    parser.add_argument("--release-input", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base-sha", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        policy = _load_object(args.policy, "refresh policy")
        state = _load_object(args.candidate_state, "candidate refresh state")
        release_input = _load_object(args.release_input, "catalog release input")
        client = GitHubClient(token=os.environ.get("GITHUB_TOKEN", ""), repository=args.repository)
        result = materialize(
            client=client,
            policy=policy,
            candidate_state=state,
            release_input=release_input,
            base_sha=args.base_sha,
        )
    except (RefreshStateMutationError, RefreshError, ReleaseInputError) as exc:
        raise SystemExit(f"refresh state proposal materialization failed: {exc}") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
