#!/usr/bin/env python3
"""Validate public project identity and optional source credential contracts.

Secret values never enter this module. Runtime health receives only the names of
explicitly configured secrets, allowing reports to expose required names and
configured/not-configured state without reading or printing credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

DEFAULT_CONFIG = Path("Data/config/public-project-configuration-v1.json")
DEFAULT_WORKFLOW_CONTRACT = Path("Data/workflows/catalog-workflow-contract-v1.json")
DEFAULT_SOURCE_ROOT = Path("Data/sources")
CREDENTIAL_POLICY_NAME = "credential-policy-v1.json"
PUBLIC_KEYS = {
    "PRODUCT_SUBMISSION_EMAIL",
    "OPEN_FOOD_FACTS_CONTACT_EMAIL",
    "OPEN_FOOD_FACTS_USER_AGENT",
}
SECRET_NAME = re.compile(r"^[A-Z][A-Z0-9_]{2,127}$")
SOURCE_KEY = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
EMAIL = re.compile(r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")
USER_AGENT = re.compile(r"^HalalFoodEU/[0-9]+\.[0-9]+ \(([^()]+)\)$")
AUTH_MODES = {"api-key", "oauth-client", "username-password", "sftp", "signing-key", "custom"}
RESERVED_SECRET_NAMES = {"GITHUB_TOKEN"}
HEALTH_KEY = "hfeu:configuration-health:owner-input:v1"


class ConfigurationError(ValueError):
    """Raised when public or credential configuration is unsupported or unsafe."""


@dataclass(frozen=True)
class CredentialPolicy:
    source_key: str
    mode: str
    required_secret_names: tuple[str, ...]
    path: Path


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"failed to read JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"{path} must contain a JSON object")
    return value


def _bounded_public_text(value: Any, field: str, *, max_length: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > max_length:
        raise ConfigurationError(f"{field} must be a non-blank bounded public string")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise ConfigurationError(f"{field} contains control characters")
    return value.strip()


def _email(value: Any, field: str) -> str:
    text = _bounded_public_text(value, field, max_length=254)
    if not EMAIL.fullmatch(text) or ".." in text or text.startswith(".") or text.endswith("."):
        raise ConfigurationError(f"{field} must be a valid public email address")
    return text


def validate_public_config(raw: dict[str, Any]) -> dict[str, str]:
    if set(raw) != {"schemaVersion", "publicValues"} or raw.get("schemaVersion") != 1:
        raise ConfigurationError("public project configuration has unsupported schema or fields")
    values = raw.get("publicValues")
    if not isinstance(values, dict) or set(values) != PUBLIC_KEYS:
        raise ConfigurationError("publicValues must define exactly the three reviewed public keys")
    submission = _email(values["PRODUCT_SUBMISSION_EMAIL"], "PRODUCT_SUBMISSION_EMAIL")
    contact = _email(values["OPEN_FOOD_FACTS_CONTACT_EMAIL"], "OPEN_FOOD_FACTS_CONTACT_EMAIL")
    agent = _bounded_public_text(values["OPEN_FOOD_FACTS_USER_AGENT"], "OPEN_FOOD_FACTS_USER_AGENT", max_length=200)
    match = USER_AGENT.fullmatch(agent)
    if match is None:
        raise ConfigurationError("OPEN_FOOD_FACTS_USER_AGENT must use 'HalalFoodEU/<major>.<minor> (<contact>)'")
    if match.group(1) != contact:
        raise ConfigurationError("OPEN_FOOD_FACTS_USER_AGENT contact must equal OPEN_FOOD_FACTS_CONTACT_EMAIL")
    return {
        "PRODUCT_SUBMISSION_EMAIL": submission,
        "OPEN_FOOD_FACTS_CONTACT_EMAIL": contact,
        "OPEN_FOOD_FACTS_USER_AGENT": agent,
    }


def load_public_values(path: Path = DEFAULT_CONFIG) -> dict[str, str]:
    return validate_public_config(load_json(path))


def validate_credential_policy(raw: dict[str, Any], path: Path) -> CredentialPolicy:
    if set(raw) != {"schemaVersion", "sourceKey", "authentication"} or raw.get("schemaVersion") != 1:
        raise ConfigurationError(f"{path} has unsupported credential policy schema or fields")
    source_key = raw.get("sourceKey")
    if not isinstance(source_key, str) or not SOURCE_KEY.fulmatch(source_key):
        raise ConfigurationError(f"{path} has invalid sourceKey")
    auth = raw.get("authentication")
    if not isinstance(auth, dict) or set(auth) != {"mode", "requiredSecretNames"}:
        raise ConfigurationError(f"{path} authentication must select exactly one mode and secret-name set")
    mode = auth.get("mode")
    if mode not in AUTH_MODES:
        raise ConfigurationError(f"{path} has unsupported authentication mode")
    names = auth.get("requiredSecretNames")
    if not isinstance(names, list) or not names:
        raise ConfigurationError(f"{path} requiredSecretNames must be a non-empty array")
    normalized: list[str] = []
    for name in names:
        if not isinstance(name, str) or not SECRET_NAME.fullmatch(name):
            raise ConfigurationError(f"{path} contains an invalid required secret name")
        if name in RESERVED_SECRET_NAMES:
            raise ConfigurationError(f"{path} must not redefine automatic GitHub token {name}")
        normalized.append(name)
    if len(normalized) != len(set(normalized)):
        raise ConfigurationError(f"{path} repeats a required secret name")
    return CredentialPolicy(source_key, mode, tuple(normalized), path)


def discover_credential_policies(source_root: Path = DEFAULT_SOURCE_ROOT) -> dict[str, CredentialPolicy]:
    if not source_root.exists():
        return {}
    result: dict[str, CredentialPolicy] = {}
    for path in sorted(source_root.glob(f"*/{CREDENTIAL_POLICY_NAME}")):
        policy = validate_credential_policy(load_json(path), path)
        expected_parent = policy.source_key
        if path.parent.name != expected_parent:
            raise ConfigurationError(f"{path} sourceKey must match its Data/sources/<sourceKey> directory")
        if policy.source_key in result:
            raise ConfigurationError(f"duplicate credential policy for {policy.source_key}")
        result[policy.source_key] = policy
    return result


def load_source_registry(path: Path = DEFAULT_WORKFLOW_CONTRACT) -> dict[str, dict[str, Any]]:
    raw = load_json(path)
    registry = raw.get("sourceRegistry")
    if not isinstance(registry, list):
        raise ConfigurationError("workflow contract sourceRegistry must be an array")
    result: dict[str, dict[str, Any]] = {}
    for item in registry:
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise ConfigurationError("workflow source registry contains an invalid entry")
        key = item["key"]
        if key in result:
            raise ConfigurationError(f"workflow source registry repeats {key}")
        if not isinstance(item.get("enabled"), bool) or not isinstance(item.get("credentialsRequired"), bool):
            raise ConfigurationError(f"workflow source {key} has invalid enabled/credentialsRequired flags")
        result[key] = item
    return result


def validate_contracts(
    *,
    config_path: Path = DEFAULT_CONFIG,
    workflow_contract_path: Path = DEFAULT_WORKFLOW_CONTRACT,
    source_root: Path = DEFAULT_SOURCE_ROOT,
) -> tuple[dict[str, str], dict[str, dict[str, Any]], dict[str, CredentialPolicy]]:
    values = load_public_values(config_path)
    registry = load_source_registry(workflow_contract_path)
    policies = discover_credential_policies(source_root)
    for source_key, policy in policies.items():
        source = registry.get(source_key)
        if source is None:
            raise ConfigurationError(f"credential policy {policy.path} targets an unregistered source")
        if not source["credentialsRequired"]:
            raise ConfigurationError(f"source {source_key} forbids credentials but has a credential policy")
    for source_key, source in registry.items():
        if source["enabled"] and source["credentialsRequired"] and source_key not in policies:
            raise ConfigurationError(f"enabled credential-bearing source {source_key} has no reviewed credential policy")
    return values, registry, policies


def evaluate_health(
    *,
    config_path: Path = DEFAULT_CONFIG,
    workflow_contract_path: Path = DEFAULT×ÕÓÔ’Ñ“Õ×ĞÓÓ•PÕˆÛİ\˜ÙWÜ›Ûİˆ]HQUSÔÓÕTÑWÔ“ÓÕˆÛÛ™šYİ\™YÜÙXÜ™]Û˜[Y\Îˆ]\˜X›VÜİ—HH

KŠHOˆXİÜİ‹[WN‚ˆË™YÚ\İKÛXÚY\ÈH˜[Y]WØÛÛ˜XİÊˆÛÛ™šY×Ü]XÛÛ™šY×Ü]ˆÛÜšÙ›İ×ØÛÛ˜XİÜ]]ÛÜšÙ›İ×ØÛÛ˜XİÜ]ˆÛİ\˜ÙWÜ›Ûİ\Ûİ\˜ÙWÜ›Ûİˆ
BˆXÛ\™YÛ˜[Y\ÈHÛ˜[YH›ÜˆÛXŞH[ˆÛXÚY\Ë˜[Y\Ê
H›Üˆ˜[YH[ˆÛXŞKœ™\]Z\™YÜÙXÜ™]Û˜[Y\ßBˆÛÛ™šYİ\™YHÙ]
ÛÛ™šYİ\™YÜÙXÜ™]Û˜[Y\ÊBˆ[šÛ›İÛˆHÛÜY
ÛÛ™šYİ\™YHXÛ\™YÛ˜[Y\ÊBˆYˆ[šÛ›İÛ‚ˆ˜Z\ÙHÛÛ™šYİ\˜][Û‘\œ›ÜŠˆ˜ÛÛ™šYİ\™YÙXÜ™][˜[YHİ]HÛÛZ[œÈ[™XÛ\™Y˜[Y\Îˆİ[šÛ›İÛŸHŠB‚ˆÛİ\˜Ù\Îˆ\İÙXİÜİ‹[WWHH×Bˆ›ØÚÙ\œÎˆ\İÙXİÜİ‹[WWHH×Bˆ›ÜˆÛİ\˜ÙWÚÙ^H[ˆÛÜY
™YÚ\İJN‚ˆÛİ\˜ÙHH™YÚ\İVÜÛİ\˜ÙWÚÙ^WBˆÛXŞHHÛXÚY\Ë™Ù]
Ûİ\˜ÙWÚÙ^JBˆYˆ›İÛİ\˜ÙVÈ™[˜X›Y—N‚ˆYˆÛXŞH\È›İ›Û™N‚ˆÛİ\˜Ù\Ë˜\[™
ÂˆœÛİ\˜ÙRÙ^HˆÛİ\˜ÙWÚÙ^Kˆœİ]Hˆ™\ØX›Y‹ˆ˜]][XØ][Û“[ÙHˆÛXŞK›[ÙKˆœ™\]Z\™YÙXÜ™]ÈˆÂˆÈ›˜[YHˆ˜[YK˜ÛÛ™šYİ\™Yˆ˜[YH[ˆÛÛ™šYİ\™YBˆ›Üˆ˜[YH[ˆÛXŞKœ™\]Z\™YÜÙXÜ™]Û˜[Y\ÂˆKˆJBˆÛÛ[YBˆYˆ›İÛİ\˜ÙVÈ˜Ü™Y[X[Ô™\]Z\™Y—N‚ˆÛÛ[YBˆ\ÜÙ\ÛXŞH\È›İ›Û™Bˆİ]\ÈHÂˆÈ›˜[YHˆ˜[YK˜ÛÛ™šYİ\™Yˆ˜[YH[ˆÛÛ™šYİ\™YBˆ›Üˆ˜[YH[ˆÛXŞKœ™\]Z\™YÜÙXÜ™]Û˜[Y\ÂˆBˆZ\ÜÚ[™ÈHÚ][VÈ›˜[YH—H›Üˆ][H[ˆİ]\ÈYˆ›İ][VÈ˜ÛÛ™šYİ\™Y—WBˆÛİ\˜Ù\Ë˜\[™
ÂˆœÛİ\˜ÙRÙ^HˆÛİ\˜ÙWÚÙ^Kˆœİ]Hˆ™[˜X›Y‹ˆ˜]][XØ][Û“[ÙHˆÛXŞK›[ÙKˆœ™\]Z\™YÙXÜ™]Èˆİ]\ËˆJBˆYˆZ\ÜÚ[™Î‚ˆ›ØÚÙ\œË˜\[™
ÂˆœÛİ\˜ÙRÙ^HˆÛİ\˜ÙWÚÙ^Kˆ˜ÛÙHˆœ™\]Z\™YXÜ™Y[X[Ë[›İXÛÛ™šYİ\™Y‹ˆœ™\]Z\™YÙXÜ™]˜[Y\Èˆ\İ
ÛXŞKœ™\]Z\™YÜÙXÜ™]Û˜[Y\ÊKˆ›Z\ÜÚ[™ÔÙXÜ™]˜[Y\ÈˆZ\ÜÚ[™ËˆJB‚ˆ™\ÜHÂˆœØÚ[XU™\œÚ[ÛˆˆKˆœİ]\Èˆ˜›ØÚÙYˆYˆ›ØÚÙ\œÈ[ÙHšX[H‹ˆ›İÛ™\’[œ]™\]Z\™Yˆ›ÛÛ
›ØÚÙ\œÊKˆ™Y\XØ][Û’Ù^HˆPSÒÑVKˆœX›XĞÛÛ™šYİ\˜][ÛˆˆÛ˜[YNˆ˜[Yˆ›Üˆ˜[YH[ˆÛÜY
P“P×ÒÑVTÊ_KˆœÛİ\˜Ù\ÈˆÛİ\˜Ù\Ëˆ˜›ØÚÙ\œÈˆ›ØÚÙ\œËˆBˆ™]\›ˆ™\Ü‚‚™YˆÜš]WÚœÛÛŠ]ˆ]˜[YNˆXİÜİ‹[WJHOˆ›Û™N‚ˆ]œ\™[›ZÙ\Š\™[ÏUYK^\İÛÚÏUYJBˆ]Üš]Wİ^
œÛÛ‹™[\Ê˜[YK[™[L‹ÛÜÚÙ^\ÏUYJH
È—ˆ‹[˜ÛÙ[™ÏH]‹NŠB‚‚™Yˆ\œÙWØ\™ÜÊ\™İˆ\İÜİ—H›Û™HH›Û™JHOˆ\™Ü\œÙK“˜[Y\ÜXÙN‚ˆ\œÙ\ˆH\™Ü\œÙK\™İ[Y[\œÙ\Š\ØÜš\[ÛW×ÙØ××ÊBˆ\œÙ\‹˜YØ\™İ[Y[
‹KXÛÛ™šYÈ‹\OT]Y˜][QQUSĞÓÓ‘’QÊBˆ\œÙ\‹˜YØ\™İ[Y[
‹K]ÛÜšÙ›İËXÛÛ˜Xİ‹\OT]Y˜][QQUSÕÓÔ’Ñ“Õ×ĞÓÓ•PÕ
Bˆ\œÙ\‹˜YØ\™İ[Y[
‹K\Ûİ\˜ÙK\›Ûİ‹\OT]Y˜][QQUSÔÓÕTÑWÔ“ÓÕ
BˆİXˆH\œÙ\‹˜YÜİXœ\œÙ\œÊ\İH˜ÛÛ[X[™‹™\]Z\™YUYJB‚ˆİX‹˜YÜ\œÙ\Š˜[Y]H‹[H˜[Y]HX›XÈ˜[Y\È[™Ûİ\˜ÙHÜ™Y[X[ÛÛ˜XİÈŠBˆÙ]HİX‹˜YÜ\œÙ\Š™Ù]‹[Hœš[Û™H™]šY]ÙYX›XÈ˜[YHŠBˆÙ]˜YØ\™İ[Y[
‹K[˜[YH‹ÚÚXÙ\Ï\ÛÜY
P“P×ÒÑVTÊK™\]Z\™YUYJBˆX[HİX‹˜YÜ\œÙ\ŠšX[‹[H™]˜[X]HÜ[Û˜[Ü™Y[X[ÛÛ™šYİ\˜][ÛˆÚ]İ]ÙXÜ™]˜[Y\ÈŠBˆX[˜YØ\™İ[Y[
‹KXÛÛ™šYİ\™Y\ÙXÜ™][˜[YH‹Xİ[ÛH˜\[™‹Y˜][V×JBˆX[˜YØ\™İ[Y[
‹K[İ]]‹\OT]™\]Z\™YUYJBˆ™]\›ˆ\œÙ\‹œ\œÙWØ\™ÜÊ\™İŠB‚‚™YˆXZ[Š\™İˆ\İÜİ—H›Û™HH›Û™JHOˆ[‚ˆ\™ÜÈH\œÙWØ\™ÜÊ\™İŠBˆN‚ˆ˜[Y\ËËÛXÚY\ÈH˜[Y]WØÛÛ˜XİÊˆÛÛ™šY×Ü]X\™ÜË˜ÛÛ™šYËˆÛÜšÙ›İ×ØÛÛ˜XİÜ]X\™ÜËÛÜšÙ›İ×ØÛÛ˜XİˆÛİ\˜ÙWÜ›ÛİX\™ÜËœÛİ\˜ÙWÜ›Ûİˆ
BˆYˆ\™ÜË˜ÛÛ[X[™OH˜[Y]H‚ˆš[
ˆ•˜[Y]YX›XÈ›Ú™XİÛÛ™šYİ\˜][Ûˆ[™Û[ŠÛXÚY\Ê_HÜ[Û˜[Ûİ\˜ÙHÜ™Y[X[ÛXÚY\ÈŠBˆ™]\›ˆˆYˆ\™ÜË˜ÛÛ[X[™OH™Ù]‚ˆš[
˜[Y\ÖØ\™ÜË›˜[YWJBˆ™]\›ˆˆ™\ÜH]˜[X]WÚX[
ˆÛÛ™šY×Ü]X\™ÜË˜ÛÛ™šYËˆÛÜšÙ›İ×ØÛÛ˜XİÜ]X\™ÜËÛÜšÙ›İ×ØÛÛ˜XİˆÛİ\˜ÙWÜ›ÛİX\™ÜËœÛİ\˜ÙWÜ›ÛİˆÛÛ™šYİ\™YÜÙXÜ™]Û˜[Y\ÏX\™ÜË˜ÛÛ™šYİ\™YÜÙXÜ™]Û˜[YKˆ
BˆÜš]WÚœÛÛŠ\™ÜË›İ]]™\Ü
Bˆš[
ˆÛÛ™šYİ\˜][ÛˆX[ˆÜ™\ÜÉÜİ]\É×_H
Û[Š™\ÜÉØ›ØÚÙ\œÉ×J_H›ØÚÙ\œÊHŠBˆ™]\›ˆˆYˆ™\ÜÈœİ]\È—HOHšX[Hˆ[ÙHˆ^Ù\ÛÛ™šYİ\˜][Û‘\œ›Üˆ\È^Î‚ˆš[
ˆœ›Ú™XİÛÛ™šYİ\˜][Ûˆ˜[Y][Ûˆ˜Z[YˆÙ^ßH‹š[O\Ş\Ëœİ\œŠBˆ™]\›ˆ‚‚‚šYˆ×Û˜[YW×ÈOH—×ÛXZ[—×È‚ˆ˜Z\ÙHŞ\İ[Q^]
XZ[Š
JB