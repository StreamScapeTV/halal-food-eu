#!/usr/bin/env python3
"""Locate the newest successful source-refresh artifact set in GitHub Actions."""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class ArtifactLocatorError(ValueError):
    pass


ARTIFACT_PREFIXES = {
    "normalized": "normalized-{source}-",
    "change": "changes-{source}-",
    "quality": "quality-{source}-",
    "refreshState": "refresh-state-{source}-",
    "refreshReport": "refresh-report-{source}-",
    "refreshQueue": "refresh-queue-{source}-",
}


def _request(url: str, token: str) -> dict[str, Any]:
    if not token:
        raise ArtifactLocatorError("GitHub token is required")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "halal-food-eu-refresh-artifact-locator",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, urllib.error.HTTPError, UnicodeDecodeError) as exc:
        raise ArtifactLocatorError(f"failed to query GitHub Actions: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ArtifactLocatorError("GitHub Actions returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ArtifactLocatorError("GitHub Actions returned an unexpected payload")
    return value


def _artifact_set(artifacts: list[Any], source_key: str) -> dict[str, str] | None:
    matched: dict[str, list[str]] = {key: [] for key in ARTIFACT_PREFIXES}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("expired") is True:
            continue
        name = artifact.get("name")
        if not isinstance(name, str):
            continue
        for key, template in ARTIFACT_PREFIXES.items():
            prefix = template.format(source=source_key)
            if not name.startswith(prefix):
                continue
            if key in {"normalized", "change", "quality"} and name.endswith("-aggregate"):
                continue
            matched[key].append(name)
    if any(len(values) != 1 for values in matched.values()):
        return None
    return {key: values[0] for key, values in matched.items()}


def select_latest(
    runs: list[Any],
    artifacts_by_run: dict[int, list[Any]],
    source_key: str,
) -> dict[str, Any]:
    eligible: list[tuple[str, int, dict[str, Any], dict[str, str]]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        if run.get("head_branch") != "main" or run.get("conclusion") != "success":
            continue
        if run.get("event") not in {"schedule", "workflow_dispatch"}:
            continue
        artifact_set = _artifact_set(artifacts_by_run.get(run_id, []), source_key)
        if artifact_set is None:
            continue
        created = run.get("created_at") if isinstance(run.get("created_at"), str) else ""
        eligible.append((created, run_id, run, artifact_set))
    if not eligible:
        return {
            "schemaVersion": 1,
            "available": False,
            "sourceKey": source_key,
            "runId": None,
            "event": None,
            "createdAt": None,
            "updatedAt": None,
            "headSha": None,
            "artifacts": {},
        }
    _, run_id, run, artifacts = max(eligible, key=lambda item: (item[0], item[1]))
    return {
        "schemaVersion": 1,
        "available": True,
        "sourceKey": source_key,
        "runId": str(run_id),
        "event": run.get("event") if isinstance(run.get("event"), str) else None,
        "createdAt": run.get("created_at") if isinstance(run.get("created_at"), str) else None,
        "updatedAt": run.get("updated_at") if isinstance(run.get("updated_at"), str) else None,
        "headSha": run.get("head_sha") if isinstance(run.get("head_sha"), str) else None,
        "artifacts": artifacts,
    }


def locate(*, repository: str, workflow: str, source_key: str, token: str) -> dict[str, Any]:
    if "/" not in repository or repository.startswith("/") or repository.endswith("/"):
        raise ArtifactLocatorError("repository must be owner/name")
    if not workflow or "/" in workflow or ".." in workflow:
        raise ArtifactLocatorError("workflow must be a workflow filename")
    if not source_key or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-." for char in source_key):
        raise ArtifactLocatorError("source key is invalid")
    encoded = urllib.parse.quote(workflow, safe="")
    runs_payload = _request(
        f"https://api.github.com/repos/{repository}/actions/workflows/{encoded}/runs?branch=main&per_page=20",
        token,
    )
    runs = runs_payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise ArtifactLocatorError("workflow_runs must be an array")

    artifacts_by_run: dict[int, list[Any]] = {}
    for run in sorted(
        (item for item in runs if isinstance(item, dict) and isinstance(item.get("id"), int)),
        key=lambda item: (str(item.get("created_at") or ""), int(item["id"])),
        reverse=True,
    ):
        if run.get("head_branch") != "main" or run.get("conclusion") != "success":
            continue
        if run.get("event") not in {"schedule", "workflow_dispatch"}:
            continue
        run_id = int(run["id"])
        payload = _request(
            f"https://api.github.com/repos/{repository}/actions/runs/{run_id}/artifacts?per_page=100",
            token,
        )
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list):
            raise ArtifactLocatorError("artifacts must be an array")
        artifacts_by_run[run_id] = artifacts
        if _artifact_set(artifacts, source_key) is not None:
            break
    return select_latest(runs, artifacts_by_run, source_key)


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", default="scheduled-catalog-refresh.yml")
    parser.add_argument("--source-key", default="open-food-facts")
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        result = locate(
            repository=args.repository,
            workflow=args.workflow,
            source_key=args.source_key,
            token=os.environ.get(args.token_env, ""),
        )
        write_json(args.output, result)
        print(
            f"Refresh artifacts: source={result['sourceKey']} "
            f"available={str(result['available']).lower()} run={result['runId']}"
        )
    except ArtifactLocatorError as exc:
        raise SystemExit(f"catalog refresh artifact location failed: {exc}") from exc


if __name__ == "__main__":
    main()
