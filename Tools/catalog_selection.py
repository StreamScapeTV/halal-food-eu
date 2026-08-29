#!/usr/bin/env python3
"""Validate/evaluate the Halal Food EU catalog-selection policy."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from catalog_selection_contract import SelectionValidationError, validate_bundle, validate_policy
from catalog_selection_engine import evaluate_bundle


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate", help="validate policy and candidate input")
    validate.add_argument("--policy", required=True, type=Path)
    validate.add_argument("--input", required=True, type=Path)

    evaluate = subparsers.add_parser("evaluate", help="evaluate candidates deterministically")
    evaluate.add_argument("--policy", required=True, type=Path)
    evaluate.add_argument("--input", required=True, type=Path)
    evaluate.add_argument("--output", required=True, type=Path)
    evaluate.add_argument("--compare-policy", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        policy = load_json(args.policy)
        bundle = load_json(args.input)
        if args.command == "validate":
            validate_policy(policy)
            validate_bundle(bundle)
            print(
                f"Validated selection policy {policy['policyVersion']} with "
                f"{len(bundle['candidates'])} candidates"
            )
            return

        previous = load_json(args.compare_policy) if args.compare_policy else None
        output = evaluate_bundle(policy, bundle, previous_policy_data=previous)
        write_json(args.output, output)
        print(
            f"Evaluated {output['report']['sourceRecordsExamined']} candidates: "
            f"{output['report']['includedProducts']} detailed, "
            f"{output['report']['excludedBasicProducts']} basic exclusions, "
            f"{output['report']['excludedInvalidRecords']} invalid exclusions"
        )
    except (OSError, json.JSONDecodeError, SelectionValidationError) as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
