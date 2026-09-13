#!/usr/bin/env python3
"""Fetch source-specific status for the trusted scheduled catalog refresh workflow.

The bounded status contract intentionally survives expiry of large Actions artifacts.
For current runs it prefers an explicit successful ``refresh-lineage`` job marker. For
historical scheduled runs created before that marker existed, it can reconstruct only
the deterministic schedule-owned full snapshot identity and required successful job
graph. Manual runs without the explicit marker never become a successful-full fallback.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class WorkflowStatusError(ValueError):
    pass


SAFE_SNAPSHOT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
LINEAGE_JOB_PREFIX = "refresh-lineage / "
REQUIRED_SUCCESS_JOBS = ("policy", "acquire", "normalize", "quality", "refresh")
SCHEDULED_SNAPSHOT_PREFIX = {
    "open-food-facts": "off-scheduled-",
    "open-prices": "op-scheduled-",
}


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


def _empty_status(source_key: str, workflow: str = "scheduled-catalog-refresh.yml") -> dict[str, Any]:
    return {
        "schemaVersion": 2,
        "available": False,
        "workflow": workflow,
        "sourceKey": source_key,
        "runId": None,
        "event": None,
        "status": None,
        "conclusion": None,
        "createdAt": None,
        "updatedAt": None,
        "headSha": None,
        "mode": None,
        "snapshotID": None,
        "sourceContentSha256": None,
        "completeness": None,
        "qualityStatus": None,
        "successfulFullAcquisitionAt": None,
        "fullAcquisitionSucceeded": False,
        "lineageSource": None,
        "lastSuccessfulFullAcquisitionAt": None,
        "lastSuccessfulFullSnapshotID": None,
        "lastSuccessfulFullRunId": None,
        "lastSuccessfulFullHeadSha": None,
        "lastSuccessfulFullLineageSource": None,
        "requiredJobs": {},
    }


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
        title = run.get("display_title")
        if not isinstance(title, str) or not (
            title == expected_title or title.startswith(expected_title + " ")
        ):
            continue
        run_id = run.get("id")
        if not isinstance(run_id, int):
            continue
        eligible.append(run)
    if not eligible:
        return _empty_status(source_key)
    latest = max(
        eligible,
        key=lambda item: (
            str(item.get("created_at") or ""),
            int(item["id"]),
        ),
    )
    head_sha = latest.get("head_sha") if isinstance(latest.get("head_sha"), str) else None
    if head_sha is not None and not SHA40.fullmatch(head_sha):
        raise WorkflowStatusError("workflow status head SHA is invalid")
    result = _empty_status(source_key)
    result.update(
        available=True,
        runId=str(latest["id"]),
        event=latest.get("event") if isinstance(latest.get("event"), str) else None,
        status=latest.get("status") if isinstance(latest.get("status"), str) else None,
        conclusion=latest.get("conclusion") if isinstance(latest.get("conclusion"), str) else None,
        createdAt=latest.get("created_at") if isinstance(latest.get("created_at"), str) else None,
        updatedAt=latest.get("updated_at") if isinstance(latest.get("updated_at"), str) else None,
        headSha=head_sha,
    )
    return result


def _logical_job(name: str) -> str | None:
    if name in REQUIRED_SUCCESS_JOBS:
        return name
    for logical in REQUIRED_SUCCESS_JOBS:
        if name.endswith(f" / {logical}"):
            return logical
    return None


def _parse_lineage_marker(name: str) -> tuple[str, str, str] | None:
    if not name.startswith(LINEAGE_JOB_PREFIX):
        return None
    parts = [item.strip() for item in name.split(" / ")]
    if len(parts) != 4 or parts[0] != "refresh-lineage":
        raise WorkflowStatusError("refresh lineage job name is malformed")
    _, source_key, mode, snapshot_id = parts
    _valid_source_key(source_key)
    if mode not in {"fixture", "full"}:
        raise WorkflowStatusError("refresh lineage job mode is invalid")
    if not SAFE_SNAPSHOT.fullmatch(snapshot_id):
        raise WorkflowStatusError("refresh lineage job snapshot ID is invalid")
    return source_key, mode, snapshot_id


def apply_job_lineage(status: dict[str, Any], jobs_payload: dict[str, Any]) -> dict[str, Any]:
    """Project durable run/job metadata into one bounded refresh-lineage status."""
    result = json.loads(json.dumps(status))
    if result.get("available") is not True:
        return result
    jobs = jobs_payload.get("jobs")
    if not isinstance(jobs, list):
        raise WorkflowStatusError("jobs must be an array")

    required: dict[str, dict[str, Any]] = {}
    marker: tuple[str, str, str] | None = None
    marker_conclusion: str | None = None
    for job in jobs:
        if not isinstance(job, dict):
            continue
        name = job.get("name")
        if not isinstance(name, str):
            continue
        logical = _logical_job(name)
        if logical is not None:
            if logical in required:
                raise WorkflowStatusError(f"duplicate required refresh job {logical}")
            required[logical] = job
        job_conclusion = job.get("conclusion") if isinstance(job.get("conclusion"), str) else None
        parsed = _parse_lineage_marker(name) if job_conclusion == "success" else None
        if parsed is not None:
            if marker is not None:
                raise WorkflowStatusError("duplicate refresh lineage job")
            marker = parsed
            marker_conclusion = job_conclusion

    result["requiredJobs"] = {
        logical: (
            required[logical].get("conclusion")
            if logical in required and isinstance(required[logical].get("conclusion"), str)
            else None
        )
        for logical in REQUIRED_SUCCESS_JOBS
    }
    all_required_success = all(result["requiredJobs"].get(name) == "success" for name in REQUIRED_SUCCESS_JOBS)
    result["completeness"] = "complete" if all_required_success else None
    result["qualityStatus"] = "pass" if result["requiredJobs"].get("quality") == "success" else None

    source_key = result.get("sourceKey")
    event = result.get("event")
    mode: str | None = None
    snapshot_id: str | None = None
    lineage_source: str | None = None
    if marker is not None and marker_conclusion == "success":
        marker_source, marker_mode, marker_snapshot = marker
        if marker_source != source_key:
            raise WorkflowStatusError("refresh lineage job source differs from workflow status source")
        mode = marker_mode
        snapshot_id = marker_snapshot
        lineage_source = "job-marker"
    elif event == "schedule" and isinstance(source_key, str) and source_key in SCHEDULED_SNAPSHOT_PREFIX:
        run_id = result.get("runId")
        if not isinstance(run_id, str) or not run_id.isdigit():
            raise WorkflowStatusError("scheduled refresh run ID is invalid")
        mode = "full"
        snapshot_id = f"{SCHEDULED_SNAPSHOT_PREFIX[source_key]}{run_id}"
        lineage_source = "scheduled-job-graph"

    result["mode"] = mode
    result["snapshotID"] = snapshot_id
    result["lineageSource"] = lineage_source
    result["sourceContentSha256"] = None

    acquire = required.get("acquire")
    acquired_at = acquire.get("completed_at") if isinstance(acquire, dict) else None
    if acquired_at is not None and not isinstance(acquired_at, str):
        raise WorkflowStatusError("acquire job completion timestamp is invalid")
    full_success = bool(
        result.get("conclusion") == "success"
        and all_required_success
        and mode == "full"
        and isinstance(snapshot_id, str)
        and SAFE_SNAPSHOT.fullmatch(snapshot_id)
        and isinstance(acquired_at, str)
        and acquired_at
    )
    result["fullAcquisitionSucceeded"] = full_success
    result["successfulFullAcquisitionAt"] = acquired_at if full_success else None
    if full_success:
        result["lastSuccessfulFullAcquisitionAt"] = acquired_at
        result["lastSuccessfulFullSnapshotID"] = snapshot_id
        result["lastSuccessfulFullRunId"] = result.get("runId")
        result["lastSuccessfulFullHeadSha"] = result.get("headSha")
        result["lastSuccessfulFullLineageSource"] = lineage_source
    return result


def apply_last_success(latest: dict[str, Any], successful: dict[str, Any] | None) -> dict[str, Any]:
    """Attach the newest proven full success without rewriting latest-attempt fields."""
    result = json.loads(json.dumps(latest))
    if successful is None:
        return result
    if successful.get("fullAcquisitionSucceeded") is not True:
        raise WorkflowStatusError("last-success candidate is not a successful full acquisition")
    result["lastSuccessfulFullAcquisitionAt"] = successful.get("successfulFullAcquisitionAt")
    result["lastSuccessfulFullSnapshotID"] = successful.get("snapshotID")
    result["lastSuccessfulFullRunId"] = successful.get("runId")
    result["lastSuccessfulFullHeadSha"] = successful.get("headSha")
    result["lastSuccessfulFullLineageSource"] = successful.get("lineageSource")
    return result


def validate_status(status: dict[str, Any]) -> None:
    if status.get("schemaVersion") != 2:
        raise WorkflowStatusError("workflow status schemaVersion must be 2")
    source_key = status.get("sourceKey")
    if not isinstance(source_key, str):
        raise WorkflowStatusError("workflow status source key is invalid")
    _valid_source_key(source_key)
    if status.get("available") is False:
        if status.get("fullAcquisitionSucceeded") is not False:
            raise WorkflowStatusError("unavailable workflow status cannot prove a full acquisition")
        return
    if status.get("available") is not True:
        raise WorkflowStatusError("workflow status available flag is invalid")
    run_id = status.get("runId")
    if not isinstance(run_id, str) or not run_id.isdigit():
        raise WorkflowStatusError("workflow status runId is invalid")
    head_sha = status.get("headSha")
    if head_sha is not None and (not isinstance(head_sha, str) or not SHA40.fullmatch(head_sha)):
        raise WorkflowStatusError("workflow status headSha is invalid")
    required = status.get("requiredJobs")
    if not isinstance(required, dict) or set(required) != set(REQUIRED_SUCCESS_JOBS):
        raise WorkflowStatusError("workflow status requiredJobs is invalid")
    if any(value is not None and not isinstance(value, str) for value in required.values()):
        raise WorkflowStatusError("workflow status required job conclusion is invalid")
    success = status.get("fullAcquisitionSucceeded")
    if not isinstance(success, bool):
        raise WorkflowStatusError("workflow status fullAcquisitionSucceeded is invalid")
    if success:
        if status.get("conclusion") != "success":
            raise WorkflowStatusError("successful full acquisition requires successful workflow conclusion")
        if status.get("mode") != "full" or status.get("completeness") != "complete" or status.get("qualityStatus") != "pass":
            raise WorkflowStatusError("successful full acquisition metadata is inconsistent")
        snapshot_id = status.get("snapshotID")
        if not isinstance(snapshot_id, str) or not SAFE_SNAPSHOT.fullmatch(snapshot_id):
            raise WorkflowStatusError("successful full acquisition snapshot ID is invalid")
        when = status.get("successfulFullAcquisitionAt")
        if not isinstance(when, str) or not when:
            raise WorkflowStatusError("successful full acquisition timestamp is invalid")
        if any(required.get(name) != "success" for name in REQUIRED_SUCCESS_JOBS):
            raise WorkflowStatusError("successful full acquisition lacks successful required jobs")
    elif status.get("successfulFullAcquisitionAt") is not None:
        raise WorkflowStatusError("unsuccessful workflow status cannot carry full acquisition timestamp")
    last_at = status.get("lastSuccessfulFullAcquisitionAt")
    last_snapshot = status.get("lastSuccessfulFullSnapshotID")
    last_run = status.get("lastSuccessfulFullRunId")
    if (last_at is None) != (last_snapshot is None) or (last_at is None) != (last_run is None):
        raise WorkflowStatusError("last successful full workflow lineage is incomplete")
    if last_at is not None:
        if not isinstance(last_at, str) or not last_at:
            raise WorkflowStatusError("last successful full acquisition timestamp is invalid")
        if not isinstance(last_snapshot, str) or not SAFE_SNAPSHOT.fullmatch(last_snapshot):
            raise WorkflowStatusError("last successful full snapshot ID is invalid")
        if not isinstance(last_run, str) or not last_run.isdigit():
            raise WorkflowStatusError("last successful full run ID is invalid")
        last_sha = status.get("lastSuccessfulFullHeadSha")
        if last_sha is not None and (not isinstance(last_sha, str) or not SHA40.fullmatch(last_sha)):
            raise WorkflowStatusError("last successful full head SHA is invalid")
        lineage_source = status.get("lastSuccessfulFullLineageSource")
        if lineage_source not in {"job-marker", "scheduled-job-graph"}:
            raise WorkflowStatusError("last successful full lineage source is invalid")


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
    runs_payload = _request(url, token)
    result = latest_relevant_run(runs_payload, source_key)
    result["workflow"] = workflow
    if result["available"] is True:
        jobs = _request(
            f"https://api.github.com/repos/{repository}/actions/runs/{result['runId']}/jobs?filter=latest&per_page=100",
            token,
        )
        result = apply_job_lineage(result, jobs)
        result["workflow"] = workflow
        successful = result if result["fullAcquisitionSucceeded"] is True else None
        if successful is None:
            runs = runs_payload.get("workflow_runs")
            assert isinstance(runs, list)
            expected_title = _display_title(source_key)
            older = sorted(
                (
                    item
                    for item in runs
                    if isinstance(item, dict)
                    and isinstance(item.get("id"), int)
                    and str(item["id"]) != result["runId"]
                    and item.get("head_branch") == "main"
                    and item.get("event") in {"schedule", "workflow_dispatch"}
                    and item.get("conclusion") == "success"
                    and isinstance(item.get("display_title"), str)
                    and (
                        item["display_title"] == expected_title
                        or item["display_title"].startswith(expected_title + " ")
                    )
                ),
                key=lambda item: (str(item.get("created_at") or ""), int(item["id"])),
                reverse=True,
            )
            for candidate in older:
                one = latest_relevant_run({"workflow_runs": [candidate]}, source_key)
                one["workflow"] = workflow
                candidate_jobs = _request(
                    f"https://api.github.com/repos/{repository}/actions/runs/{one['runId']}/jobs?filter=latest&per_page=100",
                    token,
                )
                one = apply_job_lineage(one, candidate_jobs)
                if one["fullAcquisitionSucceeded"] is True:
                    successful = one
                    break
        result = apply_last_success(result, successful)
    validate_status(result)
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
            f"conclusion={result['conclusion']} run={result['runId']} "
            f"full={str(result['fullAcquisitionSucceeded']).lower()}"
        )
    except WorkflowStatusError as exc:
        raise SystemExit(f"catalog refresh workflow status failed: {exc}") from exc


if __name__ == "__main__":
    main()
