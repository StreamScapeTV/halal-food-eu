"""Fail-closed operator recovery projection for scheduled catalog refreshes."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

RECOVERY_WORKFLOW = "scheduled-catalog-refresh.yml"
TRUSTED_RECOVERY_REF = "main"
EXPECTED_RECOVERY_SOURCES = frozenset({"open-food-facts", "open-prices"})
WEEKLY_CRON = re.compile(r"^(?P<minute>[0-9]{1,2}) (?P<hour>[0-9]{1,2}) \* \* (?P<weekday>[0-6])$")
EXPLICIT_SCHEDULE_BRANCH = re.compile(
    r'^\s*(?:if|elif)\s+\[\[\s*"\$EVENT_NAME"\s*==\s*"schedule"\s*&&\s*'
    r'"\$EVENT_SCHEDULE"\s*==\s*"([^"]+)"\s*\]\];\s*then\s*$'
)
FALLBACK_SCHEDULE_BRANCH = re.compile(
    r'^\s*(?:if|elif)\s+\[\[\s*"\$EVENT_NAME"\s*==\s*"schedule"\s*\]\];\s*then\s*$'
)
SOURCE_ASSIGNMENT = re.compile(r"^\s*SOURCE_KEY=([a-z0-9][a-z0-9-]*)\s*$")
BRANCH_BOUNDARY = re.compile(r"^\s*(?:elif|else|fi)\b")


def _parse_evaluated_at(evaluated_at: str, error: type[ValueError]) -> datetime:
    try:
        instant = datetime.fromisoformat(evaluated_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise error("evaluatedAt is invalid for recovery scheduling") from exc
    if instant.tzinfo is None:
        raise error("evaluatedAt must be timezone-aware for recovery scheduling")
    return instant.astimezone(timezone.utc)


def _next_weekly_occurrence(cron: str, evaluated_at: str, error: type[ValueError]) -> str:
    match = WEEKLY_CRON.fullmatch(cron)
    if not match:
        raise error(f"unsupported recovery cron {cron!r}")
    minute = int(match.group("minute"))
    hour = int(match.group("hour"))
    cron_weekday = int(match.group("weekday"))
    if minute > 59 or hour > 23:
        raise error(f"invalid recovery cron {cron!r}")
    instant = _parse_evaluated_at(evaluated_at, error)
    python_weekday = (cron_weekday - 1) % 7
    days_ahead = (python_weekday - instant.weekday()) % 7
    candidate = (instant + timedelta(days=days_ahead)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate <= instant:
        candidate += timedelta(days=7)
    return candidate.isoformat(timespec="seconds").replace("+00:00", "Z")


def _scheduled_crons(workflow_text: str, error: type[ValueError]) -> list[str]:
    values = re.findall(r'(?m)^\s*- cron:\s*["\']([^"\']+)["\']\s*$', workflow_text)
    if not values:
        raise error("scheduled refresh workflow exposes no cron entries")
    if len(values) != len(set(values)):
        raise error("scheduled refresh workflow contains duplicate cron entries")
    for value in values:
        if WEEKLY_CRON.fullmatch(value) is None:
            raise error(f"unsupported recovery cron {value!r}")
    return values


def _trusted_ref(workflow_text: str, error: type[ValueError]) -> str:
    matches = set(re.findall(r"(?m)^\s*EXPECTED_REF:\s*refs/heads/([^\s#]+)\s*$", workflow_text))
    if matches != {TRUSTED_RECOVERY_REF}:
        raise error("scheduled refresh workflow must require protected main as its trusted ref")
    return TRUSTED_RECOVERY_REF


def _source_schedule_mapping(workflow_text: str, error: type[ValueError]) -> dict[str, str]:
    crons = _scheduled_crons(workflow_text, error)
    explicit: dict[str, str] = {}
    fallback_source: str | None = None
    active: tuple[str, str | None] | None = None

    for line in workflow_text.splitlines():
        explicit_match = EXPLICIT_SCHEDULE_BRANCH.fullmatch(line)
        if explicit_match:
            active = ("explicit", explicit_match.group(1))
            continue
        if FALLBACK_SCHEDULE_BRANCH.fullmatch(line):
            active = ("fallback", None)
            continue
        if active is not None and BRANCH_BOUNDARY.match(line):
            active = None
            continue
        if active is None:
            continue
        source_match = SOURCE_ASSIGNMENT.fullmatch(line)
        if source_match is None:
            continue
        source_key = source_match.group(1)
        kind, cron = active
        if kind == "explicit":
            assert cron is not None
            if cron in explicit and explicit[cron] != source_key:
                raise error(f"scheduled refresh workflow maps cron {cron!r} to multiple sources")
            explicit[cron] = source_key
        else:
            if fallback_source is not None and fallback_source != source_key:
                raise error("scheduled refresh workflow fallback maps to multiple sources")
            fallback_source = source_key
        active = None

    unknown_explicit = sorted(set(explicit) - set(crons))
    if unknown_explicit:
        raise error(f"scheduled refresh workflow resolves undeclared cron {unknown_explicit[0]!r}")
    unresolved = [cron for cron in crons if cron not in explicit]
    if unresolved and fallback_source is None:
        raise error("scheduled refresh workflow leaves a cron without a source mapping")

    source_to_cron: dict[str, str] = {}
    for cron in crons:
        source_key = explicit.get(cron, fallback_source)
        if source_key is None:
            raise error(f"scheduled refresh workflow leaves cron {cron!r} without a source mapping")
        if source_key in source_to_cron:
            raise error(f"scheduled refresh workflow maps multiple cron entries to source {source_key}")
        source_to_cron[source_key] = cron
    return source_to_cron


def build_operator_recovery(
    *,
    evaluated_at: str,
    refresh_policy: dict[str, Any],
    scheduled_workflow_text: str,
    error: type[ValueError],
) -> dict[str, Any]:
    if refresh_policy.get("schemaVersion") != 1:
        raise error("refresh policy schemaVersion must be 1 for recovery scheduling")
    sources = refresh_policy.get("sources")
    if not isinstance(sources, dict):
        raise error("refresh policy sources must be an object")
    if re.search(r"(?m)^\s{2}workflow_dispatch:\s*$", scheduled_workflow_text) is None:
        raise error("scheduled refresh workflow lacks workflow_dispatch recovery")
    for required_input in ("source_key", "snapshot_id", "mode"):
        if re.search(rf"(?m)^\s{{6}}{re.escape(required_input)}:\s*$", scheduled_workflow_text) is None:
            raise error(f"scheduled refresh workflow lacks required recovery input {required_input}")
    if '-n "$INPUT_CATALOG_VERSION"' not in scheduled_workflow_text:
        raise error("scheduled refresh workflow no longer gates proposal mode on non-empty catalog_version")

    trusted_ref = _trusted_ref(scheduled_workflow_text, error)
    schedules = _source_schedule_mapping(scheduled_workflow_text, error)
    if set(schedules) != EXPECTED_RECOVERY_SOURCES:
        raise error("scheduled refresh workflow source mapping differs from accepted recovery sources")

    projected: dict[str, Any] = {}
    for source_key in sorted(EXPECTED_RECOVERY_SOURCES):
        source = sources.get(source_key)
        if not isinstance(source, dict):
            raise error(f"refresh policy lacks recovery source {source_key}")
        modes = source.get("supportedAcquisitionModes")
        if not isinstance(modes, list) or "full" not in modes:
            raise error(f"refresh policy does not admit full recovery for {source_key}")
        cadence_days = source.get("fullCadenceDays")
        if not isinstance(cadence_days, int) or isinstance(cadence_days, bool) or cadence_days <= 0:
            raise error(f"refresh policy full cadence is invalid for {source_key}")
        cron = schedules[source_key]
        if cadence_days != 7:
            raise error(f"weekly recovery schedule conflicts with policy cadence for {source_key}")
        projected[source_key] = {
            "sourceKey": source_key,
            "mode": "full",
            "snapshotIDRequired": True,
            "catalogVersionMustBeEmpty": True,
            "scheduleCronUTC": cron,
            "fullCadenceDays": cadence_days,
            "nextScheduledAt": _next_weekly_occurrence(cron, evaluated_at, error),
        }
    return {
        "workflow": RECOVERY_WORKFLOW,
        "ref": trusted_ref,
        "workflowDispatch": True,
        "sources": projected,
    }


def validate_operator_recovery(recovery: Any, error: type[ValueError]) -> None:
    if not isinstance(recovery, dict):
        raise error("refresh operatorRecovery must be an object")
    if recovery.get("workflow") != RECOVERY_WORKFLOW or recovery.get("workflowDispatch") is not True:
        raise error("refresh operatorRecovery workflow contract is invalid")
    if recovery.get("ref") != TRUSTED_RECOVERY_REF:
        raise error("refresh operatorRecovery trusted ref must be protected main")
    sources = recovery.get("sources")
    if not isinstance(sources, dict) or set(sources) != EXPECTED_RECOVERY_SOURCES:
        raise error("refresh operatorRecovery sources are invalid")
    seen_crons: set[str] = set()
    for source_key in sorted(EXPECTED_RECOVERY_SOURCES):
        value = sources.get(source_key)
        if not isinstance(value, dict):
            raise error(f"refresh operatorRecovery source {source_key} is invalid")
        if value.get("sourceKey") != source_key or value.get("mode") != "full":
            raise error(f"refresh operatorRecovery source {source_key} mode is invalid")
        if value.get("snapshotIDRequired") is not True or value.get("catalogVersionMustBeEmpty") is not True:
            raise error(f"refresh operatorRecovery source {source_key} input contract is invalid")
        cron = value.get("scheduleCronUTC")
        if not isinstance(cron, str) or WEEKLY_CRON.fullmatch(cron) is None or cron in seen_crons:
            raise error(f"refresh operatorRecovery source {source_key} cadence is invalid")
        seen_crons.add(cron)
        cadence_days = value.get("fullCadenceDays")
        if cadence_days != 7:
            raise error(f"refresh operatorRecovery source {source_key} policy cadence is invalid")
        next_at = value.get("nextScheduledAt")
        if not isinstance(next_at, str):
            raise error(f"refresh operatorRecovery source {source_key} next schedule is invalid")
        try:
            parsed = datetime.fromisoformat(next_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise error(f"refresh operatorRecovery source {source_key} next schedule is invalid") from exc
        if parsed.tzinfo is None:
            raise error(f"refresh operatorRecovery source {source_key} next schedule lacks timezone")
