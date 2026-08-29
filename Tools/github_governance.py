#!/usr/bin/env python3
"""Validate and synchronize Halal Food EU GitHub issue governance."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / ".github" / "labels.json"
HEX_COLOR = re.compile(r"^[0-9A-Fa-f]{6}$")
PRIORITY_PREFIX = "priority:"
STATUS_PREFIX = "status:"
MAX_DESCRIPTION_LENGTH = 100


@dataclass(frozen=True)
class TaxonomyResult:
    issue_number: int
    priorities: tuple[str, ...]
    statuses: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return len(self.priorities) == 1 and len(self.statuses) == 1


def load_manifest(path: Path = DEFAULT_MANIFEST) -> list[dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("label manifest must be a JSON array")

    labels: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValueError(f"label entry {index} must be an object")
        name = str(item.get("name", "")).strip()
        color = str(item.get("color", "")).strip().upper()
        description = str(item.get("description", "")).strip()
        if not name:
            raise ValueError(f"label entry {index} has no name")
        if name in seen:
            raise ValueError(f"duplicate label name: {name}")
        if not HEX_COLOR.fullmatch(color):
            raise ValueError(f"label {name!r} has invalid color {color!r}")
        if not description:
            raise ValueError(f"label {name!r} has no description")
        if len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValueError(
                f"label {name!r} description is {len(description)} characters; "
                f"GitHub maximum is {MAX_DESCRIPTION_LENGTH}"
            )
        seen.add(name)
        labels.append({"name": name, "color": color, "description": description})

    priorities = [label["name"] for label in labels if label["name"].startswith(PRIORITY_PREFIX)]
    statuses = [label["name"] for label in labels if label["name"].startswith(STATUS_PREFIX)]
    if priorities != ["priority:P0", "priority:P1", "priority:P2"]:
        raise ValueError("priority labels must be exactly priority:P0, priority:P1, priority:P2 in order")
    required_statuses = {
        "status:planned",
        "status:ready",
        "status:in-progress",
        "status:review",
        "status:blocked",
        "status:blocked-external",
        "status:needs-owner-input",
        "status:done",
    }
    if set(statuses) != required_statuses:
        missing = sorted(required_statuses - set(statuses))
        extra = sorted(set(statuses) - required_statuses)
        raise ValueError(f"status label mismatch; missing={missing}, extra={extra}")
    return labels


def classify_issue(issue: dict[str, Any]) -> TaxonomyResult:
    names = tuple(
        str(label.get("name", ""))
        for label in issue.get("labels", [])
        if isinstance(label, dict)
    )
    return TaxonomyResult(
        issue_number=int(issue["number"]),
        priorities=tuple(name for name in names if name.startswith(PRIORITY_PREFIX)),
        statuses=tuple(name for name in names if name.startswith(STATUS_PREFIX)),
    )


def validate_taxonomy(issues: Iterable[dict[str, Any]]) -> list[str]:
    failures: list[str] = []
    in_progress: list[int] = []
    for issue in issues:
        if "pull_request" in issue:
            continue
        result = classify_issue(issue)
        if not result.valid:
            failures.append(
                f"issue #{result.issue_number}: priorities={list(result.priorities)}, statuses={list(result.statuses)}"
            )
        if result.statuses == ("status:in-progress",):
            in_progress.append(result.issue_number)
    if len(in_progress) > 1:
        failures.append(f"more than one issue is in progress: {in_progress}")
    return failures


def github_request(method: str, url: str, token: str, payload: Any | None = None) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "HalalFoodEU-Governance/1",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
            return None if not body else json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"GitHub API {method} {url} failed: HTTP {error.code}: {detail}") from error


def github_context() -> tuple[str, str]:
    repository = os.environ.get("GITHUB_REPOSITORY", "").strip()
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not repository or "/" not in repository:
        raise RuntimeError("GITHUB_REPOSITORY is required, e.g. StreamScapeTV/halal-food-eu")
    if not token:
        raise RuntimeError("GITHUB_TOKEN is required")
    return repository, token


def paged_get(url: str, token: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    page = 1
    while True:
        separator = "&" if "?" in url else "?"
        payload = github_request("GET", f"{url}{separator}per_page=100&page={page}", token)
        if not isinstance(payload, list):
            raise RuntimeError(f"expected list from {url}")
        items.extend(item for item in payload if isinstance(item, dict))
        if len(payload) < 100:
            return items
        page += 1


def sync_labels(manifest: list[dict[str, str]]) -> None:
    repository, token = github_context()
    api = f"https://api.github.com/repos/{repository}"
    existing = {
        str(label["name"]): label
        for label in paged_get(f"{api}/labels", token)
        if "name" in label
    }
    for desired in manifest:
        name = desired["name"]
        current = existing.get(name)
        if current is None:
            github_request("POST", f"{api}/labels", token, desired)
            print(f"created {name}")
            continue
        current_color = str(current.get("color", "")).upper()
        current_description = str(current.get("description") or "")
        if current_color == desired["color"] and current_description == desired["description"]:
            print(f"unchanged {name}")
            continue
        encoded = urllib.parse.quote(name, safe="")
        github_request("PATCH", f"{api}/labels/{encoded}", token, desired)
        print(f"updated {name}")


def validate_remote_roadmap() -> None:
    repository, token = github_context()
    issues = paged_get(f"https://api.github.com/repos/{repository}/issues?state=all", token)
    failures = validate_taxonomy(issues)
    if failures:
        print("GitHub issue taxonomy validation failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Validated taxonomy for {sum(1 for issue in issues if 'pull_request' not in issue)} issues")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("validate-manifest", "sync-labels", "validate-roadmap"),
    )
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    if args.command == "validate-manifest":
        print(f"Validated {len(manifest)} managed labels")
    elif args.command == "sync-labels":
        sync_labels(manifest)
    else:
        validate_remote_roadmap()


if __name__ == "__main__":
    main()
