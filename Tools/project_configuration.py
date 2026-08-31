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
    if not isinstance(source_key, str) or not SOURCE_KEY.fullmatch(source_key):
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
    workflow_contract_path: Path = DEFAULT_WORKFLOW_CONTRACT,
    source_root: Path = DEFAULT_SOURCE_ROOT,
    configured_secret_names: Iterable[str] = (),
) -> dict[str, Any]:
    _, registry, policies = validate_contracts(
        config_path=config_path,
        workflow_contract_path=workflow_contract_path,
        source_root=source_root,
    )
    declared_names = {name for policy in policies.values() for name in policy.required_secret_names}
    configured = set(configured_secret_names)
    unknown = sorted(configured - declared_names)
    if unknown:
        raise ConfigurationError(f"configured secret-name state contains undeclared names: {unknown}")

    sources: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    for source_key in sorted(registry):
        source = registry[source_key]
        policy = policies.get(source_key)
        if not source["enabled"]:
            if policy is not None:
                sources.append({
                    "sourceKey": source_key,
                    "state": "disabled",
                    "authenticationMode": policy.mode,
                    "requiredSecrets": [
                        {"name": name, "configured": name in configured}
                        for name in policy.required_secret_names
                    ],
                })
            continue
        if not source["credentialsRequired"]:
            continue
        assert policy is not None
        states = [
            {"name": name, "configured": name in configured}
            for name in policy.required_secret_names
        ]
        missing = [item["name"] for item in states if not item["configured"]]
        sources.append({
            "sourceKey": source_key,
            "state": "enabled",
            "authenticationMode": policy.mode,
            "requiredSecrets": states,
        })
        if missing:
            blockers.append({
                "sourceKey": source_key,
                "code": "required-credentials-not-configured",
                "requiredSecretNames": list(policy.required_secret_names),
                "missingSecretNames": missing,
            })

    report = {
        "schemaVersion": 1,
        "status": "blocked" if blockers else "healthy",
        "ownerInputRequired": bool(blockers),
        "deduplicationKey": HEALTH_KEY,
        "publicConfiguration": {name: "valid" for name in sorted(PUBLIC_KEYS)},
        "sources": sources,
        "blockers": blockers,
    }
    return report


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--workflow-contract", type=Path, default=DEFAULT_WORKFLOW_CONTRACT)
    parser.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="validate public values and source credential contracts")
    get = sub.add_parser("get", help="print one reviewed public value")
    get.add_argument("--name", choices=sorted(PUBLIC_KEYS), required=True)
    health = sub.add_parser("health", help="evaluate optional credential configuration without secret values")
    health.add_argument("--configured-secret-name", action="append", default=[])
    health.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        values, _, policies = validate_contracts(
            config_path=args.config,
            workflow_contract_path=args.workflow_contract,
            source_root=args.source_root,
        )
        if args.command == "validate":
            print(f"Validated public project configuration and {len(policies)} optional source credential policies")
            return 0
        if args.command == "get":
            print(values[args.name])
            return 0
        report = evaluate_health(
            config_path=args.config,
            workflow_contract_path=args.workflow_contract,
            source_root=args.source_root,
            configured_secret_names=args.configured_secret_name,
        )
        write_json(args.output, report)
        print(f"Configuration health: {report['status']} ({len(report['blockers'])} blockers)")
        return 2 if report["status"] != "healthy" else 0
    except ConfigurationError as exc:
        print(f"project configuration validation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
