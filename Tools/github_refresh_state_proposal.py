#!/usr/bin/env python3
"""Promote reviewed refresh candidates on an existing catalog proposal branch.

The catalog proposal mutator remains authoritative for branch/PR creation. This
companion can update only the fixed source-refresh state paths for exact sources
present in the reviewed aggregate evidence. Unchanged source candidates are
explicit no-ops; changed candidates are promoted only after their protected-base
accepted lineage and exact source snapshot are verified.
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
from catalog_refresh_operational_state import OperationalRefreshError, validate_operational_state
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


def _validate_state(state: dict[str, Any], label: str) -> None:
    digest = state.get("stateSha256")
    if not isinstance(digest, str) or digest != digest_without(state, "stateSha256"):
        raise RefreshStateMutationError(f"{label} state digest mismatch")
    try:
        validate_operational_state(state)
    except OperationalRefreshError as exc:
        raise RefreshStateMutationError(f"{label} operational state invalid: {exc}") from exc


def _source_snapshots(evidence: dict[str, Any]) -> dict[str, str]:
    sources = evidence.get("sources")
    if not isinstance(sources, list):
        raise RefreshStateMutationError("reviewed normalized evidence lacks sources")
    result: dict[str, str] = {}
    for index, item in enumerate(sources):
        if not isinstance(item, dict):
            raise RefreshStateMutationError(f"reviewed normalized evidence sources[{index}] is invalid")
        source_key = item.get("sourceKey")
        snapshot_id = item.get("sourceSnapshotID")
        if not isinstance(source_key, str) or not source_key:
            raise RefreshStateMutationError("reviewed normalized evidence sourceKey is invalid")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise RefreshStateMutationError(f"reviewed normalized evidence {source_key} snapshot is invalid")
        if source_key in result:
            raise RefreshStateMutationError(f"reviewed normalized evidence contains duplicate source {source_key}")
        result[source_key] = snapshot_id
    return result


def _promotion_candidate(
    *,
    policy: dict[str, Any],
    state: dict[str, Any],
    base_state: dict[str, Any],
    expected_snapshot: str,
) -> dict[str, Any] | None:
    source_key = state.get("sourceKey")
    if state.get("acceptedComplete") != base_state.get("acceptedComplete"):
        raise RefreshStateMutationError(
            f"{source_key} candidate was not evaluated against protected accepted source lineage"
        )
    attempt = state.get("lastAttempt")
    if not isinstance(attempt, dict) or attempt.get("snapshotID") != expected_snapshot:
        raise RefreshStateMutationError(
            f"{source_key} refresh attempt does not bind the reviewed normalized snapshot"
        )

    candidate = state.get("candidateComplete")
    eligible = state.get("candidateEligible")
    changed = state.get("candidateChangedFromAccepted")
    if candidate is None:
        if eligible is not False or changed is not False:
            raise RefreshStateMutationError(f"{source_key} empty candidate has inconsistent eligibility flags")
        return None
    if (
        eligible is not True
        or changed is not True
        or not isinstance(candidate, dict)
        or candidate.get("status") != "complete"
        or candidate.get("snapshotID") != expected_snapshot
    ):
        raise RefreshStateMutationError(f"{source_key} candidate does not bind the reviewed normalized snapshot")
    if candidate.get("mode") == "full":
        if state.get("lastSuccessfulFullAcquisitionAt") != candidate.get("retrievedAt"):
            raise RefreshStateMutationError(f"{source_key} full acquisition clock does not match reviewed snapshot")
        if state.get("lastSuccessfulFullSnapshotID") != candidate.get("snapshotID"):
            raise RefreshStateMutationError(f"{source_key} full acquisition snapshot clock does not match reviewed snapshot")
    try:
        promoted = promote_state(policy, state)
        validate_operational_state(promoted)
    except (RefreshError, OperationalRefreshError) as exc:
        raise RefreshStateMutationError(f"{source_key} candidate refresh promotion failed: {exc}") from exc
    return promoted


def materialize(
    *,
    client: Client,
    policy: dict[str, Any],
    candidate_states: list[dict[str, Any]],
    release_input: dict[str, Any],
    normalized_evidence: dict[str, Any],
    base_sha: str,
) -> dict[str, Any]:
    validate_policy(policy)
    receipt = validate_release_input(release_input)
    if receipt["reviewedSourceCommit"] != base_sha:
        raise RefreshStateMutationError("refresh promotion base differs from reviewed catalog source commit")
    if not candidate_states:
        raise RefreshStateMutationError("at least one reviewed refresh candidate state is required")

    source_snapshots = _source_snapshots(normalized_evidence)
    if source_snapshots.get(receipt["sourceKey"]) != receipt["snapshotId"]:
        raise RefreshStateMutationError("reviewed aggregate evidence does not bind the catalog proposal source snapshot")

    by_source: dict[str, dict[str, Any]] = {}
    for state in candidate_states:
        _validate_state(state, "candidate")
        source_key = state.get("sourceKey")
        if source_key not in STATE_PATHS or source_key not in policy.get("sources", {}):
            raise RefreshStateMutationError("candidate refresh source has no admitted state path")
        if source_key in by_source:
            raise RefreshStateMutationError(f"duplicate candidate refresh state for {source_key}")
        if state.get("market") != policy.get("market"):
            raise RefreshStateMutationError(f"{source_key} candidate refresh market differs from policy")
        if source_key not in source_snapshots:
            raise RefreshStateMutationError(f"reviewed aggregate evidence does not contain {source_key}")
        by_source[source_key] = state

    base_states: dict[str, dict[str, Any]] = {}
    desired_states: dict[str, dict[str, Any] | None] = {}
    for source_key in sorted(by_source):
        state_path = STATE_PATHS[source_key]
        response = client.get_optional(_content_path(state_path, base_sha))
        if response is None:
            raise RefreshStateMutationError(f"protected base refresh state is missing for {source_key}")
        base_state = _decode_json(response, f"protected base {source_key} refresh state")
        _validate_state(base_state, f"protected base {source_key}")
        if base_state.get("sourceKey") != source_key or base_state.get("market") != policy.get("market"):
            raise RefreshStateMutationError(f"protected base {source_key} refresh state identity mismatch")
        base_states[source_key] = base_state
        desired_states[source_key] = _promotion_candidate(
            policy=policy,
            state=by_source[source_key],
            base_state=base_state,
            expected_snapshot=source_snapshots[source_key],
        )

    accepted_receipt_response = client.get_optional(_content_path(RECEIPT_PATH, base_sha))
    if accepted_receipt_response is not None:
        accepted_receipt = validate_release_input(
            _decode_json(accepted_receipt_response, "protected base release receipt")
        )
        if accepted_receipt["logicalCatalogSha256"] == receipt["logicalCatalogSha256"]:
            return {
                "branch": receipt["proposalKey"],
                "unchanged": True,
                "promotions": {
                    source_key: {"promoted": False, "statePath": STATE_PATHS[source_key]}
                    for source_key in sorted(by_source)
                },
            }

    branch = receipt["proposalKey"]
    if client.get_optional(_ref_path(branch)) is None:
        raise RefreshStateMutationError("catalog proposal branch does not exist")

    branch_receipt_response = client.get_optional(_content_path(RECEIPT_PATH, branch))
    if branch_receipt_response is None:
        raise RefreshStateMutationError("catalog proposal branch lacks reviewed release receipt")
    branch_receipt = validate_release_input(
        _decode_json(branch_receipt_response, "proposal branch release receipt")
    )
    if branch_receipt != receipt:
        raise RefreshStateMutationError("proposal branch receipt differs from reviewed release input")

    promotion_results: dict[str, dict[str, Any]] = {}
    changed_paths: set[str] = set()
    for source_key in sorted(by_source):
        state_path = STATE_PATHS[source_key]
        promoted = desired_states[source_key]
        branch_state_response = client.get_optional(_content_path(state_path, branch))
        if branch_state_response is None:
            raise RefreshStateMutationError(
                f"proposal branch did not inherit protected refresh state for {source_key}"
            )
        current = _decode_content(branch_state_response, f"proposal branch {source_key} refresh state")
        base_bytes = _json_bytes(base_states[source_key])
        if promoted is None:
            if current != base_bytes:
                raise RefreshStateMutationError(
                    f"unchanged {source_key} proposal branch state differs from protected base"
                )
            promotion_results[source_key] = {
                "promoted": False,
                "statePath": state_path,
                "acceptedSnapshotID": (
                    base_states[source_key].get("acceptedComplete") or {}
                ).get("snapshotID"),
            }
            continue

        desired = _json_bytes(promoted)
        changed_paths.add(state_path)
        if current != desired:
            if current != base_bytes:
                raise RefreshStateMutationError(
                    f"proposal branch {source_key} refresh state already differs from protected base"
                )
            blob_sha = branch_state_response.get("sha") if isinstance(branch_state_response, dict) else None
            if not isinstance(blob_sha, str) or not blob_sha:
                raise RefreshStateMutationError(
                    f"proposal branch {source_key} refresh state blob SHA is unavailable"
                )
            client.put(
                "/contents/" + urllib.parse.quote(state_path, safe="/"),
                {
                    "message": f"Accept {source_key} refresh state for {source_snapshots[source_key]}",
                    "content": base64.b64encode(desired).decode("ascii"),
                    "branch": branch,
                    "sha": blob_sha,
                },
            )
        promotion_results[source_key] = {
            "promoted": True,
            "statePath": state_path,
            "acceptedSnapshotID": promoted["acceptedComplete"]["snapshotID"],
            "stateSha256": promoted["stateSha256"],
        }

    comparison = client.get(_compare_path(base_sha, branch))
    if not isinstance(comparison, dict) or not isinstance(comparison.get("files"), list):
        raise RefreshStateMutationError("proposal branch comparison is unavailable")
    paths = {
        item.get("filename")
        for item in comparison["files"]
        if isinstance(item, dict) and isinstance(item.get("filename"), str)
    }
    expected = {RECEIPT_PATH} | changed_paths
    if paths != expected:
        raise RefreshStateMutationError(
            "catalog proposal branch contains unexpected paths after refresh promotion"
        )

    return {
        "branch": branch,
        "unchanged": False,
        "promotions": promotion_results,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", type=Path, default=Path("Data/refresh/catalog-refresh-policy-v1.json"))
    parser.add_argument("--candidate-state", type=Path, action="append", required=True)
    parser.add_argument("--release-input", type=Path, required=True)
    parser.add_argument("--normalized-evidence", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--base-sha", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        policy = _load_object(args.policy, "refresh policy")
        states = [
            _load_object(path, f"candidate refresh state {index}")
            for index, path in enumerate(args.candidate_state)
        ]
        release_input = _load_object(args.release_input, "catalog release input")
        normalized_evidence = _load_object(args.normalized_evidence, "reviewed normalized evidence")
        client = GitHubClient(token=os.environ.get("GITHUB_TOKEN", ""), repository=args.repository)
        result = materialize(
            client=client,
            policy=policy,
            candidate_states=states,
            release_input=release_input,
            normalized_evidence=normalized_evidence,
            base_sha=args.base_sha,
        )
    except (RefreshStateMutationError, RefreshError, ReleaseInputError) as exc:
        raise SystemExit(f"refresh state proposal materialization failed: {exc}") from exc
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
