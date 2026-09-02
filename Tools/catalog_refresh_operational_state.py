#!/usr/bin/env python3
"""Separate operational full-acquisition cadence from accepted evidence lineage.

A successful complete full acquisition advances the next *acquisition* due time even
when its content is unchanged or still waiting for catalog review. It never rewrites
accepted evidence timestamps. Failed, partial, or quality-blocked attempts do not move
this operational clock.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class OperationalRefreshError(ValueError):
    pass


def load_json(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OperationalRefreshError(f"failed to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise OperationalRefreshError(f"{path} must contain a JSON object")
    return value


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_without(value: dict[str, Any], field: str) -> str:
    work = dict(value)
    work.pop(field, None)
    return hashlib.sha256(canonical(work)).hexdigest()


def parse_time(value: Any, label: str, *, allow_none: bool = False) -> datetime | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise OperationalRefreshError(f"{label} must be RFC3339")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise OperationalRefreshError(f"{label} must be RFC3339") from exc
    if dt.tzinfo is None:
        raise OperationalRefreshError(f"{label} must be timezone-aware")
    return dt.astimezone(timezone.utc)


def stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_identity(state: dict[str, Any], report: dict[str, Any], policy: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    if state.get("schemaVersion") != 1 or report.get("schemaVersion") != 1:
        raise OperationalRefreshError("refresh state/report schemaVersion must be 1")
    source_key = state.get("sourceKey")
    if not isinstance(source_key, str) or source_key not in policy.get("sources", {}):
        raise OperationalRefreshError("refresh source is not admitted by policy")
    if report.get("sourceKey") != source_key:
        raise OperationalRefreshError("refresh state/report source mismatch")
    if state.get("market") != policy.get("market"):
        raise OperationalRefreshError("refresh state market mismatch")
    source = policy["sources"][source_key]
    if not isinstance(source, dict):
        raise OperationalRefreshError("refresh source policy is invalid")
    cadence = source.get("fullCadenceDays")
    if not isinstance(cadence, int) or isinstance(cadence, bool) or cadence < 1:
        raise OperationalRefreshError("fullCadenceDays must be positive")
    return source_key, source


def apply_operational_clock(
    *,
    state: dict[str, Any],
    report: dict[str, Any],
    policy: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    source_key, source = _validate_identity(state, report, policy)
    result = json.loads(json.dumps(state))
    report_result = json.loads(json.dumps(report))
    attempt = result.get("lastAttempt")
    if not isinstance(attempt, dict):
        raise OperationalRefreshError("refresh state lastAttempt is required")
    if attempt.get("snapshotID") != report_result.get("snapshotID"):
        raise OperationalRefreshError("refresh attempt/report snapshot mismatch")

    previous_success_at = None
    previous_success_snapshot = None
    previous_due = None
    if previous is not None:
        if previous.get("schemaVersion") != 1 or previous.get("sourceKey") != source_key or previous.get("market") != result.get("market"):
            raise OperationalRefreshError("previous operational refresh state identity mismatch")
        previous_success_at = previous.get("lastSuccessfulFullAcquisitionAt")
        previous_success_snapshot = previous.get("lastSuccessfulFullSnapshotID")
        if previous_success_at is not None:
            parse_time(previous_success_at, "previous lastSuccessfulFullAcquisitionAt")
        if previous_success_snapshot is not None and (not isinstance(previous_success_snapshot, str) or not previous_success_snapshot):
            raise OperationalRefreshError("previous lastSuccessfulFullSnapshotID is invalid")
        previous_due = previous.get("nextFullDueAt")
        if previous_due is not None:
            parse_time(previous_due, "previous nextFullDueAt")

    successful_full = (
        attempt.get("status") == "complete"
        and attempt.get("mode") == "full"
        and attempt.get("qualityStatus") == "pass"
    )
    if successful_full:
        retrieved = parse_time(attempt.get("retrievedAt"), "successful full retrievedAt")
        assert retrieved is not None
        success_at = stamp(retrieved)
        success_snapshot = attempt.get("snapshotID")
        if not isinstance(success_snapshot, str) or not success_snapshot:
            raise OperationalRefreshError("successful full snapshotID is invalid")
        due = stamp(retrieved + timedelta(days=source["fullCadenceDays"]))
    else:
        success_at = previous_success_at
        success_snapshot = previous_success_snapshot
        due = previous_due or result.get("nextFullDueAt")
        parse_time(due, "nextFullDueAt")

    # These are operational scheduling facts only. acceptedComplete remains byte-for-byte
    # unchanged here; evidence observations retain their own observed/retrieved clocks.
    result["lastSuccessfulFullAcquisitionAt"] = success_at
    result["lastSuccessfulFullSnapshotID"] = success_snapshot
    result["nextFullDueAt"] = due
    result["stateSha256"] = digest_without(result, "stateSha256")

    report_result["lastSuccessfulFullAcquisitionAt"] = success_at
    report_result["lastSuccessfulFullSnapshotID"] = success_snapshot
    report_result["nextFullDueAt"] = due
    report_result["reportSha256"] = digest_without(report_result, "reportSha256")
    return result, report_result


def validate_operational_state(state: dict[str, Any]) -> None:
    for field in ("lastSuccessfulFullAcquisitionAt", "lastSuccessfulFullSnapshotID"):
        if field not in state:
            raise OperationalRefreshError(f"refresh state missing {field}")
    when = state.get("lastSuccessfulFullAcquisitionAt")
    snapshot = state.get("lastSuccessfulFullSnapshotID")
    if (when is None) != (snapshot is None):
        raise OperationalRefreshError("successful full acquisition timestamp/snapshot must be both null or both present")
    if when is not None:
        parse_time(when, "lastSuccessfulFullAcquisitionAt")
        if not isinstance(snapshot, str) or not snapshot:
            raise OperationalRefreshError("lastSuccessfulFullSnapshotID is invalid")
    parse_time(state.get("nextFullDueAt"), "nextFullDueAt")
    if state.get("stateSha256") != digest_without(state, "stateSha256"):
        raise OperationalRefreshError("refresh state digest mismatch")


def validate_operational_report(report: dict[str, Any]) -> None:
    for field in ("lastSuccessfulFullAcquisitionAt", "lastSuccessfulFullSnapshotID"):
        if field not in report:
            raise OperationalRefreshError(f"refresh report missing {field}")
    when = report.get("lastSuccessfulFullAcquisitionAt")
    snapshot = report.get("lastSuccessfulFullSnapshotID")
    if (when is None) != (snapshot is None):
        raise OperationalRefreshError("refresh report successful full acquisition timestamp/snapshot mismatch")
    if when is not None:
        parse_time(when, "report lastSuccessfulFullAcquisitionAt")
    parse_time(report.get("nextFullDueAt"), "report nextFullDueAt")
    if report.get("reportSha256") != digest_without(report, "reportSha256"):
        raise OperationalRefreshError("refresh report digest mismatch")


def merge_previous_state(
    *,
    accepted: dict[str, Any],
    operational: dict[str, Any] | None,
) -> dict[str, Any]:
    """Compose authoritative accepted lineage with the newest safe operational clock.

    `accepted` comes from protected main and owns every evidence/candidate field.
    `operational` may come from an immutable prior workflow artifact and contributes
    only the successful-full acquisition timestamp/snapshot and next-due time.
    """
    validate_operational_state(accepted)
    if accepted.get("candidateComplete") is not None or accepted.get("candidateEligible") is not False or accepted.get("candidateChangedFromAccepted") is not False:
        raise OperationalRefreshError("protected accepted refresh state contains an unpromoted candidate")
    result = json.loads(json.dumps(accepted))
    if operational is None:
        return result
    validate_operational_state(operational)
    if (
        operational.get("schemaVersion") != accepted.get("schemaVersion")
        or operational.get("sourceKey") != accepted.get("sourceKey")
        or operational.get("market") != accepted.get("market")
        or operational.get("policyVersion") != accepted.get("policyVersion")
    ):
        raise OperationalRefreshError("operational artifact identity differs from protected accepted state")

    accepted_at = parse_time(
        accepted.get("lastSuccessfulFullAcquisitionAt"),
        "accepted lastSuccessfulFullAcquisitionAt",
        allow_none=True,
    )
    operational_at = parse_time(
        operational.get("lastSuccessfulFullAcquisitionAt"),
        "operational lastSuccessfulFullAcquisitionAt",
        allow_none=True,
    )
    use_operational = False
    if operational_at is not None and accepted_at is None:
        use_operational = True
    elif operational_at is not None and accepted_at is not None:
        if operational_at > accepted_at:
            use_operational = True
        elif operational_at == accepted_at:
            accepted_snapshot = accepted.get("lastSuccessfulFullSnapshotID")
            operational_snapshot = operational.get("lastSuccessfulFullSnapshotID")
            if operational_snapshot != accepted_snapshot:
                raise OperationalRefreshError("equal successful-full timestamps identify different snapshots")
    if use_operational:
        result["lastSuccessfulFullAcquisitionAt"] = operational["lastSuccessfulFullAcquisitionAt"]
        result["lastSuccessfulFullSnapshotID"] = operational["lastSuccessfulFullSnapshotID"]
        result["nextFullDueAt"] = operational["nextFullDueAt"]
    result["stateSha256"] = digest_without(result, "stateSha256")
    validate_operational_state(result)
    return result


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--policy", type=Path, default=Path("Data/refresh/catalog-refresh-policy-v1.json"))
    apply.add_argument("--state", type=Path, required=True)
    apply.add_argument("--report", type=Path, required=True)
    apply.add_argument("--previous-state", type=Path)
    apply.add_argument("--state-output", type=Path, required=True)
    apply.add_argument("--report-output", type=Path, required=True)
    merge = sub.add_parser("merge-previous")
    merge.add_argument("--accepted-state", type=Path, required=True)
    merge.add_argument("--operational-state", type=Path)
    merge.add_argument("--output", type=Path, required=True)
    validate_state = sub.add_parser("validate-state")
    validate_state.add_argument("--input", type=Path, required=True)
    validate_report = sub.add_parser("validate-report")
    validate_report.add_argument("--input", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.command == "validate-state":
            value = load_json(args.input)
            assert value is not None
            validate_operational_state(value)
            print(f"Validated operational refresh state {value['stateSha256']}")
            return
        if args.command == "validate-report":
            value = load_json(args.input)
            assert value is not None
            validate_operational_report(value)
            print(f"Validated operational refresh report {value['reportSha256']}")
            return
        if args.command == "merge-previous":
            accepted = load_json(args.accepted_state)
            assert accepted is not None
            merged = merge_previous_state(
                accepted=accepted,
                operational=load_json(args.operational_state),
            )
            write_json(args.output, merged)
            print(
                f"Merged previous refresh state: source={merged['sourceKey']} "
                f"lastFull={merged['lastSuccessfulFullAcquisitionAt']} "
                f"accepted={((merged.get('acceptedComplete') or {}).get('snapshotID'))}"
            )
            return
        state = load_json(args.state)
        report = load_json(args.report)
        policy = load_json(args.policy)
        assert state is not None and report is not None and policy is not None
        updated_state, updated_report = apply_operational_clock(
            state=state,
            report=report,
            policy=policy,
            previous=load_json(args.previous_state),
        )
        validate_operational_state(updated_state)
        validate_operational_report(updated_report)
        write_json(args.state_output, updated_state)
        write_json(args.report_output, updated_report)
        print(
            f"Operational refresh clock: source={updated_state['sourceKey']} "
            f"lastFull={updated_state['lastSuccessfulFullAcquisitionAt']} "
            f"nextFull={updated_state['nextFullDueAt']}"
        )
    except OperationalRefreshError as exc:
        raise SystemExit(f"operational refresh state failed: {exc}") from exc


if __name__ == "__main__":
    main()
