"""Shared constants and local validation primitives for catalog workflow tooling."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

CONTRACT_SCHEMA_VERSION = 1
HANDOFF_SCHEMA_VERSION = 1
REPOSITORY = "StreamScapeTV/halal-food-eu"
SAFE_KEY = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
SAFE_SNAPSHOT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SAFE_WORKFLOW = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
SHA40 = re.compile(r"^[0-9a-f]{40}$")
SHA64 = re.compile(r"^[0-9a-f]{64}$")
RUN_ID = re.compile(r"^[0-9]{1,32}$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-[0-9A-Za-z.-]+)?$")
ARTIFACT_CLASSES = {"restricted", "redistributable", "metadata-only"}
COMPLETENESS = {"complete", "partial"}
ALLOWED_SOURCE_CLASSES = {"fixture", "open-database", "official-feed", "community", "reference"}
ALLOWED_ACCESS_METHODS = {"committed-fixture", "https-export", "https-api", "sftp", "manual-admission"}


class ContractError(ValueError):
    pass


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ContractError(f"cannot read JSON {path}: {exc}") from exc


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object")
    return value


def exact_keys(value: dict[str, Any], *, required: set[str], optional: set[str], label: str) -> None:
    missing = sorted(required - value.keys())
    unexpected = sorted(value.keys() - required - optional)
    if missing:
        raise ContractError(f"{label} missing required fields: {', '.join(missing)}")
    if unexpected:
        raise ContractError(f"{label} has unexpected fields: {', '.join(unexpected)}")


def positive_int(value: Any, label: str, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{label} must be an integer")
    minimum = 0 if allow_zero else 1
    if value < minimum:
        raise ContractError(f"{label} must be >= {minimum}")
    return value


def parse_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ContractError(f"{label} must be an RFC3339 UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ContractError(f"{label} is not a valid timestamp") from exc
    return value


def safe_relative_path(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 240:
        raise ContractError(f"{label} must be a non-empty relative path <= 240 characters")
    if "\\" in value or any(ord(ch) < 32 for ch in value):
        raise ContractError(f"{label} contains an unsafe character")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts or path.parts[0] in {"~", ""}:
        raise ContractError(f"{label} must stay within the artifact root")
    if ":" in path.parts[0]:
        raise ContractError(f"{label} must not contain a drive/scheme prefix")
    return value


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
