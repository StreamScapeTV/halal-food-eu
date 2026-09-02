#!/usr/bin/env python3
"""Enrich catalog-health v1 with deterministic source-refresh health.

This layer does not decide halal status and does not mutate evidence. It projects
refresh due state, privacy-safe queue aggregates, and trusted workflow status into
the existing catalog-health incident surface.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Any

import catalog_health


class RefreshHealthError(ValueError):
    pass


def load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshHealthError(f"failed to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RefreshHealthError(f"{path} must contain a JSON object")
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


def _queue_projection(queue: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    entries = queue.get("entries")
    if not isinstance(entries, list):
        raise RefreshHealthError("refresh queue entries must be an array")
    reasons = Counter()
    priorities = Counter()
    for item in entries:
        if not isinstance(item, dict):
            raise RefreshHealthError("refresh queue entry must be an object")
        reason = item.get("reason")
        priority = item.get("priority")
        if not isinstance(reason, str) or not reason:
            raise RefreshHealthError("refresh queue reason is invalid")
        if not isinstance(priority, str) or not priority:
            raise RefreshHealthError("refresh queue priority is invalid")
        reasons[reason] += 1
        priorities[priority] += 1

    incident_reasons = {
        "stale-ingredients",
        "changed-unreviewed",
        "certification-expiry",
        "certification-invalidated",
        "source-or-quality-blocker",
    }
    blockers = sorted(reason for reason in reasons if reason in incident_reasons)
    return (
        {
            "entryCount": len(entries),
            "reasonCounts": dict(sorted(reasons.items())),
            "priorityCounts": dict(sorted(priorities.items())),
            "queueSha256": queue.get("queueSha256"),
        },
        blockers,
    )


def _workflow_projection(status: dict[str, Any] | None) -> tuple[dict[str, Any], str | None]:
    if status is None or status.get("available") is False:
        return {"available": False, "conclusion": None, "runId": None, "event": None, "updatedAt": None}, None
    available = status.get("available")
    if available not in {None, True}:
        raise RefreshHealthError("workflow status available flag is invalid")
    conclusion = status.get("conclusion")
    if conclusion is not None and not isinstance(conclusion, str):
        raise RefreshHealthError("workflow status conclusion is invalid")
    run_id = status.get("runId")
    if run_id is not None and not isinstance(run_id, (str, int)):
        raise RefreshHealthError("workflow status runId is invalid")
    projection = {
        "available": True,
        "conclusion": conclusion,
        "runId": str(run_id) if run_id is not None else None,
        "event": status.get("event") if isinstance(status.get("event"), str) else None,
        "updatedAt": status.get("updatedAt") if isinstance(status.get("updatedAt"), str) else None,
    }
    unhealthy = conclusion in {"failure", "cancelled", "timed_out", "action_required", "startup_failure"}
    return projection, conclusion if unhealthy else None


def enrich_health(
    *,
    base_health: dict[str, Any],
    refresh_queue: dict[str, Any],
    refresh_plan: dict[str, Any],
    refresh_report: dict[str, Any] | None = None,
    workflow_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    catalog_health.validate_health_report(base_health)
    if refresh_plan.get("schemaVersion") != 1:
        raise RefreshHealthError("refresh plan schemaVersion must be 1")
    source_key = refresh_plan.get("sourceKey")
    if not isinstance(source_key, str) or not source_key:
        raise RefreshHealthError("refresh plan sourceKey is invalid")

    queue_projection, queue_blockers = _queue_projection(refresh_queue)
    workflow_projection, workflow_blocker = _workflow_projection(workflow_status)
    targeted = refresh_plan.get("targetedExecution")
    if targeted is not None and not isinstance(targeted, dict):
        raise RefreshHealthError("targetedExecution must be object or null")

    blocker_keys: set[str] = {
        f"refresh:{source_key}:queue:{reason}" for reason in queue_blockers
    }
    due_reason = refresh_plan.get("fullDueReason")
    if due_reason == "full-cadence-due":
        blocker_keys.add(f"refresh:{source_key}:full-overdue")
    elif due_reason == "no-successful-full-acquisition":
        blocker_keys.add(f"refresh:{source_key}:no-successful-full-acquisition")
    if workflow_blocker:
        blocker_keys.add(f"refresh:scheduled-catalog-refresh:{workflow_blocker}")

    attempt = {
        "available": refresh_report is not None,
        "attemptStatus": None,
        "qualityStatus": None,
        "snapshotID": None,
        "candidateChangedFromAccepted": None,
    }
    last_success_at = None
    last_success_snapshot = None
    if refresh_report is not None:
        if refresh_report.get("schemaVersion") != 1:
            raise RefreshHealthError("refresh report schemaVersion must be 1")
        if refresh_report.get("sourceKey") != source_key:
            raise RefreshHealthError("refresh report source differs from plan")
        last_success_at = refresh_report.get("lastSuccessfulFullAcquisitionAt")
        last_success_snapshot = refresh_report.get("lastSuccessfulFullSnapshotID")
        if (last_success_at is None) != (last_success_snapshot is None):
            raise RefreshHealthError("refresh report successful-full acquisition clock is inconsistent")
        if last_success_at is not None and not isinstance(last_success_at, str):
            raise RefreshHealthError("refresh report successful-full acquisition time is invalid")
        if last_success_snapshot is not None and (not isinstance(last_success_snapshot, str) or not last_success_snapshot):
            raise RefreshHealthError("refresh report successful-full snapshot ID is invalid")
        attempt.update(
            attemptStatus=refresh_report.get("attemptStatus"),
            qualityStatus=refresh_report.get("qualityStatus"),
            snapshotID=refresh_report.get("snapshotID"),
            candidateChangedFromAccepted=refresh_report.get("candidateChangedFromAccepted"),
        )
        if refresh_report.get("attemptStatus") != "complete" or refresh_report.get("qualityStatus") != "pass":
            blocker_keys.add(
                f"refresh:{source_key}:attempt:{refresh_report.get('attemptStatus', 'unknown')}:quality:{refresh_report.get('qualityStatus', 'unknown')}"
            )

    refresh = {
        "available": True,
        "sourceKey": source_key,
        "acceptedSnapshotID": refresh_plan.get("acceptedSnapshotID"),
        "acceptedContentSha256": refresh_plan.get("acceptedContentSha256"),
        "lastSuccessfulFullAcquisitionAt": last_success_at,
        "lastSuccessfulFullSnapshotID": last_success_snapshot,
        "fullDueAt": refresh_plan.get("fullDueAt"),
        "fullDueReason": due_reason,
        "requestedMode": refresh_plan.get("requestedMode"),
        "fallbackReason": refresh_plan.get("fallbackReason"),
        "queue": queue_projection,
        "targeted": {
            "available": targeted is not None,
            "enabled": targeted.get("enabled") if targeted else False,
            "gtinCount": targeted.get("gtinCount") if targeted else 0,
            "networkExecutionAllowed": targeted.get("networkExecutionAllowed") if targeted else False,
            "networkExecutionPerformed": targeted.get("networkExecutionPerformed") if targeted else False,
            "blockedReason": targeted.get("blockedReason") if targeted else None,
        },
        "latestAttempt": attempt,
        "scheduledWorkflow": workflow_projection,
        "deduplicationKeys": sorted(blocker_keys),
    }

    report = json.loads(json.dumps(base_health))
    report["schemaVersion"] = 2
    report["refresh"] = refresh
    gate = report.get("qualityGate")
    if not isinstance(gate, dict):
        raise RefreshHealthError("base health lacks qualityGate")
    existing = gate.get("deduplicationKeys", [])
    if not isinstance(existing, list) or any(not isinstance(item, str) for item in existing):
        raise RefreshHealthError("base health qualityGate deduplicationKeys are invalid")
    gate["deduplicationKeys"] = sorted(set(existing) | blocker_keys)
    incident = gate.get("incident")
    if blocker_keys and (not isinstance(incident, dict) or incident.get("action") in {None, "none"}):
        gate["incident"] = {"action": "investigate-refresh", "deduplicationKeys": sorted(blocker_keys)}
    report["reportSha256"] = digest_without(report, "reportSha256")
    validate_refresh_health(report)
    return report


def validate_refresh_health(report: dict[str, Any]) -> None:
    if report.get("schemaVersion") != 2:
        raise RefreshHealthError("refresh-enriched health schemaVersion must be 2")
    refresh = report.get("refresh")
    if not isinstance(refresh, dict) or refresh.get("available") is not True:
        raise RefreshHealthError("refresh-enriched health lacks refresh projection")
    if not isinstance(refresh.get("deduplicationKeys"), list):
        raise RefreshHealthError("refresh deduplicationKeys must be an array")
    success_at = refresh.get("lastSuccessfulFullAcquisitionAt")
    success_snapshot = refresh.get("lastSuccessfulFullSnapshotID")
    if (success_at is None) != (success_snapshot is None):
        raise RefreshHealthError("refresh successful-full acquisition clock is inconsistent")
    gate = report.get("qualityGate")
    if not isinstance(gate, dict):
        raise RefreshHealthError("refresh-enriched health lacks qualityGate")
    gate_keys = gate.get("deduplicationKeys")
    if not isinstance(gate_keys, list):
        raise RefreshHealthError("qualityGate deduplicationKeys must be an array")
    if not set(refresh["deduplicationKeys"]).issubset(set(gate_keys)):
        raise RefreshHealthError("refresh blockers are not exposed through catalog-health incidents")
    if report.get("reportSha256") != digest_without(report, "reportSha256"):
        raise RefreshHealthError("refresh-enriched health digest mismatch")


def human_summary(report: dict[str, Any]) -> str:
    refresh = report["refresh"]
    lines = [
        "# Catalog health",
        "",
        f"- Evaluated commit: `{report['commitSha']}`",
        f"- Evaluated at: `{report['evaluatedAt']}`",
        f"- Current products: {report['products']['uniqueCurrentSelections']}",
        f"- Current exact ingredient coverage: {report['products']['withCurrentIngredients']} / {report['products']['uniqueCurrentSelections']}",
        f"- Quality gate: `{report['qualityGate']['status']}`",
        "",
        "## Refresh health",
        f"- Source: `{refresh['sourceKey']}`",
        f"- Accepted snapshot: `{refresh['acceptedSnapshotID']}`",
        f"- Last successful full acquisition: `{refresh['lastSuccessfulFullAcquisitionAt']}` (`{refresh['lastSuccessfulFullSnapshotID']}`)",
        f"- Full refresh due: `{refresh['fullDueAt']}` (`{refresh['fullDueReason']}`)",
        f"- Queue entries: `{refresh['queue']['entryCount']}`; reasons: `{json.dumps(refresh['queue']['reasonCounts'], sort_keys=True)}`",
        f"- Targeted network allowed: `{str(refresh['targeted']['networkExecutionAllowed']).lower()}`; performed: `{str(refresh['targeted']['networkExecutionPerformed']).lower()}`",
        f"- Scheduled refresh conclusion: `{refresh['scheduledWorkflow']['conclusion']}`",
        f"- Refresh incident keys: `{json.dumps(refresh['deduplicationKeys'])}`",
        "",
        "Refresh dates are independent of formulation, retailer, certification, and assessment evidence dates. A source check never freshens older evidence by itself.",
        "",
        f"- Report SHA-256: `{report['reportSha256']}`",
        "",
    ]
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    enrich = sub.add_parser("enrich", help="enrich one validated catalog-health v1 report")
    enrich.add_argument("--base-health", type=Path, required=True)
    enrich.add_argument("--refresh-queue", type=Path, required=True)
    enrich.add_argument("--refresh-plan", type=Path, required=True)
    enrich.add_argument("--refresh-report", type=Path)
    enrich.add_argument("--workflow-status", type=Path)
    enrich.add_argument("--output", type=Path, required=True)
    enrich.add_argument("--markdown-output", type=Path, required=True)
    validate = sub.add_parser("validate", help="validate refresh-enriched catalog health")
    validate.add_argument("--input", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.command == "validate":
            report = load_json(args.input)
            assert report is not None
            validate_refresh_health(report)
            print(f"Validated refresh catalog health {report['reportSha256']}")
            return
        base = load_json(args.base_health)
        queue = load_json(args.refresh_queue)
        plan = load_json(args.refresh_plan)
        assert base is not None and queue is not None and plan is not None
        report = enrich_health(
            base_health=base,
            refresh_queue=queue,
            refresh_plan=plan,
            refresh_report=load_json(args.refresh_report),
            workflow_status=load_json(args.workflow_status),
        )
        write_json(args.output, report)
        args.markdown_output.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_output.write_text(human_summary(report), encoding="utf-8")
        print(
            f"Refresh health: source={report['refresh']['sourceKey']} "
            f"queue={report['refresh']['queue']['entryCount']} "
            f"blockers={len(report['refresh']['deduplicationKeys'])}"
        )
    except (RefreshHealthError, catalog_health.CatalogHealthError) as exc:
        raise SystemExit(f"catalog refresh health failed: {exc}") from exc


if __name__ == "__main__":
    main()
