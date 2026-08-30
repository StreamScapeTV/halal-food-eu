"""Shared policy, identity and validation primitives for the Open Prices adapter."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SOURCE_KEY = "open-prices"
SOURCE_OPERATOR = "Open Food Facts"
MARKET = "DE"
DEFAULT_SOURCE_POLICY = Path("Data/sources/open-prices/source-policy-v1.json")
DEFAULT_ALIAS_REGISTRY = Path("Data/sources/open-prices/retailer-aliases-v1.json")
DEFAULT_FIXTURES = {
    "locations": Path("Data/sources/open-prices/fixture-locations.jsonl"),
    "proofs": Path("Data/sources/open-prices/fixture-proofs.jsonl"),
    "prices": Path("Data/sources/open-prices/fixture-prices.jsonl"),
}
EXPORT_KINDS = ("locations", "proofs", "prices")
SAFE_RETAILER_KEY = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
SPACE = re.compile(r"\s+")
PUNCT = re.compile(r"[^a-z0-9äöüß]+")


class AdapterError(ValueError):
    """Raised when Open Prices configuration or source data is unsafe/unsupported."""


@dataclass(frozen=True)
class SourcePolicy:
    raw: dict[str, Any]
    export_urls: dict[str, str]
    allowed_hosts: tuple[str, ...]


@dataclass(frozen=True)
class RetailerAliases:
    raw: dict[str, Any]
    version: str
    stable: dict[str, frozenset[str]]
    names: dict[str, frozenset[str]]


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdapterError(f"failed to read JSON {path}: {exc}") from exc


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def derive_evidence_id(collection_kind: str, record: dict[str, Any]) -> str:
    value = copy.deepcopy(record)
    value.pop("id", None)
    digest = sha256_bytes(canonical_json(value).encode("utf-8"))
    return f"hfeu:{collection_kind}:sha256:{digest}"


def _exact_keys(value: dict[str, Any], required: set[str], label: str) -> None:
    missing = sorted(required - set(value))
    unknown = sorted(set(value) - required)
    if missing or unknown:
        raise AdapterError(f"{label} keys mismatch; missing={missing}, unknown={unknown}")


def _nonblank(value: Any, label: str, *, max_len: int = 4096) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_len:
        raise AdapterError(f"{label} must be a non-blank bounded string")
    if CONTROL.search(value):
        raise AdapterError(f"{label} contains control characters")
    return value.strip()


def load_source_policy(path: Path = DEFAULT_SOURCE_POLICY) -> SourcePolicy:
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise AdapterError("source policy must be an object")
    required = {
        "schemaVersion", "sourceKey", "operator", "sourceClass", "accessMethod",
        "exportURLs", "allowedAcquisitionHosts", "databaseLicense", "attribution",
        "evidenceKind", "proofBinaryPolicy", "completenessClaimAllowed", "documentation",
    }
    _exact_keys(raw, required, "source policy")
    if raw["schemaVersion"] != 1 or raw["sourceKey"] != SOURCE_KEY or raw["operator"] != SOURCE_OPERATOR:
        raise AdapterError("unsupported Open Prices source identity/version")
    if raw["sourceClass"] != "open-database" or raw["accessMethod"] != "public-bulk":
        raise AdapterError("Open Prices must remain an open-database public-bulk source")
    if raw["evidenceKind"] != "retailer-observation" or raw["completenessClaimAllowed"] is not False:
        raise AdapterError("Open Prices is observational only and must prohibit completeness claims")
    license_value = raw["databaseLicense"]
    if not isinstance(license_value, dict) or license_value.get("identifier") != "ODbL" or license_value.get("attributionRequired") is not True or license_value.get("shareAlikeRequired") is not True:
        raise AdapterError("ODbL attribution/share-alike obligations must be explicit")
    proof = raw["proofBinaryPolicy"]
    if proof != {"mode": "metadata-only", "downloadBinaries": False, "redistributeBinaries": False}:
        raise AdapterError("proof binaries must remain excluded from the adapter")
    hosts = raw["allowedAcquisitionHosts"]
    if not isinstance(hosts, list) or not hosts or len(hosts) != len(set(hosts)):
        raise AdapterError("allowedAcquisitionHosts must be a unique non-empty array")
    allowed: list[str] = []
    for index, host in enumerate(hosts):
        value = _nonblank(host, f"allowedAcquisitionHosts[{index}]", max_len=253).lower()
        if "/" in value or ":" in value or value.startswith(".") or value.endswith("."):
            raise AdapterError("allowedAcquisitionHosts entries must be hostname-only")
        allowed.append(value)
    exports = raw["exportURLs"]
    if not isinstance(exports, dict) or set(exports) != set(EXPORT_KINDS):
        raise AdapterError(f"exportURLs must define exactly {EXPORT_KINDS}")
    normalized: dict[str, str] = {}
    for kind in EXPORT_KINDS:
        url = _nonblank(exports[kind], f"exportURLs.{kind}")
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.query or parsed.fragment:
            raise AdapterError(f"exportURLs.{kind} must be an exact HTTPS URL on an admitted host")
        if not parsed.path.endswith(f"/{kind}.jsonl.gz"):
            raise AdapterError(f"exportURLs.{kind} has an unexpected path")
        normalized[kind] = url
    return SourcePolicy(raw=raw, export_urls=normalized, allowed_hosts=tuple(allowed))


def normalize_alias(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = SPACE.sub(" ", value.strip().casefold())
    value = PUNCT.sub(" ", value)
    value = SPACE.sub(" ", value).strip()
    return value or None


def load_alias_registry(path: Path = DEFAULT_ALIAS_REGISTRY) -> RetailerAliases:
    raw = load_json(path)
    if not isinstance(raw, dict):
        raise AdapterError("retailer alias registry must be an object")
    _exact_keys(raw, {"schemaVersion", "aliasVersion", "retailers"}, "retailer alias registry")
    if raw["schemaVersion"] != 1 or not isinstance(raw["aliasVersion"], str) or not SEMVER.fullmatch(raw["aliasVersion"]):
        raise AdapterError("unsupported retailer alias registry version")
    retailers = raw["retailers"]
    if not isinstance(retailers, list) or not retailers:
        raise AdapterError("retailers must be a non-empty array")
    stable: dict[str, set[str]] = {}
    names: dict[str, set[str]] = {}
    seen_keys: set[str] = set()
    for index, raw_retailer in enumerate(retailers):
        if not isinstance(raw_retailer, dict):
            raise AdapterError(f"retailers[{index}] must be an object")
        _exact_keys(raw_retailer, {"key", "displayName", "osmBrands", "osmTagValues", "names"}, f"retailers[{index}]")
        key = _nonblank(raw_retailer["key"], f"retailers[{index}].key", max_len=64)
        if not SAFE_RETAILER_KEY.fullmatch(key) or key in seen_keys:
            raise AdapterError(f"retailers[{index}].key is invalid or duplicate")
        seen_keys.add(key)
        _nonblank(raw_retailer["displayName"], f"retailers[{index}].displayName", max_len=120)
        for field, target in (("osmBrands", stable), ("osmTagValues", stable), ("names", names)):
            values = raw_retailer[field]
            if not isinstance(values, list) or not values:
                raise AdapterError(f"retailers[{index}].{field} must be non-empty")
            for item in values:
                alias = normalize_alias(item)
                if not alias:
                    raise AdapterError(f"retailers[{index}].{field} contains invalid alias")
                target.setdefault(alias, set()).add(key)
    return RetailerAliases(
        raw=raw,
        version=raw["aliasVersion"],
        stable={key: frozenset(value) for key, value in stable.items()},
        names={key: frozenset(value) for key, value in names.items()},
    )


def match_retailer(location: dict[str, Any], aliases: RetailerAliases) -> tuple[str | None, str, str | None]:
    stable_values = [location.get("osm_brand"), location.get("osm_tag_value")]
    candidates: set[str] = set()
    matched_alias: str | None = None
    for raw in stable_values:
        alias = normalize_alias(raw)
        if not alias:
            continue
        keys = aliases.stable.get(alias, frozenset())
        if keys:
            candidates.update(keys)
            matched_alias = alias
    if len(candidates) == 1:
        return next(iter(candidates)), "stable", matched_alias
    if len(candidates) > 1:
        return None, "ambiguous", matched_alias
    name = normalize_alias(location.get("osm_name"))
    if name:
        name_candidates = aliases.names.get(name, frozenset())
        if len(name_candidates) == 1:
            return next(iter(name_candidates)), "name", name
        if len(name_candidates) > 1:
            return None, "ambiguous", name
    return None, "unmatched", name or normalize_alias(location.get("osm_brand")) or normalize_alias(location.get("osm_tag_value"))


def canonical_gtin(value: Any) -> str:
    if not isinstance(value, str):
        raise AdapterError("product_code must be a string")
    text = value.strip()
    if len(text) not in {8, 12, 13, 14} or not text.isascii() or not text.isdigit():
        raise AdapterError("product_code must be a GTIN-8/12/13/14 digit string")
    total = 0
    for offset, character in enumerate(reversed(text[:-1])):
        total += int(character) * (3 if offset % 2 == 0 else 1)
    expected = (10 - total % 10) % 10
    if expected != int(text[-1]):
        raise AdapterError("product_code has an invalid GTIN check digit")
    return text.zfill(14)


def parse_retrieved_at(value: str | None) -> str:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    if not isinstance(value, str) or not value.endswith("Z"):
        raise AdapterError("retrievedAt must be an RFC3339 UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise AdapterError("retrievedAt is invalid") from exc
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def observation_timestamp(value: Any, retrieved_at: str) -> str:
    if not isinstance(value, str):
        raise AdapterError("price.date must be an ISO date")
    try:
        observed = date.fromisoformat(value)
    except ValueError as exc:
        raise AdapterError("price.date must be YYYY-MM-DD") from exc
    retrieved = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00")).date()
    if observed > retrieved:
        raise AdapterError("price.date cannot be in the future relative to acquisition")
    return observed.isoformat() + "T00:00:00Z"


def require_int(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise AdapterError(f"{label} must be a non-negative integer")
    return value


def safe_text(value: Any, *, max_len: int = 160) -> str | None:
    if not isinstance(value, str):
        return None
    text = SPACE.sub(" ", CONTROL.sub("", value)).strip()
    return text[:max_len] if text else None


def source_revision(price: dict[str, Any], proof: dict[str, Any]) -> str:
    price_revision = safe_text(price.get("updated") or price.get("created") or price.get("date"), max_len=80) or "unknown"
    proof_revision = safe_text(proof.get("updated") or proof.get("created") or proof.get("date"), max_len=80) or "unknown"
    proof_type = safe_text(proof.get("type"), max_len=32) or "UNKNOWN"
    return f"price:{price_revision};proof:{proof_revision};type:{proof_type}"[:255]


def evidence_envelope(source: dict[str, Any], retailer_records: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "assessments": [],
        "certifications": [],
        "currentSelections": [],
        "identities": [],
        "ingredients": [],
        "packageEvidence": [],
        "releases": [],
        "remoteImages": [],
        "retailerEvidence": retailer_records,
        "reviews": [],
        "schemaVersion": 1,
        "sources": [source],
        "validityEvents": [],
    }
