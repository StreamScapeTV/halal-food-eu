#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import sys

MAX_RAW_BYTES = 2048
OPERATIONS = {"build", "test", "full", "ui-test", "release"}
ALLOWED_KEYS = {
    "build": {"product_target"},
    "test": {"product_target", "test_selectors"},
    "full": {"product_target"},
    "ui-test": {"product_target", "ui_mode"},
    "release": {"product_target", "release_kind", "build_identity"},
}
TARGETS = {"app", "ios", "halal-food-eu"}
UI_MODES = {"smoke", "full-ordinary"}
BUILD_IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,63}\Z")


def fail(message: str) -> "NoReturn":
    print(f"ci_input_error: {message}", file=sys.stderr)
    raise SystemExit(2)


def load(operation: str) -> dict[str, object]:
    raw = os.environ.get("CI_INPUTS_JSON", '{"schemaVersion":1,"inputs":{}}')
    if len(raw.encode("utf-8")) > MAX_RAW_BYTES:
        fail("semantic input payload exceeds 2048 bytes")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        fail("semantic input payload is not valid JSON")
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        fail("semantic input schemaVersion must be 1")
    inputs = value.get("inputs")
    if not isinstance(inputs, dict):
        fail("semantic inputs must be an object")
    if set(inputs) - ALLOWED_KEYS[operation]:
        fail("semantic inputs contain unsupported fields")

    target = inputs.get("product_target")
    if target is not None and target not in TARGETS:
        fail("product_target is unsupported")

    if operation == "test":
        selectors = inputs.get("test_selectors", [])
        if not isinstance(selectors, list):
            fail("test_selectors must be a list")
        if selectors:
            fail("selective XCTest execution is not part of the reviewed repository contract")

    if operation == "ui-test":
        mode = inputs.get("ui_mode")
        if mode is not None and mode not in UI_MODES:
            fail("ui_mode is unsupported")

    if operation == "release":
        if inputs.get("release_kind") != "prepare":
            fail("release_kind must be prepare")
        identity = inputs.get("build_identity")
        if not isinstance(identity, str) or BUILD_IDENTITY.fullmatch(identity) is None:
            fail("build_identity is outside the reviewed bound")

    return inputs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=sorted(OPERATIONS))
    args = parser.parse_args()
    print(json.dumps({"operation": args.operation, "inputs": load(args.operation)}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
