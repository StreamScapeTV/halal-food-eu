#!/usr/bin/env python3
"""Catalog-health refresh facade with deterministic operator recovery metadata."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

TOOLS = str(Path(__file__).resolve().parent)
if TOOLS not in sys.path:
    sys.path.insert(0, TOOLS)

import catalog_health
import catalog_refresh_health_core as core
import catalog_refresh_recovery as recovery

RefreshHealthError = core.RefreshHealthError
load_json = core.load_json
canonical = core.canonical
digest_without = core.digest_without
write_json = core.write_json

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_POLICY = ROOT / "Data/refresh/catalog-refresh-policy-v1.json"
DEFAULT_WORKFLOW = ROOT / ".github/workflows/scheduled-catalog-refresh.yml"


def _recovery_inputs(
    refresh_policy: dict[str, Any] | None,
    scheduled_workflow_text: str | None,
) -> tuple[dict[str, Any], str]:
    policy = refresh_policy if refresh_policy is not None else load_json(DEFAULT_POLICY)
    if policy is None:
        raise RefreshHealthError("refresh policy is unavailable")
    if scheduled_workflow_text is None:
        try:
            scheduled_workflow_text = DEFAULT_WORKFLOW.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise RefreshHealthError(f"failed to read scheduled refresh workflow: {exc}") from exc
    return policy, scheduled_workflow_text


def enrich_health(
    *,
    base_health: dict[str, Any],
    refresh_queue: dict[str, Any],
    refresh_plan: dict[str, Any],
    refresh_report: dict[str, Any] | None = None,
    workflow_statuses: list[dict[str, Any]] | None = None,
    refresh_policy: dict[str, Any] | None = None,
    scheduled_workflow_text: str | None = None,
) -> dict[str, Any]:
    report = core.enrich_health(
        base_health=base_health,
        refresh_queue=refresh_queue,
        refresh_plan=refresh_plan,
        refresh_report=refresh_report,
        workflow_statuses=workflow_statuses,
    )
    policy, workflow_text = _recovery_inputs(refresh_policy, scheduled_workflow_text)
    report["refresh"]["operatorRecovery"] = recovery.build_operator_recovery(
        evaluated_at=str(report.get("evaluatedAt", "")),
        refresh_policy=policy,
        scheduled_workflow_text=workflow_text,
        error=RefreshHealthError,
    )
    report["reportSha256"] = digest_without(report, "reportSha256")
    validate_refresh_health(report)
    return report


def validate_refresh_health(report: dict[str, Any]) -> None:
    core.validate_refresh_health(report)
    recovery.validate_operator_recovery(report.get("refresh", {}).get("operatorRecovery"), RefreshHealthError)
    if report.get("reportSha256") != digest_without(report, "reportSha256"):
        raise RefreshHealthError("refresh-enriched health digest mismatch")


def human_summary(report: dict[str, Any]) -> str:
    validate_refresh_health(report)
    lines = core.human_summary(report).rstrip().splitlines()
    insertion = next((i for i, line in enumerate(lines) if line.startswith("- Refresh incident keys:")), len(lines))
    operator = report["refresh"]["operatorRecovery"]
    extra = [
        f"- Manual recovery workflow: `{operator['workflow']}` from `{operator['ref']}` (`workflow_dispatch`)"
    ]
    for source in operator["sources"].values():
        extra.append(
            f"- Manual recovery `{source['sourceKey']}`: `source_key={source['sourceKey']}`, `mode=full`, "
            f"provide a unique `snapshot_id`, leave `catalog_version` empty; next scheduled full refresh "
            f"`{source['nextScheduledAt']}` (`{source['scheduleCronUTC']}` UTC)"
        )
    lines[insertion:insertion] = extra
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    enrich = sub.add_parser("enrich", help="enrich one validated catalog-health v1 report")
    enrich.add_argument("--base-health", type=Path, required=True)
    enrich.add_argument("--refresh-queue", type=Path, required=True)
    enrich.add_argument("--refresh-plan", type=Path, required=True)
    enrich.add_argument("--refresh-report", type=Path)
    enrich.add_argument("--workflow-status", type=Path, action="append", default=[])
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
        base, queue, plan = load_json(args.base_health), load_json(args.refresh_queue), load_json(args.refresh_plan)
        assert base is not None and queue is not None and plan is not None
        statuses = []
        for path in args.workflow_status:
            value = load_json(path)
            assert value is not None
            statuses.append(value)
        report = enrich_health(
            base_health=base,
            refresh_queue=queue,
            refresh_plan=plan,
            refresh_report=load_json(args.refresh_report),
            workflow_statuses=statuses,
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
