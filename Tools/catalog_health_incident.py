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
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MARKER_PREFIX = "catalog-health-key:"
TITLE_PREFIX = "[Catalog health]"
PRIORITY_PREFIX = "priority:"
STATUS_PREFIX = "status:"
P0_PRIORITY = "priority:P0"
P1_PRIORITY = "priority:P1"
ACTIVE_STATUS = "status:blocked"
RESOLVED_STATUS = "status:done"
BASE_CLASSIFICATION_LABELS = ("type:data-quality", "area:observability")
REFRESH_CLASSIFICATION_LABEL = "area:sources"
P1_INCIDENT_ACTIONS = {"investigate-refresh"}


class HealthIncidentError(ValueError):
    pass


@dataclass(frozen=True)
class Incident:
    key: str
    title: str
    body: str
    labels: tuple[str, ...]


def load_report(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HealthIncidentError(f"failed to read health report: {exc}") from exc
    if not isinstance(value, dict):
        raise HealthIncidentError("health report must be an object")
    return value


def _priority_for_action(action: str) -> str:
    # Catalog correctness/safety actions are release blockers and remain P0.
    # Refresh/coverage degradation is material production coverage work and is P1.
    return P1_PRIORITY if action in P1_INCIDENT_ACTIONS else P0_PRIORITY


def _classification_labels(key: str) -> tuple[str, ...]:
    labels = list(BASE_CLASSIFICATION_LABELS)
    if key.startswith("refresh:"):
        labels.append(REFRESH_CLASSIFICATION_LABEL)
    return tuple(labels)


def _desired_labels(
    *,
    key: str,
    priority: str,
    status: str,
    existing: Any = None,
) -> tuple[str, ...]:
    preserved: set[str] = set()
    if isinstance(existing, list):
        for label in existing:
            if not isinstance(label, dict):
                continue
            name = label.get("name")
            if not isinstance(name, str) or not name:
                continue
            if name.startswith(PRIORITY_PREFIX) or name.startswith(STATUS_PREFIX):
                continue
            preserved.add(name)
    preserved.update(_classification_labels(key))
    return tuple(sorted({priority, status, *preserved}))


def _resolved_priority(key: str, current: dict[str, Any]) -> str:
    names = [
        label.get("name")
        for label in current.get("labels", [])
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    ]
    priorities = [name for name in names if name.startswith(PRIORITY_PREFIX)]
    if len(priorities) == 1 and priorities[0] in {P0_PRIORITY, P1_PRIORITY}:
        return priorities[0]
    return P1_PRIORITY if key.startswith("refresh:") else P0_PRIORITY


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
    action = (
        str(quality.get("incident", {}).get("action", "block-release"))
        if isinstance(quality.get("incident"), dict)
        else "block-release"
    )
    priority = _priority_for_action(action)
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
        incidents.append(
            Incident(
                key=key,
                title=f"{TITLE_PREFIX} {key}",
                body=body,
                labels=_desired_labels(
                    key=key,
                    priority=priority,
                    status=ACTIVE_STATUS,
                ),
            )
        )
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
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else None
    except (urllib.error.URLError, urllib.error.HTTPError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HealthIncidentError(f"GitHub issue synchronization failed: {exc}") from exc


def _marker_key(body: Any) -> str | None:
    if not isinstance(body, str):
        return None
    prefix = f"<!-- {MARKER_PREFIX}"
    for line in body.splitlines():
        if line.startswith(prefix) and line.endswith(" -->"):
            key = line[len(prefix):-4]
            return key or None
    return None


def _existing_health_issues(repository: str, token: str) -> dict[str, dict[str, Any]]:
    """List issues directly so deduplication is not subject to search-index delay."""
    existing: dict[str, dict[str, Any]] = {}
    base = f"https://api.github.com/repos/{repository}/issues"
    for page in range(1, 11):
        result = _request("GET", f"{base}?state=all&per_page=100&page={page}", token)
        if not isinstance(result, list):
            raise HealthIncidentError("GitHub issue listing returned an unexpected payload")
        if not result:
            break
        for item in result:
            if not isinstance(item, dict) or "pull_request" in item:
                continue
            key = _marker_key(item.get("body"))
            if key:
                existing[key] = item
        if len(result) < 100:
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
            _request(
                "POST",
                base,
                token,
                {
                    "title": incident.title,
                    "body": incident.body,
                    "labels": list(incident.labels),
                },
            )
            created += 1
        else:
            number = current.get("number")
            if not isinstance(number, int):
                raise HealthIncidentError("existing health issue lacks number")
            labels = _desired_labels(
                key=key,
                priority=_priority_for_action(
                    str(report.get("qualityGate", {}).get("incident", {}).get("action", "block-release"))
                    if isinstance(report.get("qualityGate", {}).get("incident"), dict)
                    else "block-release"
                ),
                status=ACTIVE_STATUS,
                existing=current.get("labels"),
            )
            _request(
                "PATCH",
                f"{base}/{number}",
                token,
                {
                    "title": incident.title,
                    "body": incident.body,
                    "state": "open",
                    "labels": list(labels),
                },
            )
            updated += 1
    for key, current in existing.items():
        if key in planned or current.get("state") != "open":
            continue
        number = current.get("number")
        if not isinstance(number, int):
            continue
        body = str(current.get("body") or "") + "\n\nResolved by a later trusted catalog-health evaluation.\n"
        labels = _desired_labels(
            key=key,
            priority=_resolved_priority(key, current),
            status=RESOLVED_STATUS,
            existing=current.get("labels"),
        )
        _request(
            "PATCH",
            f"{base}/{number}",
            token,
            {
                "body": body,
                "state": "closed",
                "state_reason": "completed",
                "labels": list(labels),
            },
        )
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
