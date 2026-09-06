"""Fail-closed operator recovery projection for scheduled catalog refreshes."""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any

RECOVERY_WORKFLOW = "scheduled-catalog-refresh.yml"
RECOVERY_SCHEDULES = {
    "open-food-facts": "17 3 * * 3",
    "open-prices": "41 3 * * 5",
}
WEEKLY_CRON = re.compile(r"^(?P<minute>[0-9]{1,2}) (?P<hour>[0-9]{1,2}) \* \* (?P<weekday>[0-6])$")


def _next_weekly_occurrence(cron: str, evaluated_at: str, error: type[ValueError]) -> str:
    match = WEEKLY_CRON.fullmatch(cron)
    if not match:
        raise error(f"unsupported recovery cron {cron!r}")
    minute = int(match.group("minute"))
    hour = int(match.group("hour"))
    cron_weekday = int(match.group("weekday"))
    if minute > 59 or hour > 23:
        raise error(f"invalid recovery cron {cron!r}")
    try:
        instant = datetime.fromisoformat(evaluated_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise error("evaluatedAt is invalid for recovery scheduling") from exc
    if instant.tzinfo is None:
        raise error("evaluatedAt must be timezone-aware for recovery scheduling")
    instant = instant.astimezone(timezone.utc)
    python_weekday = (cron_weekday - 1) % 7
    days_ahead = (python_weekday - instant.weekday()) % 7
    candidate = (instant + timedelta(days=days_ahead)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate <= instant:
        candidate += timedelta(days=7)
    return candidate.isoformat(timespec="seconds").replace("+00:00", "Z")


def _scheduled_crons(workflow_text: str, error: type[ValueError]) -> set[str]:
    values = set(re.findall(r'(?m)^\s*- cron:\s*["\']([^"\']+)["\']\s*$', workflow_text))
    if not values:
        raise error("scheduled refresh workflow exposes no cron entries")
    return values


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
    if "workflow_dispatch:" not in scheduled_workflow_text:
        raise error("scheduled refresh workflow lacks workflow_dispatch recovery")
    for required_input in ("source_key:", "snapshot_id:", "mode:"):
        if required_input not in scheduled_workflow_text:
            raise error(f"scheduled refresh workflow lacks required recovery input {required_input[:-1]}")

    if _scheduled_crons(scheduled_workflow_text, error) != set(RECOVERY_SCHEDULES.values()):
        raise error("scheduled refresh workflow cron set differs from reviewed recovery contract")
    if re.search(
        rf'(?s)"\$EVENT_SCHEDULE"\s*==\s*"{re.escape(RECOVERY_SCHEDULES["open-prices"])}"'
        rf'.{{0,240}}?SOURCE_KEY=open-prices',
        scheduled_workflow_text,
    ) is None or re.search(
        r'(?s)elif\s+\[\[\s*"\$EVENT_NAME"\s*==\s*"schedule"\s*\]\];\s*then'
        r'.{0,160}?SOURCE_KEY=open-food-facts',
        scheduled_workflow_text,
    ) is None:
        raise error("scheduled refresh workflow source-to-cron mapping differs from reviewed recovery contract")
    if '-n "$INPUT_CATALOG_VERSION"' not in scheduled_workflow_text:
        raise error("scheduled refresh workflow no longer gates proposal mode on non-empty catalog_version")

    projected: dict[str, Any] = {}
    for source_key, cron in sorted(RECOVERY_SCHEDULES.items()):
        source = sources.get(source_key)
        if not isinstance(source, dict):
            raise error(f"refresh policy lacks recovery source {source_key}")
        modes = source.get("supportedAcquisitionModes")
        if not isinstance(modes, list) or "full" not in modes:
            raise error(f"refresh policy does not admit full recovery for {source_key}")
        cadence_days = source.get("fullCadenceDays")
        if cadence_days != 7:
            raise error(f"recovery schedule requires weekly full cadence for {source_key}")
        projected[source_key] = {
            "sourceKey": source_key,
            "mode": "full",
            "snapshotIDRequired": True,
            "catalogVersionMustBeEmpty": True,
            "scheduleCronUTC": cron,
            "fullCadenceDays": cadence_days,
            "nextScheduledAt": _next_weekly_occurrence(cron, evaluated_at, error),
        }
    return {"workflow": RECOVERY_WORKFLOW, "workflowDispatch": True, "sources": projected}


def validate_operator_recovery(recovery: Any, error: type[ValueError]) -> None:
    if not isinstance(recovery, dict):
        raise error("refresh operatorRecovery must be an object")
    if recovery.get("workflow") != RECOVERY_WORKFLOW or recovery.get("workflowDispatch") is not True:
        raise error("refresh operatorRecovery workflow contract is invalid")
    sources = recovery.get("sources")
    if not isinstance(sources, dict) or set(sources) != set(RECOVERY_SCHEDULES):
        raise error("refresh operatorRecovery sources are invalid")
    for source_key, cron in RECOVERY_SCHEDULES.items():
        value = sources.get(source_key)
        if not isinstance(value, dict):
            raise error(f"refresh operatorRecovery source {source_key} is invalid")
        if value.get("sourceKey") != source_key or value.get("mode") != "full":
            raise error(f"refresh operatorRecovery source {source_key} mode is invalid")
        if value.get("snapshotIDRequired") is not True or value.get("catalogVersionMustBeEmpty") is not True:
            raise error(f"refresh operatorRecovery source {source_key} input contract is invalid")
        if value.get("scheduleCronUTC") != cron or value.get("fullCadenceDays") != 7:
            raise error(f"refresh operatorRecovery source {source_key} cadence is invalid")
        next_at = value.get("nextScheduledAt")
        if not isinstance(next_at, str):
            raise error(f"refresh operatorRecovery source {source_key} next schedule is invalid")
        try:
            parsed = datetime.fromisoformat(next_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise error(f"refresh operatorRecovery source {source_key} next schedule is invalid") from exc
        if parsed.tzinfo is None:
            raise error(f"refresh operatorRecovery source {source_key} next schedule lacks timezone")
