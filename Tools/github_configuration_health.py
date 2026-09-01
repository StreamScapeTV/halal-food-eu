#!/usr/bin/env python3
"""Reconcile the single configuration owner-input issue from a safe health report."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

MARKER = "<!-- hfeu-configuration-health:owner-input-v1 -->"
TITLE = "[Configuration Health] Owner input required"
LABELS = ["priority:P0", "status:needs-owner-input", "type:policy", "area:workflows", "needs:owner-action"]


class ReconcileError(ValueError):
    pass


def _load_report(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReconcileError(f"failed to load configuration health report: {exc}") from exc
    required = {"schemaVersion", "status", "ownerInputRequired", "deduplicationKey", "publicConfiguration", "sources", "blockers"}
    if not isinstance(raw, dict) or set(raw) != required or raw.get("schemaVersion") != 1:
        raise ReconcileError("configuration health report has unsupported schema")
    if raw["status"] not in {"healthy", "blocked"} or raw["ownerInputRequired"] is not (raw["status"] == "blocked"):
        raise ReconcileError("configuration health report status is inconsistent")
    if raw["deduplicationKey"] != "hfeu:configuration-health:owner-input:v1":
        raise ReconcileError("configuration health report has unexpected deduplication key")
    if not isinstance(raw["blockers"], list) or not isinstance(raw["sources"], list):
        raise ReconcileError("configuration health report arrays are invalid")
    return raw


def _request_json(method: str, url: str, token: str, payload: dict[str, Any] | None = None) -> Any:
    data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "HalalFoodEU-ConfigurationHealth/1.0",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise ReconcileError(f"GitHub API {method} failed with HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise ReconcileError(f"GitHub API {method} failed: {exc}") from exc
    if not body:
        return None
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ReconcileError("GitHub API returned invalid JSON") from exc


def _existing_issue(repository: str, token: str) -> dict[str, Any] | None:
    owner, repo = repository.split("/", 1)
    for page in range(1, 11):
        query = urllib.parse.urlencode({"state": "all", "per_page": 100, "page": page})
        items = _request_json("GET", f"https://api.github.com/repos/{owner}/{repo}/issues?{query}", token)
        if not isinstance(items, list):
            raise ReconcileError("GitHub issue listing returned an unexpected shape")
        for item in items:
            if not isinstance(item, dict) or "pull_request" in item:
                continue
            body = item.get("body")
            if isinstance(body, str) and MARKER in body:
                return item
        if len(items) < 100:
            return None
    raise ReconcileError("configuration health issue search exceeded pagination bound")


def _issue_body(report: dict[str, Any]) -> str:
    lines = [
        MARKER,
        "## Configuration owner action required",
        "",
        "An approved source is enabled but its exact required credential set is not fully configured.",
        "This issue records credential **names and configured/not-configured state only**; secret values are never read or written here.",
        "",
        "| Source | Authentication | Required secret | Configured |",
        "| --- | --- | --- | --- |",
    ]
    source_rows = {item.get("sourceKey"): item for item in report["sources"] if isinstance(item, dict)}
    for blocker in report["blockers"]:
        if not isinstance(blocker, dict):
            continue
        source_key = str(blocker.get("sourceKey", "unknown"))
        source = source_rows.get(source_key, {})
        mode = str(source.get("authenticationMode", "unknown"))
        states = source.get("requiredSecrets", []) if isinstance(source, dict) else []
        for state in states:
            if not isinstance(state, dict) or not isinstance(state.get("name"), str):
                continue
            configured = "yes" if state.get("configured") is True else "no"
            lines.append(f"| `{source_key}` | `{mode}` | `{state['name']}` | {configured} |")
    lines += [
        "",
        "### Required action",
        "Configure the missing repository/environment credential names exactly as documented by the approved source-specific credential policy, then rerun Configuration health.",
        "Do not paste credential values into this issue.",
        "",
        f"Deduplication key: `{report['deduplicationKey']}`",
    ]
    return "\n".join(lines) + "\n"


def reconcile(report: dict[str, Any], repository: str, token: str) -> dict[str, Any]:
    if repository.count("/") != 1 or any(not part for part in repository.split("/")):
        raise ReconcileError("repository must use owner/name form")
    if not token:
        raise ReconcileError("GitHub token is required")
    existing = _existing_issue(repository, token)
    owner, repo = repository.split("/", 1)
    if report["ownerInputRequired"]:
        payload = {"title": TITLE, "body": _issue_body(report), "labels": LABELS, "state": "open"}
        if existing is None:
            result = _request_json("POST", f"https://api.github.com/repos/{owner}/{repo}/issues", token, payload)
            return {"action": "created", "issueNumber": result.get("number") if isinstance(result, dict) else None}
        number = existing.get("number")
        if not isinstance(number, int):
            raise ReconcileError("existing configuration health issue has no numeric issue number")
        result = _request_json("PATCH", f"https://api.github.com/repos/{owner}/{repo}/issues/{number}", token, payload)
        return {"action": "updated", "issueNumber": result.get("number") if isinstance(result, dict) else number}

    if existing is None:
        return {"action": "none", "issueNumber": None}
    number = existing.get("number")
    if not isinstance(number, int):
        raise ReconcileError("existing configuration health issue has no numeric issue number")
    if existing.get("state") == "open":
        _request_json(
            "PATCH",
            f"https://api.github.com/repos/{owner}/{repo}/issues/{number}",
            token,
            {"state": "closed", "state_reason": "completed"},
        )
        return {"action": "closed", "issueNumber": number}
    return {"action": "none", "issueNumber": number}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--token-env", default="GITHUB_TOKEN")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        report = _load_report(args.report)
        token = os.environ.get(args.token_env, "")
        result = reconcile(report, args.repository, token)
        print(json.dumps(result, sort_keys=True))
        return 0
    except ReconcileError as exc:
        print(f"configuration health reconciliation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
