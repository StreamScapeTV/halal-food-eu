#!/usr/bin/env python3
"""Analyze ingredient evidence and materialize explicit halal review artifacts."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evidence_model_core import EvidenceValidationError, validate_envelope
from halal_methodology_batch import analyze_envelope
from halal_methodology_core import (
    MethodologyError,
    analyze_ingredient,
    assessment_migration_report,
    complete_review,
    validate_methodology,
    validity_events_from_migration,
)

DEFAULT_METHODOLOGY = Path("Data/methodology/halal-methodology-v1.json")


def load_json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MethodologyError(f"failed to load JSON {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise MethodologyError(f"{path} must contain a JSON object")
    return raw


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _selection(envelope: dict[str, Any], gtin: str, market: str) -> dict[str, Any] | None:
    matches = [
        item for item in envelope.get("currentSelections", [])
        if isinstance(item, dict) and item.get("gtin") == gtin and item.get("market") == market
    ]
    if len(matches) > 1:
        raise MethodologyError("multiple current selections exist for GTIN/market")
    return matches[0] if matches else None


def _ingredient(envelope: dict[str, Any], ingredient_id: Any) -> dict[str, Any] | None:
    if not isinstance(ingredient_id, str):
        return None
    return next((item for item in envelope.get("ingredients", []) if isinstance(item, dict) and item.get("id") == ingredient_id), None)


def _certifications(envelope: dict[str, Any], ids: list[str]) -> list[dict[str, Any]]:
    by_id = {item["id"]: item for item in envelope.get("certifications", []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
    return [by_id[item] for item in ids if item in by_id]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate", help="validate the committed methodology data")
    validate.add_argument("--methodology", type=Path, default=DEFAULT_METHODOLOGY)

    analyze = sub.add_parser("analyze", help="analyze one current GTIN/market ingredient observation")
    analyze.add_argument("--methodology", type=Path, default=DEFAULT_METHODOLOGY)
    analyze.add_argument("--evidence", type=Path, required=True)
    analyze.add_argument("--gtin", required=True)
    analyze.add_argument("--market", default="DE")
    analyze.add_argument("--freshness-state", choices=["fresh", "refresh-recommended", "stale", "date-unknown", "changed-unreviewed"], default="fresh")
    analyze.add_argument("--output", type=Path, required=True)

    batch = sub.add_parser("analyze-envelope", help="analyze every current selection and emit one deterministic methodology report")
    batch.add_argument("--methodology", type=Path, default=DEFAULT_METHODOLOGY)
    batch.add_argument("--evidence", type=Path, required=True)
    batch.add_argument("--quality-report", type=Path)
    batch.add_argument("--output", type=Path, required=True)

    review = sub.add_parser("review", help="materialize immutable assessment/review records from explicit human review input")
    review.add_argument("--methodology", type=Path, default=DEFAULT_METHODOLOGY)
    review.add_argument("--evidence", type=Path, required=True)
    review.add_argument("--analysis", type=Path, required=True)
    review.add_argument("--review-input", type=Path, required=True)
    review.add_argument("--output", type=Path, required=True)

    migrate = sub.add_parser("migrate", help="report current-assessment compatibility with the current methodology/formulation")
    migrate.add_argument("--methodology", type=Path, default=DEFAULT_METHODOLOGY)
    migrate.add_argument("--evidence", type=Path, required=True)
    migrate.add_argument("--occurred-at", required=True)
    migrate.add_argument("--output", type=Path, required=True)
    return root


def main() -> None:
    args = parser().parse_args()
    try:
        methodology = load_json(args.methodology)
        validate_methodology(methodology)
        if args.command == "validate":
            print(f"Validated halal methodology {methodology['methodologyVersion']}")
            return

        envelope = load_json(args.evidence)
        validate_envelope(envelope)
        if args.command == "analyze":
            selection = _selection(envelope, args.gtin, args.market)
            if selection is None:
                ingredient = None
                conflicts: list[str] = []
            else:
                ingredient = _ingredient(envelope, selection.get("ingredientObservationID"))
                conflicts = [str(item) for item in selection.get("conflictFlags", []) if isinstance(item, str)]
            report = analyze_ingredient(
                ingredient,
                methodology,
                gtin=args.gtin,
                market=args.market,
                freshness_state=args.freshness_state,
                conflict_flags=conflicts,
            )
            write_json(args.output, report)
            print(f"Methodology parser status: {report['parserStatus']} ({len(report['candidateFindings'])} candidates, {len(report['reviewQueues'])} queues)")
            return

        if args.command == "analyze-envelope":
            quality_report = load_json(args.quality_report) if args.quality_report else None
            report = analyze_envelope(envelope=envelope, methodology=methodology, quality_report=quality_report)
            write_json(args.output, report)
            print(
                f"Methodology envelope: {report['counts']['products']} products, "
                f"{sum(report['counts']['reviewQueues'].values())} queued review routes"
            )
            return

        if args.command == "review":
            report = load_json(args.analysis)
            review_input = load_json(args.review_input)
            selection = _selection(envelope, str(report.get("gtin")), str(report.get("market")))
            cert_ids = [] if selection is None else [item for item in selection.get("certificationIDs", []) if isinstance(item, str)]
            result = complete_review(
                report=report,
                methodology=methodology,
                review_input=review_input,
                certifications=_certifications(envelope, cert_ids),
            )
            write_json(args.output, result)
            print(f"Materialized reviewed assessment {result['assessment']['id']} with status {result['assessment']['status']}")
            return

        migration = assessment_migration_report(envelope=envelope, methodology=methodology)
        migration["validityEvents"] = validity_events_from_migration(migration, occurred_at=args.occurred_at)
        write_json(args.output, migration)
        print(f"Methodology migration: {migration['invalidated']} invalidated, {migration['carriedForward']} carried forward")
    except (MethodologyError, EvidenceValidationError) as exc:
        raise SystemExit(f"halal methodology failed: {exc}") from exc


if __name__ == "__main__":
    main()
