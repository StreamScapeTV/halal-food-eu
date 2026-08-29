"""Versioned source/stage/artifact contract for the catalog workflow."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from catalog_workflow_common import (
    ALLOWED_ACCESS_METHODS,
    ALLOWED_SOURCE_CLASSES,
    ARTIFACT_CLASSES,
    CONTRACT_SCHEMA_VERSION,
    ContractError,
    SAFE_KEY,
    SAFE_SNAPSHOT,
    SEMVER,
    exact_keys,
    load_json,
    positive_int,
    require_object,
)


@dataclass(frozen=True)
class SourceRegistration:
    key: str
    enabled: bool
    source_class: str
    access_method: str
    credentials_required: bool
    redistribution_class: str
    allowed_hosts: tuple[str, ...]
    adapter_version: str


@dataclass(frozen=True)
class WorkflowContract:
    raw: dict[str, Any]
    stage_order: tuple[str, ...]
    stages: dict[str, dict[str, Any]]
    artifacts: dict[str, dict[str, Any]]
    sources: dict[str, SourceRegistration]
    allowed_modes: tuple[str, ...]
    max_retries: int
    initial_retry_seconds: int
    max_retry_seconds: int

    @classmethod
    def load(cls, path: Path) -> "WorkflowContract":
        raw = require_object(load_json(path), "workflow contract")
        exact_keys(
            raw,
            required={"schemaVersion", "contractVersion", "defaultMarket", "stages", "artifactKinds", "sourceRegistry", "execution"},
            optional=set(),
            label="workflow contract",
        )
        if raw["schemaVersion"] != CONTRACT_SCHEMA_VERSION:
            raise ContractError(f"unsupported workflow contract schemaVersion {raw['schemaVersion']!r}")
        if not isinstance(raw["contractVersion"], str) or not SEMVER.fullmatch(raw["contractVersion"]):
            raise ContractError("contractVersion must be semantic versioning")
        if raw["defaultMarket"] != "DE":
            raise ContractError("v1 defaultMarket must be DE")

        stages_raw = raw["stages"]
        if not isinstance(stages_raw, list) or not stages_raw:
            raise ContractError("stages must be a non-empty array")
        stages: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        prior_produced: set[str] = set()
        for index, value in enumerate(stages_raw):
            stage = require_object(value, f"stages[{index}]")
            exact_keys(
                stage,
                required={"key", "accepts", "produces", "requiresCompleteInput", "mayWriteRepository"},
                optional=set(),
                label=f"stages[{index}]",
            )
            key = stage["key"]
            if not isinstance(key, str) or not SAFE_KEY.fullmatch(key):
                raise ContractError(f"stages[{index}].key is invalid")
            if key in stages:
                raise ContractError(f"duplicate stage key {key}")
            for field in ("accepts", "produces"):
                values = stage[field]
                if not isinstance(values, list) or not all(isinstance(item, str) and SAFE_KEY.fullmatch(item) for item in values):
                    raise ContractError(f"stages[{index}].{field} must contain safe artifact keys")
                if len(set(values)) != len(values):
                    raise ContractError(f"stages[{index}].{field} contains duplicates")
            if index and any(item not in prior_produced for item in stage["accepts"]):
                unknown = sorted(item for item in stage["accepts"] if item not in prior_produced)
                raise ContractError(f"stage {key} accepts artifacts not produced by an earlier stage: {unknown}")
            if not isinstance(stage["requiresCompleteInput"], bool) or not isinstance(stage["mayWriteRepository"], bool):
                raise ContractError(f"stage {key} boolean flags are invalid")
            stages[key] = stage
            order.append(key)
            prior_produced.update(stage["produces"])

        artifacts_raw = require_object(raw["artifactKinds"], "artifactKinds")
        artifacts: dict[str, dict[str, Any]] = {}
        referenced = {item for stage in stages_raw for field in ("accepts", "produces") for item in stage[field]}
        if set(artifacts_raw) != referenced:
            missing = sorted(referenced - set(artifacts_raw))
            unused = sorted(set(artifacts_raw) - referenced)
            raise ContractError(f"artifactKinds mismatch; missing={missing}, unused={unused}")
        for key, value in artifacts_raw.items():
            if not SAFE_KEY.fullmatch(key):
                raise ContractError(f"invalid artifact kind {key}")
            artifact = require_object(value, f"artifactKinds.{key}")
            exact_keys(
                artifact,
                required={"maxBytes", "maxRecords", "allowedRedistributionClasses"},
                optional=set(),
                label=f"artifactKinds.{key}",
            )
            positive_int(artifact["maxBytes"], f"artifactKinds.{key}.maxBytes")
            positive_int(artifact["maxRecords"], f"artifactKinds.{key}.maxRecords")
            classes = artifact["allowedRedistributionClasses"]
            if not isinstance(classes, list) or not classes or set(classes) - ARTIFACT_CLASSES:
                raise ContractError(f"artifactKinds.{key}.allowedRedistributionClasses is invalid")
            if len(classes) != len(set(classes)):
                raise ContractError(f"artifactKinds.{key}.allowedRedistributionClasses contains duplicates")
            artifacts[key] = artifact

        sources_raw = raw["sourceRegistry"]
        if not isinstance(sources_raw, list) or not sources_raw:
            raise ContractError("sourceRegistry must contain at least the synthetic fixture source")
        sources: dict[str, SourceRegistration] = {}
        for index, value in enumerate(sources_raw):
            source = require_object(value, f"sourceRegistry[{index}]")
            exact_keys(
                source,
                required={"key", "enabled", "sourceClass", "accessMethod", "credentialsRequired", "redistributionClass", "allowedHosts", "adapterVersion"},
                optional=set(),
                label=f"sourceRegistry[{index}]",
            )
            key = source["key"]
            if not isinstance(key, str) or not SAFE_KEY.fullmatch(key) or key in sources:
                raise ContractError(f"sourceRegistry[{index}].key is invalid or duplicate")
            if not isinstance(source["enabled"], bool) or not isinstance(source["credentialsRequired"], bool):
                raise ContractError(f"sourceRegistry[{index}] boolean field is invalid")
            if source["sourceClass"] not in ALLOWED_SOURCE_CLASSES:
                raise ContractError(f"sourceRegistry[{index}].sourceClass is unsupported")
            if source["accessMethod"] not in ALLOWED_ACCESS_METHODS:
                raise ContractError(f"sourceRegistry[{index}].accessMethod is unsupported")
            if source["redistributionClass"] not in ARTIFACT_CLASSES:
                raise ContractError(f"sourceRegistry[{index}].redistributionClass is invalid")
            hosts = source["allowedHosts"]
            if not isinstance(hosts, list) or not all(isinstance(host, str) and host and "/" not in host and ":" not in host for host in hosts):
                raise ContractError(f"sourceRegistry[{index}].allowedHosts must contain hostnames only")
            if len(hosts) != len(set(hosts)):
                raise ContractError(f"sourceRegistry[{index}].allowedHosts contains duplicates")
            if not isinstance(source["adapterVersion"], str) or not SEMVER.fullmatch(source["adapterVersion"]):
                raise ContractError(f"sourceRegistry[{index}].adapterVersion must be semantic versioning")
            sources[key] = SourceRegistration(
                key=key,
                enabled=source["enabled"],
                source_class=source["sourceClass"],
                access_method=source["accessMethod"],
                credentials_required=source["credentialsRequired"],
                redistribution_class=source["redistributionClass"],
                allowed_hosts=tuple(hosts),
                adapter_version=source["adapterVersion"],
            )

        execution = require_object(raw["execution"], "execution")
        exact_keys(
            execution,
            required={"allowedModes", "maxRetries", "initialRetrySeconds", "maxRetrySeconds", "artifactRetentionDays", "generatedBranchPrefix", "healthIssuePrefix"},
            optional=set(),
            label="execution",
        )
        modes = execution["allowedModes"]
        if not isinstance(modes, list) or not modes or not all(isinstance(mode, str) and SAFE_KEY.fullmatch(mode) for mode in modes):
            raise ContractError("execution.allowedModes is invalid")
        if len(modes) != len(set(modes)):
            raise ContractError("execution.allowedModes contains duplicates")
        max_retries = positive_int(execution["maxRetries"], "execution.maxRetries", allow_zero=True)
        initial_retry_seconds = positive_int(execution["initialRetrySeconds"], "execution.initialRetrySeconds")
        max_retry_seconds = positive_int(execution["maxRetrySeconds"], "execution.maxRetrySeconds")
        if initial_retry_seconds > max_retry_seconds:
            raise ContractError("initialRetrySeconds must not exceed maxRetrySeconds")
        retention = require_object(execution["artifactRetentionDays"], "execution.artifactRetentionDays")
        if set(retention) != ARTIFACT_CLASSES:
            raise ContractError("artifactRetentionDays must define every redistribution class")
        for key, value in retention.items():
            days = positive_int(value, f"artifactRetentionDays.{key}")
            if days > 90:
                raise ContractError(f"artifactRetentionDays.{key} must be <= 90")
        if execution["generatedBranchPrefix"] != "catalog-update/":
            raise ContractError("generatedBranchPrefix must be catalog-update/")
        if execution["healthIssuePrefix"] != "[Catalog Health]":
            raise ContractError("healthIssuePrefix must be [Catalog Health]")

        return cls(
            raw=raw,
            stage_order=tuple(order),
            stages=stages,
            artifacts=artifacts,
            sources=sources,
            allowed_modes=tuple(modes),
            max_retries=max_retries,
            initial_retry_seconds=initial_retry_seconds,
            max_retry_seconds=max_retry_seconds,
        )

    def validate_source(self, source_key: str, snapshot_id: str, mode: str) -> SourceRegistration:
        if not SAFE_KEY.fullmatch(source_key):
            raise ContractError("source key is not a safe bounded identifier")
        if not SAFE_SNAPSHOT.fullmatch(snapshot_id):
            raise ContractError("snapshot ID is not a safe bounded identifier")
        if mode not in self.allowed_modes:
            raise ContractError(f"unsupported acquisition mode {mode!r}")
        source = self.sources.get(source_key)
        if source is None:
            raise ContractError(f"source {source_key!r} is not registered")
        if not source.enabled:
            raise ContractError(f"source {source_key!r} is disabled")
        if source.source_class == "fixture" and mode != "fixture":
            raise ContractError("synthetic fixture sources may run only in fixture mode")
        return source

    def retry_delays(self) -> tuple[int, ...]:
        values: list[int] = []
        delay = self.initial_retry_seconds
        for _ in range(self.max_retries):
            values.append(min(delay, self.max_retry_seconds))
            delay = min(delay * 2, self.max_retry_seconds)
        return tuple(values)
