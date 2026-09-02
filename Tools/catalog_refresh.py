#!/usr/bin/env python3
"""Deterministic source-refresh state and review-queue projection."""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SEMVER = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GTIN = re.compile(r"^[0-9]{8,14}$")


class RefreshError(ValueError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RefreshError(f"failed to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RefreshError(f"{path} must contain a JSON object")
    return value


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def digest_without(value: dict[str, Any], field: str) -> str:
    work = dict(value)
    work.pop(field, None)
    return hashlib.sha256(canonical(work)).hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def parse_time(value: Any, label: str, *, allow_none: bool = False) -> datetime | None:
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or not value.strip():
        raise RefreshError(f"{label} must be RFC3339")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RefreshError(f"{label} must be RFC3339") from exc
    if dt.tzinfo is None:
        raise RefreshError(f"{label} must be timezone-aware")
    return dt.astimezone(timezone.utc)


def stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def add_months(dt: datetime, months: int) -> datetime:
    total = dt.year * 12 + dt.month - 1 + months
    year, month0 = divmod(total, 12)
    month = month0 + 1
    day = min(dt.day, calendar.monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def validate_policy(policy: dict[str, Any]) -> dict[str, Any]:
    required = {"schemaVersion", "policyVersion", "market", "queue", "sources"}
    if set(policy) != required:
        raise RefreshError("refresh policy keys mismatch")
    if policy["schemaVersion"] != 1 or policy["market"] != "DE":
        raise RefreshError("unsupported refresh policy identity")
    if not isinstance(policy["policyVersion"], str) or not SEMVER.fullmatch(policy["policyVersion"]):
        raise RefreshError("policyVersion must be semver")
    queue = policy["queue"]
    if not isinstance(queue, dict) or set(queue) != {"maxEntries", "certificationDueDays", "assessmentDueDays"}:
        raise RefreshError("queue policy is invalid")
    for key in queue:
        if not isinstance(queue[key], int) or isinstance(queue[key], bool) or queue[key] < 1:
            raise RefreshError(f"queue.{key} must be positive")
    sources = policy["sources"]
    if not isinstance(sources, dict) or set(sources) != {"open-food-facts", "open-prices"}:
        raise RefreshError("v1 refresh policy must define OFF and Open Prices")
    for key, source in sources.items():
        if not isinstance(source, dict):
            raise RefreshError(f"sources.{key} must be object")
        expected = {
            "adapterVersion",
            "fullCadenceDays",
            "targetedCadenceHours",
            "supportedAcquisitionModes",
            "conditionalMetadata",
            "targetedQueue",
        }
        if set(source) != expected:
            raise RefreshError(f"sources.{key} keys mismatch")
        if not isinstance(source["adapterVersion"], str) or not SEMVER.fullmatch(source["adapterVersion"]):
            raise RefreshError(f"sources.{key}.adapterVersion invalid")
        if not isinstance(source["fullCadenceDays"], int) or source["fullCadenceDays"] < 1:
            raise RefreshError(f"sources.{key}.fullCadenceDays invalid")
        if source["supportedAcquisitionModes"] != ["full"]:
            raise RefreshError(f"sources.{key} supports only reviewed full acquisition in v1")
        target = source["targetedQueue"]
        required_target = {
            "enabled",
            "endpointReference",
            "maxGtinsPerRun",
            "batchSize",
            "maxRequestsPerMinute",
            "minimumRequestIntervalSeconds",
            "fields",
        }
        if not isinstance(target, dict) or set(target) != required_target:
            raise RefreshError(f"sources.{key}.targetedQueue invalid")
        if target["enabled"]:
            for field in ("maxGtinsPerRun", "batchSize", "maxRequestsPerMinute"):
                if not isinstance(target[field], int) or target[field] < 1:
                    raise RefreshError(f"sources.{key}.targetedQueue.{field} invalid")
            interval = target["minimumRequestIntervalSeconds"]
            if not isinstance(interval, (int, float)) or interval <= 0:
                raise RefreshError("targeted request interval invalid")
            if 60.0 / interval > target["maxRequestsPerMinute"] + 1e-9:
                raise RefreshError("targeted request interval exceeds declared rate limit")
            if target["batchSize"] > target["maxGtinsPerRun"]:
                raise RefreshError("targeted batch exceeds run bound")
        elif any(
            target[field] != 0
            for field in (
                "maxGtinsPerRun",
                "batchSize",
                "maxRequestsPerMinute",
                "minimumRequestIntervalSeconds",
            )
        ):
            raise RefreshError(f"disabled targeted queue for {key} must have zero bounds")
    return policy


def _source_digest(meta: dict[str, Any]) -> str:
    value = meta.get("transportSha256") or meta.get("payloadSha256")
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise RefreshError("acquisition metadata lacks valid payload/transport SHA-256")
    return value


def _upstream(meta: dict[str, Any]) -> dict[str, Any]:
    if isinstance(meta.get("httpMetadata"), dict):
        headers = meta["httpMetadata"]
        return {"etag": headers.get("etag"), "lastModified": headers.get("last-modified")}
    if isinstance(meta.get("upstreamExports"), dict):
        result = {}
        for key in sorted(meta["upstreamExports"]):
            item = meta["upstreamExports"][key]
            if isinstance(item, dict):
                result[key] = {
                    "etag": item.get("etag"),
                    "lastModified": item.get("lastModified"),
                    "sha256": item.get("sha256"),
                }
        return result
    return {}


def _attempt(
    meta: dict[str, Any],
    source_key: str,
    source_policy_sha: str,
    adapter_version: str,
    quality: dict[str, Any],
) -> dict[str, Any]:
    if meta.get("sourceKey") != source_key:
        raise RefreshError("acquisition sourceKey mismatch")
    snapshot = meta.get("snapshotID")
    mode = meta.get("mode")
    retrieved = meta.get("retrievedAt")
    if not isinstance(snapshot, str) or not snapshot or not isinstance(mode, str):
        raise RefreshError("acquisition identity invalid")
    parse_time(retrieved, "retrievedAt")
    complete = meta.get("downloadComplete") is True and mode == "full"
    records = meta.get("recordsEmitted")
    if not isinstance(records, int) or isinstance(records, bool) or records < 0:
        raise RefreshError("recordsEmitted invalid")
    quality_status = quality.get("status")
    if not isinstance(quality_status, str):
        raise RefreshError("quality report status missing")
    return {
        "snapshotID": snapshot,
        "mode": mode,
        "status": "complete" if complete else "partial",
        "retrievedAt": retrieved,
        "contentSha256": _source_digest(meta),
        "recordCount": records,
        "upstream": _upstream(meta),
        "adapterVersion": adapter_version,
        "sourcePolicySha256": source_policy_sha,
        "qualityStatus": quality_status,
    }


def _valid_previous(
    previous: dict[str, Any] | None,
    source_key: str,
    market: str,
) -> dict[str, Any] | None:
    if previous is None:
        return None
    if (
        previous.get("schemaVersion") != 1
        or previous.get("sourceKey") != source_key
        or previous.get("market") != market
    ):
        raise RefreshError("previous refresh state identity mismatch")
    accepted = previous.get("acceptedComplete")
    if accepted is not None and (
        not isinstance(accepted, dict) or accepted.get("status") != "complete"
    ):
        raise RefreshError("previous acceptedComplete invalid")
    return accepted


def _queue_entries(
    evidence: dict[str, Any],
    quality: dict[str, Any],
    quality_policy: dict[str, Any],
    now: datetime,
    max_entries: int,
    due_days: int,
    change: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    selections = evidence.get("currentSelections", [])
    ingredients = evidence.get("ingredients", [])
    assessments = evidence.get("assessments", [])
    certifications = evidence.get("certifications", [])
    if not all(isinstance(value, list) for value in (selections, ingredients, assessments, certifications)):
        raise RefreshError("evidence envelope refresh collections invalid")
    ingredient_by_id = {
        item.get("id"): item
        for item in ingredients
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    assessment_by_id = {
        item.get("id"): item
        for item in assessments
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    certificate_by_id = {
        item.get("id"): item
        for item in certifications
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    formulation = quality_policy["freshness"]["formulation"]
    refresh_months = formulation["refreshRecommendedMonths"]
    stale_months = formulation["staleMonths"]
    entries: dict[str, dict[str, Any]] = {}

    def add(
        reason: str,
        gtin: str | None,
        market: str | None,
        priority: str,
        detail: str,
        evidence_id: str | None = None,
    ) -> None:
        key = f"{reason}:{market or '-'}:{gtin or '-'}:{evidence_id or '-'}"
        entries[key] = {
            "key": key,
            "reason": reason,
            "priority": priority,
            "gtin": gtin,
            "market": market,
            "evidenceID": evidence_id,
            "detail": detail,
        }

    for selection in selections:
        if not isinstance(selection, dict):
            continue
        gtin = selection.get("gtin") if isinstance(selection.get("gtin"), str) else None
        market = selection.get("market") if isinstance(selection.get("market"), str) else None
        ingredient_id = selection.get("ingredientObservationID")
        current = ingredient_by_id.get(ingredient_id) if isinstance(ingredient_id, str) else None
        if current is None:
            add(
                "missing-current-ingredients",
                gtin,
                market,
                "high",
                "Current product selection has no exact current ingredient observation.",
            )
        else:
            observed = parse_time(current.get("observedAt"), "ingredient observedAt", allow_none=True)
            if observed is None:
                add(
                    "date-unknown-ingredients",
                    gtin,
                    market,
                    "high",
                    "Current ingredient formulation has no trustworthy observedAt date.",
                    current.get("id"),
                )
            elif now >= add_months(observed, stale_months):
                add(
                    "stale-ingredients",
                    gtin,
                    market,
                    "high",
                    "Current ingredient formulation is beyond the accepted stale threshold.",
                    current.get("id"),
                )
            elif now >= add_months(observed, refresh_months):
                add(
                    "refresh-recommended-ingredients",
                    gtin,
                    market,
                    "medium",
                    "Current ingredient formulation reached the refresh-recommended threshold.",
                    current.get("id"),
                )
        flags = selection.get("conflictFlags")
        if isinstance(flags, list) and flags:
            add(
                "identity-or-formulation-conflict",
                gtin,
                market,
                "high",
                "Current selection contains unresolved conflict flags.",
                selection.get("id"),
            )
        assessment_id = selection.get("assessmentID")
        assessment = assessment_by_id.get(assessment_id) if isinstance(assessment_id, str) else None
        if assessment:
            recheck = parse_time(assessment.get("recheckAt"), "assessment recheckAt", allow_none=True)
            if recheck is not None and recheck <= now + timedelta(days=due_days):
                add(
                    "assessment-recheck",
                    gtin,
                    market,
                    "high",
                    "Assessment recheck is due or approaching.",
                    assessment_id,
                )
            if assessment.get("status") in {"questionable", "unknown"}:
                add(
                    "ambiguous-review",
                    gtin,
                    market,
                    "medium",
                    "Current assessment remains questionable or unknown.",
                    assessment_id,
                )
        certificate_ids = (
            selection.get("certificationIDs", [])
            if isinstance(selection.get("certificationIDs"), list)
            else []
        )
        for certificate_id in certificate_ids:
            certificate = certificate_by_id.get(certificate_id)
            if not certificate:
                continue
            expiry = parse_time(certificate.get("expiryAt"), "certificate expiryAt", allow_none=True)
            if expiry is not None and expiry <= now + timedelta(days=due_days):
                add(
                    "certification-expiry",
                    gtin,
                    market,
                    "high",
                    "Linked certificate is expired or approaching expiry.",
                    certificate_id,
                )

    if isinstance(change, dict):
        review_queue = change.get("reviewQueue", [])
        if isinstance(review_queue, list):
            for item in review_queue:
                if isinstance(item, dict):
                    gtin = item.get("gtin") if isinstance(item.get("gtin"), str) else None
                    market = item.get("market") if isinstance(item.get("market"), str) else None
                    evidence_id = item.get("id") if isinstance(item.get("id"), str) else None
                    reason = (
                        item.get("reason")
                        if isinstance(item.get("reason"), str)
                        else "changed evidence requires review"
                    )
                    add("changed-unreviewed", gtin, market, "high", reason, evidence_id)
                elif isinstance(item, str) and item:
                    add(
                        "changed-unreviewed",
                        None,
                        None,
                        "high",
                        "Change report contains a pending review item.",
                        item,
                    )
        count = change.get("formulationChanges")
        if isinstance(count, int) and not isinstance(count, bool) and count > 0 and not review_queue:
            add(
                "changed-unreviewed",
                None,
                None,
                "high",
                f"Change report contains {count} formulation change(s) requiring review.",
            )

    findings = quality.get("releaseBlockingFindings", [])
    if isinstance(findings, list):
        for item in findings:
            if isinstance(item, dict) and isinstance(item.get("code"), str):
                add(
                    "source-or-quality-blocker",
                    None,
                    None,
                    "high",
                    f"Quality blocker {item['code']} requires source/review attention.",
                    item["code"],
                )
    ordered = sorted(
        entries.values(),
        key=lambda item: (
            {"high": 0, "medium": 1, "low": 2}[item["priority"]],
            item["reason"],
            item.get("market") or "",
            item.get("gtin") or "",
            item["key"],
        ),
    )
    return ordered[:max_entries]


def _targeted(entries: list[dict[str, Any]], policy: dict[str, Any]) -> dict[str, Any]:
    target = policy["sources"]["open-food-facts"]["targetedQueue"]
    reasons = {
        "missing-current-ingredients",
        "date-unknown-ingredients",
        "stale-ingredients",
        "refresh-recommended-ingredients",
        "identity-or-formulation-conflict",
        "ambiguous-review",
        "assessment-recheck",
    }
    gtins = sorted(
        {
            item["gtin"]
            for item in entries
            if item.get("reason") in reasons
            and isinstance(item.get("gtin"), str)
            and GTIN.fullmatch(item["gtin"])
        }
    )[: target["maxGtinsPerRun"]]
    size = target["batchSize"]
    batches = [gtins[index : index + size] for index in range(0, len(gtins), size)] if size else []
    return {
        "sourceKey": "open-food-facts",
        "enabled": target["enabled"],
        "endpointReference": target["endpointReference"],
        "gtinCount": len(gtins),
        "batchSize": size,
        "batches": batches,
        "maxRequestsPerMinute": target["maxRequestsPerMinute"],
        "minimumRequestIntervalSeconds": target["minimumRequestIntervalSeconds"],
        "minimumExecutionSeconds": (
            0
            if not batches
            else math.ceil(max(0, len(batches) - 1) * target["minimumRequestIntervalSeconds"])
        ),
        "fields": target["fields"],
        "networkExecutionPerformed": False,
    }


def evaluate(
    *,
    policy: dict[str, Any],
    quality_policy: dict[str, Any],
    source_policy: dict[str, Any],
    acquisition: dict[str, Any],
    evidence: dict[str, Any],
    quality: dict[str, Any],
    change: dict[str, Any] | None,
    previous: dict[str, Any] | None,
    evaluated_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    validate_policy(policy)
    now = parse_time(evaluated_at, "evaluatedAt")
    assert now is not None
    source_key = acquisition.get("sourceKey")
    if source_key not in policy["sources"]:
        raise RefreshError("source is not admitted by refresh policy")
    source_config = policy["sources"][source_key]
    source_policy_sha = hashlib.sha256(canonical(source_policy)).hexdigest()
    attempt = _attempt(
        acquisition,
        source_key,
        source_policy_sha,
        source_config["adapterVersion"],
        quality,
    )
    previous_accepted = _valid_previous(previous, source_key, policy["market"])
    eligible = attempt["status"] == "complete" and attempt["qualityStatus"] == "pass"
    same = (
        previous_accepted is not None
        and previous_accepted.get("snapshotID") == attempt["snapshotID"]
        and previous_accepted.get("contentSha256") == attempt["contentSha256"]
    )
    advanced = bool(eligible and not same)
    anchor = (
        parse_time((attempt if eligible else previous_accepted)["retrievedAt"], "refresh anchor retrievedAt")
        if (eligible or previous_accepted)
        else now
    )
    assert anchor is not None
    state = {
        "schemaVersion": 1,
        "sourceKey": source_key,
        "market": policy["market"],
        "policyVersion": policy["policyVersion"],
        "evaluatedAt": stamp(now),
        "acceptedComplete": previous_accepted,
        "candidateComplete": attempt if eligible else None,
        "lastAttempt": attempt,
        "nextFullDueAt": stamp(anchor + timedelta(days=source_config["fullCadenceDays"])),
        "candidateEligible": eligible,
        "candidateChangedFromAccepted": advanced,
    }
    state["stateSha256"] = digest_without(state, "stateSha256")
    entries = _queue_entries(
        evidence,
        quality,
        quality_policy,
        now,
        policy["queue"]["maxEntries"],
        max(policy["queue"]["assessmentDueDays"], policy["queue"]["certificationDueDays"]),
        change,
    )
    queue = {
        "schemaVersion": 1,
        "market": policy["market"],
        "evaluatedAt": stamp(now),
        "entries": entries,
        "targetedExecution": _targeted(entries, policy),
    }
    queue["queueSha256"] = digest_without(queue, "queueSha256")
    report = {
        "schemaVersion": 1,
        "sourceKey": source_key,
        "snapshotID": attempt["snapshotID"],
        "mode": attempt["mode"],
        "evaluatedAt": stamp(now),
        "attemptStatus": attempt["status"],
        "qualityStatus": attempt["qualityStatus"],
        "candidateEligible": eligible,
        "candidateChangedFromAccepted": advanced,
        "acceptedSnapshotID": previous_accepted.get("snapshotID") if previous_accepted else None,
        "candidateSnapshotID": attempt.get("snapshotID") if eligible else None,
        "queueCount": len(entries),
    }
    report["reportSha256"] = digest_without(report, "reportSha256")
    return state, report, queue


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate-policy")
    validate.add_argument(
        "--policy",
        type=Path,
        default=Path("Data/refresh/catalog-refresh-policy-v1.json"),
    )
    evaluate_parser = sub.add_parser("evaluate")
    for name in (
        "policy",
        "quality-policy",
        "source-policy",
        "acquisition-metadata",
        "evidence",
        "quality-report",
    ):
        evaluate_parser.add_argument(
            "--" + name,
            type=Path,
            required=name not in {"policy", "quality-policy"},
            default=(
                Path("Data/refresh/catalog-refresh-policy-v1.json")
                if name == "policy"
                else Path("Data/quality/catalog-quality-policy-v1.json")
                if name == "quality-policy"
                else None
            ),
        )
    evaluate_parser.add_argument("--change-report", type=Path)
    evaluate_parser.add_argument("--previous-state", type=Path)
    evaluate_parser.add_argument("--evaluated-at", required=True)
    evaluate_parser.add_argument("--state-output", type=Path, required=True)
    evaluate_parser.add_argument("--report-output", type=Path, required=True)
    evaluate_parser.add_argument("--queue-output", type=Path, required=True)
    queues = sub.add_parser("queues")
    queues.add_argument(
        "--policy",
        type=Path,
        default=Path("Data/refresh/catalog-refresh-policy-v1.json"),
    )
    queues.add_argument(
        "--quality-policy",
        type=Path,
        default=Path("Data/quality/catalog-quality-policy-v1.json"),
    )
    queues.add_argument("--evidence", type=Path, required=True)
    queues.add_argument("--quality-report", type=Path, required=True)
    queues.add_argument("--change-report", type=Path)
    queues.add_argument("--evaluated-at", required=True)
    queues.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        policy = load_json(args.policy)
        validate_policy(policy)
        if args.command == "validate-policy":
            print(f"Validated catalog refresh policy {policy['policyVersion']}")
            return
        quality_policy = load_json(args.quality_policy)
        evidence = load_json(args.evidence)
        quality = load_json(args.quality_report)
        if args.command == "queues":
            now = parse_time(args.evaluated_at, "evaluatedAt")
            assert now is not None
            entries = _queue_entries(
                evidence,
                quality,
                quality_policy,
                now,
                policy["queue"]["maxEntries"],
                max(
                    policy["queue"]["assessmentDueDays"],
                    policy["queue"]["certificationDueDays"],
                ),
                load_json(args.change_report) if args.change_report else None,
            )
            output = {
                "schemaVersion": 1,
                "market": policy["market"],
                "evaluatedAt": stamp(now),
                "entries": entries,
                "targetedExecution": _targeted(entries, policy),
            }
            output["queueSha256"] = digest_without(output, "queueSha256")
            write_json(args.output, output)
            print(f"Refresh queue entries: {len(entries)}")
            return
        previous = load_json(args.previous_state) if args.previous_state else None
        state, report, queue = evaluate(
            policy=policy,
            quality_policy=quality_policy,
            source_policy=load_json(args.source_policy),
            acquisition=load_json(args.acquisition_metadata),
            evidence=evidence,
            quality=quality,
            change=load_json(args.change_report) if args.change_report else None,
            previous=previous,
            evaluated_at=args.evaluated_at,
        )
        write_json(args.state_output, state)
        write_json(args.report_output, report)
        write_json(args.queue_output, queue)
        print(
            f"Refresh state {state['sourceKey']}: {report['attemptStatus']} / "
            f"quality {report['qualityStatus']} / queue {report['queueCount']}"
        )
    except RefreshError as exc:
        raise SystemExit(f"catalog refresh validation failed: {exc}") from exc


if __name__ == "__main__":
    main()
