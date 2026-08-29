#!/usr/bin/env python3
"""Public evidence-contract validator and runtime projector.

The implementation core is kept separate so source-scope checks can evolve without
mixing acquisition policy into the immutable record validator. This module is the
stable CLI/API entry point used by CI and downstream workflow code.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import evidence_model_core as _core
from evidence_model_core import *  # noqa: F401,F403

SOURCE_SCOPED_COLLECTIONS = (
    "identities",
    "ingredients",
    "retailerEvidence",
    "remoteImages",
    "certifications",
)


def _validate_source_markets(data: dict[str, Any]) -> None:
    source_markets = {
        source["sourceKey"]: set(source["markets"])
        for source in data["sources"]
    }
    for collection in SOURCE_SCOPED_COLLECTIONS:
        for index, record in enumerate(data[collection]):
            source_key = record["sourceKey"]
            market = record["market"]
            declared = source_markets.get(source_key)
            if declared is None:
                raise EvidenceValidationError(
                    f"{collection}[{index}].sourceKey: unknown source {source_key!r}"
                )
            if market not in declared:
                raise EvidenceValidationError(
                    f"{collection}[{index}].sourceKey: source {source_key!r} "
                    f"does not declare market {market}"
                )


def validate_envelope(data: Any) -> dict[str, Any]:
    validated = _core.validate_envelope(data)
    _validate_source_markets(validated)
    return validated


def runtime_projection(data: dict[str, Any]) -> dict[str, Any]:
    validate_envelope(data)
    return _core.runtime_projection(data)


def write_projection(input_path: Path, output_path: Path) -> None:
    projection = runtime_projection(_core.load_json(input_path))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(projection, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    args = _core.parse_args()
    if args.command == "validate":
        data = _core.load_json(args.input)
        validate_envelope(data)
        print(
            f"Validated evidence schema v{data['schemaVersion']} with "
            f"{len(data['currentSelections'])} current product selections"
        )
    else:
        write_projection(args.input, args.output)
        print(f"Wrote runtime projection to {args.output}")


if __name__ == "__main__":
    main()
