#!/usr/bin/env python3
"""Fetch source-specific status for the trusted scheduled catalog refresh workflow."""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class WorkflowStatusError(ValueError):
    pass


def _request(url: str, token: str) -> dict[str, Any]:
    if not token:
        raise WorkflowStatusError("GitHub token is required")
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "halal-food-eu-refresh-health",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except (urllib.error.URLError, urllib.error.HTTPError, UnicodeDecodeError) as exc:
        raise WorkflowStatusError(f"failed to query GitHub Actions: {exc}") from exc
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise WorkflowStatusError("GitHub Actions returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise WorkflowStatusError("GitHub Actions returned an unexpected payload")
    return value


def _valid_source_key(source_key: str) -> str:
    if not source_key or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-." for char in source_key):
        raise WorkflowStatusError("source key is invalid")
    return source_key


def _display_title(source_key: str) -> str:
    return f"Catalog refresh {source_key}"


def latest_relevant_run(payload: dict[str, Any], source_key: str) -> dict[str, Any]:
    source_key = _valid_source_key(source_key)
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        raise WorkflowStatusError("workflow_runs must be an array")
    eligible = []
    expected_title = _display_title(source_key)
    for run in runs:
        if not isinstance(run, dict):
            continue
        if run.get("head_branch") != "main":
            continue
        if run.get("event") not in {"schedule", "workflow_dispatch"}:
            continue
        if run.get("display_title") != expected_title:
            continue
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        eligible.append(run)
    if not eligible:
        return {
            "schemaVersion": 1,
            "available": False,
            "workflow": "scheduled-catalog-refresh.yml",
            "sourceKey": source_key,
            "runId": None,
            "event": None,
            "status": None,
            "conclusion": None,
            "createdAt": None,
            "updatedAt": None,
            "headSha": None,
        }
    latest = max(
        eligible,
        key=lambda item: (
            str(item.get("created_at") or ""),
            int(item["id"]),
        ),
    )
    return {
        "schemaVersion": 1,
        "available": True,
        "workflow": "scheduled-catalog-refresh.yml",
        "sourceKey": source_key,
        "runId": str(latest["id"]),
        "event": latest.get("event") if isinstance(latest.get("event"), str) else None,
        "status": latest.get("status") if isinstance(latest.get("status"), str) else None,
        "conclusion": latest.get("conclusion") if isinstance(latest.get("conclusion"), str) else None,
        "createdAt": latest.get("created_at") if isinstance(latest.get("created_at"), str) else None,
        "updatedAt": latest.get("updated_at") if isinstance(latest.get("updated_at"), str) else None,
        "headSha": latest.get("head_sha") if isinstance(latest.get("head_sha"), str) else None,
    }


def fetch_status(*, repository: str, workflow: str, source_key: str, token: str) -> dict[str, Any]:
    if "/" not in repository or repository.startswith("/") or repository.endswith("/"):
        raise WorkflowStatusError("repository must be owner/name")
    if not workflow or "/" in workflow or ".." in workflow:
        raise WorkflowStatusError("workflow must be a workflow filename")
    source_key = _valid_source_key(source_key)
    encoded = urllib.parse.quote(workflow, safe="")
    url = (
        f"https://api.github.com/repos/{repository}/actions/workflows/{encoded}/runs"
        "?branch=main&per_page=40"
    )
    result = latest_relevant_run(_request(url, token), source_key)
    result["workflow"] = workflow
    return result


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", default="scheduled-catalog-refresh.yml")
    parser.add_argument("--source-key", required=True)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        result = fetch_status(
            repository=args.repository,
            workflow=args.workflow,
            source_key=args.source_key,
            token=os.environ.get(args.token_env, ""),
        )
        write_json(args.output, result)
        print(
            f"Scheduled refresh status: source={result['sourceKey']} "
            f"available={str(result['available']).lower()} "
            f"conclusion={result['conclusion']} run={result['runId']}"
        )
    except WorkflowStatusError as exc:
        raise SystemExit(f"catalog refresh workflow status failed: {exc}") from exc


if __name__ == "__main__":
    main()
