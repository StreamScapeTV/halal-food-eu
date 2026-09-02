#!/usr/bin/env python3
"""Synchronize aggregate-only catalog health incidents with GitHub Issues.

The networked `sync` command is intended only for a trusted default-branch
workflow with bounded `issues: write` permission. Planning is deterministic and
contains no raw source payloads.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MARKER_PREFIX = "catalog-health-key:"
TITLE_PREFIX = "[Catalog health]"


class HealthIncidentError(ValueError):
    pass


@dataclass(frozen=True)
class Incident:
    key: str
    title: str
    body: str


def load_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HealthIncidentError(f"failed to read health report: {exc}") from exc
    if not isinstance(value, dict):
        raise HealthIncidentError("health report must be an object")
    return value


def plan_incidents(report: dict[str, Any]) -> list[Incident]:
    quality = report.get("qualityGate")
    if not isinstance(quality, dict):
        raise HealthIncidentError("health report lacks qualityGate")
    keys = quality.get("deduplicationKeys", [])
    if not isinstance(keys, list) or any(not isinstance(item, str) or not item for item in keys):
        raise HealthIncidentError("qualityGate deduplicationKeys must be strings")
    codes = report.get("assessments", {}).get("invalidatedOrBlockingCodes", [])
    if not isinstance(codes, list):
        codes = []
    commit = str(report.get("commitSha", "unknown"))
    evaluated = str(report.get("evaluatedAt", "unknown"))
    action = str(quality.get("incident", {}).get("action", "block-release")) if isinstance(quality.get("incident"), dict) else "block-release"
    incidents = []
    for key in sorted(set(keys)):
        marker = f"<!-- {MARKER_PREFIX}{key} -->"
        body = "\n".join([
            marker,
            "This issue is maintained by the trusted catalog-health workflow.",
            "",
            f"- Health key: `{key}`",
            f"- Current action: `{action}`",
            f"- Blocking codes: `{json.dumps(sorted(str(code) for code in codes))}`",
            f"- Evaluated commit: `{commit}`",
            f"- Evaluated at: `{evaluated}`",
            "",
            "The issue contains aggregate health metadata only. Inspect the exact workflow artifact for the machine-readable report.",
        ])
        incidents.append(Incident(key=key, title=f"{TITLE_PREFIX} {key}", body=body))
    return incidents


def _request(method: str, url: str, token: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "halal-food-eu-catalog-health",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HealthIncidentError(f"GitHub issue synchronization failed: {exc}") from exc


def _existing_health_issues(repository: str, token: str) -> dict[str, dict[str, Any]]:
    query = urllib.parse.quote(f'repo:{repository} is:issue in:body "{MARKER_PREFIX}"')
    result = _request("GET", f"https://api.github.com/search/issues?q={query}&per_page=100", token)
    items = result.get("items", []) if isinstance(result, dict) else []
    existing: dict[str, dict[str, Any]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        body = item.get("body")
        if not isinstance(body, str):
            continue
        for line in body.splitlines():
            prefix = f"<!-- {MARKER_PREFIX}"
            if line.startswith(prefix) and line.endswith(" -->"):
                key = line[len(prefix):-4]
                if key:
                    existing[key] = item
                break
    return existing


def synchronize(report: dict[str, Any], repository: str, token: str) -> dict[str, int]:
    if "/" not in repository or repository.startswith("/") or repository.endswith("/"):
        raise HealthIncidentError("repository must be owner/name")
    if not token:
        raise HealthIncidentError("GitHub token is required")
    planned = {item.key: item for item in plan_incidents(report)}
    existing = _existing_health_issues(repository, token)
    created = updated = closed = 0
    base = f"https://api.github.com/repos/{repository}/issues"
    for key, incident in planned.items():
        current = existing.get(key)
        if current is None:
            _request("POST", base, token, {"title": incident.title, "body": incident.body})
            created += 1
        else:
            number = current.get("number")
            if not isinstance(number, int):
                raise HealthIncidentError("existing health issue lacks number")
            _request("PATCH", f"{base}/{number}", token, {"title": incident.title, "body": incident.body, "state": "open"})
            updated += 1
    for key, current in existing.items():
        if key in planned or current.get("state") != "open":
            continue
        number = current.get("number")
        if not isinstance(number, int):
            continue
        body = str(current.get("body") or "") + "\n\nResolved by a later trusted catalog-health evaluation.\n"
        _request("PATCH", f"{base}/{number}", token, {"body": body, "state": "closed", "state_reason": "completed"})
        closed += 1
    return {"created": created, "updated": updated, "closed": closed}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="print the aggregate incident plan without network access")
    plan.add_argument("--report", type=Path, required=True)
    sync = sub.add_parser("sync", help="create/update/close deduplicated GitHub health issues")
    sync.add_argument("--report", type=Path, required=True)
    sync.add_argument("--repository", required=True)
    sync.add_argument("--token-env", default="GITHUB_TOKEN")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        report = load_report(args.report)
        if args.command == "plan":
            print(json.dumps([item.__dict__ for item in plan_incidents(report)], indent=2, sort_keys=True))
            return
        token = os.environ.get(args.token_env, "")
        result = synchronize(report, args.repository, token)
        print(json.dumps(result, sort_keys=True))
    except HealthIncidentError as exc:
        raise SystemExit(f"catalog health incident sync failed: {exc}") from exc


if __name__ == "__main__":
    main()
