#!/usr/bin/env python3
"""Build deterministic source-refresh acquisition plans without network access.

Planning is deliberately separate from acquisition. A reviewed cadence may decide
that work is due without granting permission to call a new endpoint. In
particular, targeted execution remains disabled unless the endpoint host is an
explicit acquisition host in the admitted source policy.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
GTIN = re.compile(r"^[0-9]{8,14}$")


class RefreshPlanError(ValueError):
    pass


def load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshPlanError(f"failed to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RefreshPlanError(f"{path} must contain a JSON object")
    return value


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_without(value: dict[str, Any], field: str) -> str:
    work = dict(value)
    work.pop(field, None)
    return hashlib.sha256(canonical(work)).hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_time(value: Any, label: str, *, allow_none: bool = False) -> datetime | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RefreshPlanError(f"{label} must be RFC3339")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RefreshPlanError(f"{label} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise RefreshPlanError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _source_config(policy: dict[str, Any], source_key: str) -> dict[str, Any]:
    if policy.get("schemaVersion") != 1 or policy.get("market") != "DE":
        raise RefreshPlanError("unsupported refresh policy")
    version = policy.get("policyVersion")
    if not isinstance(version, str) or not SEMVER.fullmatch(version):
        raise RefreshPlanError("invalid refresh policyVersion")
    sources = policy.get("sources")
    if not isinstance(sources, dict) or source_key not in sources:
        raise RefreshPlanError("source is not admitted by refresh policy")
    source = sources[source_key]
    if not isinstance(source, dict):
        raise RefreshPlanError("source refresh policy must be an object")
    modes = source.get("supportedAcquisitionModes")
    if (
        not isinstance(modes, list)
        or not modes
        or any(item not in {"full", "delta"} for item in modes)
        or len(set(modes)) != len(modes)
        or "full" not in modes
    ):
        raise RefreshPlanError("supported acquisition modes must contain reviewed full and optional delta")
    cadence = source.get("fullCadenceDays")
    if not isinstance(cadence, int) or isinstance(cadence, bool) or cadence < 1:
        raise RefreshPlanError("fullCadenceDays must be positive")
    return source


def _accepted(previous: dict[str, Any] | None, source_key: str) -> dict[str, Any] | None:
    if previous is None:
        return None
    if previous.get("schemaVersion") != 1 or previous.get("sourceKey") != source_key:
        raise RefreshPlanError("previous refresh state identity mismatch")
    value = previous.get("acceptedComplete")
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("status") != "complete":
        raise RefreshPlanError("previous acceptedComplete is invalid")
    return value


def _full_due(
    source: dict[str, Any],
    previous: dict[str, Any] | None,
    accepted: dict[str, Any] | None,
    now: datetime,
) -> tuple[str, str]:
    if previous is not None:
        due = parse_time(previous.get("nextFullDueAt"), "previous nextFullDueAt", allow_none=True)
        success_at = parse_time(
            previous.get("lastSuccessfulFullAcquisitionAt"),
            "previous lastSuccessfulFullAcquisitionAt",
            allow_none=True,
        )
        if due is not None:
            if success_at is None and previous.get("lastSuccessfulFullSnapshotID") is None:
                return stamp(due), "no-successful-full-acquisition"
            return stamp(due), "full-cadence-due" if now >= due else "full-cadence-not-due"
    if accepted is None:
        return stamp(now), "no-successful-full-acquisition"
    retrieved = parse_time(accepted.get("retrievedAt"), "accepted retrievedAt")
    assert retrieved is not None
    due = retrieved + timedelta(days=source["fullCadenceDays"])
    return stamp(due), "full-cadence-due" if now >= due else "full-cadence-not-due"


def _conditional_headers(source: dict[str, Any], accepted: dict[str, Any] | None) -> dict[str, str]:
    if accepted is None:
        return {}
    declared = source.get("conditionalMetadata", [])
    if not isinstance(declared, list) or any(not isinstance(item, str) for item in declared):
        raise RefreshPlanError("conditionalMetadata must be a string array")
    upstream = accepted.get("upstream")
    if not isinstance(upstream, dict):
        return {}
    result: dict[str, str] = {}
    if "etag" in declared:
        value = upstream.get("etag")
        if isinstance(value, str) and value.strip():
            result["If-None-Match"] = value.strip()
    if "last-modified" in declared or "lastModified" in declared:
        value = upstream.get("lastModified")
        if isinstance(value, str) and value.strip():
            result["If-Modified-Since"] = value.strip()
    return result


def _delta_decision(
    source: dict[str, Any],
    accepted: dict[str, Any] | None,
    now: datetime,
) -> tuple[str, str | None, dict[str, Any] | None]:
    modes = source["supportedAcquisitionModes"]
    if "delta" not in modes:
        return "full", "delta-not-admitted", None
    if accepted is None:
        return "full", "delta-missing-accepted-predecessor", None
    cursor = accepted.get("cursor")
    if not isinstance(cursor, str) or not cursor.strip():
        return "full", "delta-missing-cursor", None
    expires = parse_time(accepted.get("cursorExpiresAt"), "cursorExpiresAt", allow_none=True)
    if expires is not None and expires <= now:
        return "full", "delta-cursor-expired", None
    predecessor = {
        "snapshotID": accepted.get("snapshotID"),
        "contentSha256": accepted.get("contentSha256"),
        "cursor": cursor,
        "cursorExpiresAt": accepted.get("cursorExpiresAt"),
    }
    return "delta", None, predecessor


def _targeted_plan(
    source: dict[str, Any],
    source_policy: dict[str, Any],
    refresh_queue: dict[str, Any] | None,
) -> dict[str, Any]:
    target = source.get("targetedQueue")
    if not isinstance(target, dict):
        raise RefreshPlanError("targetedQueue policy is missing")
    enabled = target.get("enabled") is True
    endpoint = target.get("endpointReference")
    host = urlparse(endpoint).hostname if isinstance(endpoint, str) else None
    acquisition_hosts = source_policy.get("allowedAcquisitionHosts", [])
    if not isinstance(acquisition_hosts, list) or any(not isinstance(item, str) for item in acquisition_hosts):
        raise RefreshPlanError("source policy allowedAcquisitionHosts is invalid")
    network_allowed = bool(enabled and host and host in set(acquisition_hosts))

    entries = (refresh_queue or {}).get("entries", [])
    if not isinstance(entries, list):
        raise RefreshPlanError("refresh queue entries must be an array")
    eligible_reasons = {
        "missing-current-ingredients",
        "date-unknown-ingredients",
        "stale-ingredients",
        "refresh-recommended-ingredients",
        "identity-or-formulation-conflict",
        "ambiguous-review",
        "assessment-recheck",
        "changed-unreviewed",
        "admitted-submission",
        "privacy-safe-demand",
    }
    gtins = sorted({
        item.get("gtin")
        for item in entries
        if isinstance(item, dict)
        and item.get("reason") in eligible_reasons
        and isinstance(item.get("gtin"), str)
        and GTIN.fullmatch(item["gtin"])
    })
    max_gtins = target.get("maxGtinsPerRun", 0)
    batch_size = target.get("batchSize", 0)
    request_limit = target.get("maxRequestsPerMinute", 0)
    interval = target.get("minimumRequestIntervalSeconds", 0)
    for label, value in (("maxGtinsPerRun", max_gtins), ("batchSize", batch_size), ("maxRequestsPerMinute", request_limit)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise RefreshPlanError(f"targetedQueue.{label} is invalid")
    if not isinstance(interval, (int, float)) or isinstance(interval, bool) or interval < 0:
        raise RefreshPlanError("targetedQueue.minimumRequestIntervalSeconds is invalid")
    if enabled:
        if min(max_gtins, batch_size, request_limit) < 1 or interval <= 0:
            raise RefreshPlanError("enabled targeted queue requires positive execution bounds")
        if 60.0 / float(interval) > request_limit + 1e-9:
            raise RefreshPlanError("targeted request interval exceeds declared rate limit")
        if batch_size > max_gtins:
            raise RefreshPlanError("targeted batch exceeds per-run target bound")
    bounded = gtins[:max_gtins] if enabled else []
    batches = [bounded[index:index + batch_size] for index in range(0, len(bounded), batch_size)] if batch_size else []
    return {
        "enabled": enabled,
        "endpointReference": endpoint,
        "endpointHost": host,
        "networkExecutionAllowed": network_allowed,
        "networkExecutionPerformed": False,
        "blockedReason": None if network_allowed else ("target-endpoint-not-admitted-for-acquisition" if enabled else "targeted-refresh-disabled"),
        "gtinCount": len(bounded),
        "batches": batches,
        "maxGtinsPerRun": max_gtins,
        "batchSize": batch_size,
        "maxRequestsPerMinute": request_limit,
        "minimumRequestIntervalSeconds": interval,
        "fields": target.get("fields", []),
    }


def build_plan(
    *,
    policy: dict[str, Any],
    source_policy: dict[str, Any],
    source_key: str,
    lane: str,
    evaluated_at: str,
    previous: dict[str, Any] | None = None,
    refresh_queue: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if source_policy.get("sourceKey") != source_key:
        raise RefreshPlanError("source policy identity mismatch")
    if lane not in {"full", "auto", "targeted"}:
        raise RefreshPlanError("lane must be full, auto, or targeted")
    source = _source_config(policy, source_key)
    now = parse_time(evaluated_at, "evaluatedAt")
    assert now is not None
    accepted = _accepted(previous, source_key)
    due_at, due_reason = _full_due(source, previous, accepted, now)

    requested_mode = "full"
    fallback_reason = None
    delta_predecessor = None
    if lane == "auto":
        requested_mode, fallback_reason, delta_predecessor = _delta_decision(source, accepted, now)
    elif lane == "targeted":
        requested_mode = "targeted"

    plan: dict[str, Any] = {
        "schemaVersion": 1,
        "policyVersion": policy["policyVersion"],
        "sourceKey": source_key,
        "market": policy["market"],
        "lane": lane,
        "evaluatedAt": evaluated_at,
        "requestedMode": requested_mode,
        "fallbackReason": fallback_reason,
        "fullDueAt": due_at,
        "fullDueReason": due_reason,
        "conditionalRequestHeaders": _conditional_headers(source, accepted) if requested_mode == "full" else {},
        "deltaPredecessor": delta_predecessor,
        "targetedExecution": _targeted_plan(source, source_policy, refresh_queue) if lane == "targeted" else None,
        "acceptedSnapshotID": accepted.get("snapshotID") if accepted else None,
        "acceptedContentSha256": accepted.get("contentSha256") if accepted else None,
    }
    plan["planSha256"] = digest_without(plan, "planSha256")
    return plan


def validate_plan(plan: dict[str, Any]) -> None:
    required = {
        "schemaVersion", "policyVersion", "sourceKey", "market", "lane", "evaluatedAt",
        "requestedMode", "fallbackReason", "fullDueAt", "fullDueReason",
        "conditionalRequestHeaders", "deltaPredecessor", "targetedExecution",
        "acceptedSnapshotID", "acceptedContentSha256", "planSha256",
    }
    if set(plan) != required or plan.get("schemaVersion") != 1 or plan.get("market") != "DE":
        raise RefreshPlanError("refresh plan fields or schema are invalid")
    parse_time(plan.get("evaluatedAt"), "evaluatedAt")
    parse_time(plan.get("fullDueAt"), "fullDueAt")
    if plan.get("planSha256") != digest_without(plan, "planSha256"):
        raise RefreshPlanError("refresh plan digest mismatch")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="build a deterministic no-network acquisition plan")
    plan.add_argument("--policy", type=Path, required=True)
    plan.add_argument("--source-policy", type=Path, required=True)
    plan.add_argument("--source-key", required=True)
    plan.add_argument("--lane", choices=("full", "auto", "targeted"), required=True)
    plan.add_argument("--evaluated-at", required=True)
    plan.add_argument("--previous-state", type=Path)
    plan.add_argument("--refresh-queue", type=Path)
    plan.add_argument("--output", type=Path, required=True)
    validate = sub.add_parser("validate", help="validate a deterministic refresh plan")
    validate.add_argument("--input", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.command == "validate":
            value = load_json(args.input)
            assert value is not None
            validate_plan(value)
            print(f"Validated refresh plan {value['planSha256']}")
            return
        policy = load_json(args.policy)
        source_policy = load_json(args.source_policy)
        assert policy is not None and source_policy is not None
        value = build_plan(
            policy=policy,
            source_policy=source_policy,
            source_key=args.source_key,
            lane=args.lane,
            evaluated_at=args.evaluated_at,
            previous=load_json(args.previous_state),
            refresh_queue=load_json(args.refresh_queue),
        )
        validate_plan(value)
        write_json(args.output, value)
        print(f"Refresh plan: source={value['sourceKey']} lane={value['lane']} mode={value['requestedMode']}")
    except RefreshPlanError as exc:
        raise SystemExit(f"catalog refresh planning failed: {exc}") from exc


if __name__ == "__main__":
    main()
