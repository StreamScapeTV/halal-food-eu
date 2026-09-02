#!/usr/bin/env python3
"""Fail-closed certifier admission and exact certificate eligibility helpers.

This module is deliberately standard-library-only and local-file-only. It validates
reviewed registry data; it never decides that a new real certifier is acceptable.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from evidence_model_core import derive_id

DEFAULT_REGISTRY = Path("Data/certifiers/certifier-registry-v1.json")
KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,79}$")
SOURCE_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,79}$")
CREDENTIAL_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,99}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MARKET_RE = re.compile(r"^[A-Z]{2}$")
REGISTRY_STATES = {"accepted", "review-required", "blocked", "revoked"}
EXACT_MATCH_KINDS = {"exact-gtin", "explicit-product-list", "exact-batch"}


class RegistryError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{field} must be a non-blank RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RegistryError(f"{field} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise RegistryError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def _nonblank(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistryError(f"{field} must be a non-blank string")
    return value.strip()


def load_registry(path: Path = DEFAULT_REGISTRY) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistryError(f"failed to read registry {path}: {exc}") from exc
    validate_registry(value)
    return value


def validate_registry(registry: Any, *, now: datetime | None = None) -> dict[str, Any]:
    if not isinstance(registry, dict):
        raise RegistryError("registry must be an object")
    expected = {"schemaVersion", "registryVersion", "defaultDecision", "reviewedAt", "entries"}
    if set(registry) != expected or registry.get("schemaVersion") != 1:
        raise RegistryError("registry has unsupported schema or fields")
    version = _nonblank(registry.get("registryVersion"), "registryVersion")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
        raise RegistryError("registryVersion must be semantic numeric version text")
    if registry.get("defaultDecision") != "review-required":
        raise RegistryError("unknown certifiers/schemes must fail closed to review-required")
    reviewed = _timestamp(registry.get("reviewedAt"), "reviewedAt")
    now = now or datetime.now(timezone.utc)
    if reviewed > now.astimezone(timezone.utc) + timedelta(minutes=5):
        raise RegistryError("registry reviewedAt may not be future-dated")
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise RegistryError("entries must be an array")

    seen_keys: set[tuple[str, str]] = set()
    seen_identifiers: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        path = f"entries[{index}]"
        required = {
            "certifierKey", "schemeKey", "certifier", "scheme", "legalName", "displayName",
            "markets", "state", "officialReference", "allowedMatchKinds", "maxRecheckAgeDays",
            "sourceApprovals", "reviewerID", "reviewedAt", "nextReviewAt", "limitations",
            "allowedAppWording",
        }
        optional = {"standardReferences", "recognitionReferences"}
        if not isinstance(entry, dict) or set(entry) - required - optional or required - set(entry):
            raise RegistryError(f"{path} fields mismatch")
        certifier_key = _nonblank(entry["certifierKey"], f"{path}.certifierKey")
        scheme_key = _nonblank(entry["schemeKey"], f"{path}.schemeKey")
        if not KEY_RE.fullmatch(certifier_key) or not KEY_RE.fullmatch(scheme_key):
            raise RegistryError(f"{path} stable keys are invalid")
        stable = (certifier_key, scheme_key)
        if stable in seen_keys:
            raise RegistryError(f"{path} duplicates certifier/scheme stable keys")
        seen_keys.add(stable)

        certifier = _nonblank(entry["certifier"], f"{path}.certifier")
        scheme = _nonblank(entry["scheme"], f"{path}.scheme")
        identity = (certifier, scheme)
        if identity in seen_identifiers:
            raise RegistryError(f"{path} duplicates evidence certifier/scheme identifiers")
        seen_identifiers.add(identity)
        for field in ("legalName", "displayName", "limitations", "allowedAppWording", "reviewerID"):
            _nonblank(entry[field], f"{path}.{field}")
        reference = _nonblank(entry["officialReference"], f"{path}.officialReference")
        if not reference.startswith("https://"):
            raise RegistryError(f"{path}.officialReference must be HTTPS")
        if entry["state"] not in REGISTRY_STATES:
            raise RegistryError(f"{path}.state unsupported")

        markets = entry["markets"]
        if not isinstance(markets, list) or not markets or len(markets) != len(set(markets)):
            raise RegistryError(f"{path}.markets must be a non-empty unique array")
        if any(not isinstance(item, str) or not MARKET_RE.fullmatch(item) for item in markets):
            raise RegistryError(f"{path}.markets contains an invalid market")

        kinds = entry["allowedMatchKinds"]
        if not isinstance(kinds, list) or not kinds or len(kinds) != len(set(kinds)):
            raise RegistryError(f"{path}.allowedMatchKinds must be a non-empty unique array")
        if any(item not in EXACT_MATCH_KINDS for item in kinds):
            raise RegistryError(f"{path}.allowedMatchKinds contains non-exact match semantics")
        age = entry["maxRecheckAgeDays"]
        if not isinstance(age, int) or isinstance(age, bool) or not 1 <= age <= 3660:
            raise RegistryError(f"{path}.maxRecheckAgeDays out of range")

        entry_reviewed = _timestamp(entry["reviewedAt"], f"{path}.reviewedAt")
        next_review = _timestamp(entry["nextReviewAt"], f"{path}.nextReviewAt")
        if next_review <= entry_reviewed:
            raise RegistryError(f"{path}.nextReviewAt must follow reviewedAt")
        if entry_reviewed > reviewed:
            raise RegistryError(f"{path}.reviewedAt cannot be later than registry reviewedAt")

        approvals = entry["sourceApprovals"]
        if not isinstance(approvals, list) or not approvals:
            raise RegistryError(f"{path}.sourceApprovals must be non-empty")
        seen_sources: set[str] = set()
        for approval_index, approval in enumerate(approvals):
            apath = f"{path}.sourceApprovals[{approval_index}]"
            if not isinstance(approval, dict) or set(approval) != {"sourceKey", "automated", "approvalReference", "credentialNames"}:
                raise RegistryError(f"{apath} fields mismatch")
            source_key = _nonblank(approval["sourceKey"], f"{apath}.sourceKey")
            if not SOURCE_KEY_RE.fullmatch(source_key) or source_key in seen_sources:
                raise RegistryError(f"{apath}.sourceKey invalid or duplicated")
            seen_sources.add(source_key)
            if not isinstance(approval["automated"], bool):
                raise RegistryError(f"{apath}.automated must be boolean")
            approval_reference = _nonblank(approval["approvalReference"], f"{apath}.approvalReference")
            if approval["automated"] and not approval_reference.startswith("source-policy:"):
                raise RegistryError(f"{apath}.approvalReference must identify a separate source-policy approval")
            credentials = approval["credentialNames"]
            if not isinstance(credentials, list) or len(credentials) != len(set(credentials)):
                raise RegistryError(f"{apath}.credentialNames must be a unique array")
            if any(not isinstance(item, str) or not CREDENTIAL_RE.fullmatch(item) for item in credentials):
                raise RegistryError(f"{apath}.credentialNames contains an invalid configuration name")

        for refs_field in ("standardReferences", "recognitionReferences"):
            refs = entry.get(refs_field, [])
            if not isinstance(refs, list) or len(refs) != len(set(refs)) or any(not isinstance(item, str) or not item.strip() for item in refs):
                raise RegistryError(f"{path}.{refs_field} must be unique non-blank strings")
    return registry


def registry_entry(
    registry: dict[str, Any], certification: dict[str, Any], *, at: datetime
) -> tuple[dict[str, Any] | None, list[str]]:
    validate_registry(registry)
    certifier, scheme = certification.get("certifier"), certification.get("scheme")
    matches = [
        entry for entry in registry["entries"]
        if entry["certifier"] == certifier and entry["scheme"] == scheme
    ]
    if not matches:
        return None, ["certifier-scheme-not-registered"]
    entry = matches[0]
    reasons: list[str] = []
    if entry["state"] != "accepted":
        reasons.append(f"certifier-state-{entry['state']}")
    reviewed_at = _timestamp(entry["reviewedAt"], "registryEntry.reviewedAt")
    next_review = _timestamp(entry["nextReviewAt"], "registryEntry.nextReviewAt")
    if at < reviewed_at:
        reasons.append("certifier-review-not-yet-effective")
    if at >= next_review:
        reasons.append("certifier-review-expired")
    return entry, reasons


def _binding_reasons(
    certification: dict[str, Any],
    *, ingredient_observation_id: str, binding: dict[str, Any] | None,
) -> list[str]:
    if not isinstance(binding, dict):
        return ["certificate-formulation-binding-missing"]
    expected = {"certificationID", "ingredientObservationID", "matchBasis"}
    if set(binding) != expected:
        return ["certificate-formulation-binding-invalid"]
    reasons: list[str] = []
    if binding.get("certificationID") != certification.get("id"):
        reasons.append("certificate-binding-id-mismatch")
    if binding.get("ingredientObservationID") != ingredient_observation_id:
        reasons.append("certificate-formulation-mismatch")
    if binding.get("matchBasis") != certification.get("matchBasis"):
        reasons.append("certificate-binding-match-basis-mismatch")
    return reasons


def certificate_eligibility(
    certification: dict[str, Any],
    registry: dict[str, Any],
    *,
    gtin: str,
    market: str,
    ingredient_observation_id: str,
    at: datetime,
    binding: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return deterministic fail-closed eligibility without creating a halal ruling."""
    entry, reasons = registry_entry(registry, certification, at=at)
    if entry is None:
        return {"eligible": False, "reasons": reasons, "registryEntry": None, "derivedStatus": "unknown"}

    if certification.get("gtin") != gtin:
        reasons.append("certificate-gtin-mismatch")
    if certification.get("market") != market or market not in entry["markets"]:
        reasons.append("certificate-market-mismatch")

    source_key = certification.get("sourceKey")
    approved_sources = {item["sourceKey"] for item in entry["sourceApprovals"]}
    if source_key not in approved_sources:
        reasons.append("certificate-source-not-approved")

    match_basis = certification.get("matchBasis")
    if match_basis not in EXACT_MATCH_KINDS or match_basis not in entry["allowedMatchKinds"]:
        reasons.append("certificate-match-not-exact")
    reasons.extend(_binding_reasons(
        certification,
        ingredient_observation_id=ingredient_observation_id,
        binding=binding,
    ))

    for field in ("certificateReference", "scope", "sourceRecordID", "lastCheckedAt"):
        if not isinstance(certification.get(field), str) or not certification[field].strip():
            reasons.append(f"certificate-{field}-missing")
    evidence_hash = certification.get("evidenceHash")
    if not isinstance(evidence_hash, str) or not SHA256_RE.fullmatch(evidence_hash):
        reasons.append("certificate-evidence-hash-missing")

    derived_status = "active"
    effective = certification.get("effectiveAt")
    expiry = certification.get("expiryAt")
    revoked = certification.get("revokedAt")
    suspended = certification.get("suspendedAt")
    if effective is not None and _timestamp(effective, "certification.effectiveAt") > at:
        derived_status = "not-yet-effective"
        reasons.append("certificate-not-yet-effective")
    if expiry is not None and at >= _timestamp(expiry, "certification.expiryAt"):
        derived_status = "expired"
        reasons.append("certificate-expired")
    if revoked is not None and _timestamp(revoked, "certification.revokedAt") <= at:
        derived_status = "revoked"
        reasons.append("certificate-revoked")
    if suspended is not None and _timestamp(suspended, "certification.suspendedAt") <= at:
        derived_status = "suspended"
        reasons.append("certificate-suspended")

    checked = certification.get("lastCheckedAt")
    if isinstance(checked, str) and checked.strip():
        checked_at = _timestamp(checked, "certification.lastCheckedAt")
        if checked_at > at:
            reasons.append("certificate-last-check-future")
        elif at - checked_at > timedelta(days=entry["maxRecheckAgeDays"]):
            reasons.append("certificate-recheck-stale")

    return {
        "eligible": not reasons,
        "reasons": sorted(set(reasons)),
        "registryEntry": {
            "certifierKey": entry["certifierKey"],
            "schemeKey": entry["schemeKey"],
            "registryVersion": registry["registryVersion"],
        },
        "derivedStatus": derived_status,
    }


def complete_review_with_registry(
    *,
    report: dict[str, Any],
    methodology: dict[str, Any],
    review_input: dict[str, Any],
    certifications: list[dict[str, Any]],
    registry: dict[str, Any],
) -> dict[str, Any]:
    """Gate certified reviews through the canonical registry before core review materialization."""
    from halal_methodology_core import MethodologyError, complete_review

    if review_input.get("decision") != "halal-certified":
        return complete_review(
            report=report,
            methodology=methodology,
            review_input=review_input,
            certifications=certifications,
        )
    validate_registry(registry)
    ingredient_id = report.get("ingredientObservationID")
    if not isinstance(ingredient_id, str) or not ingredient_id:
        raise MethodologyError("halal-certified requires exact current formulation evidence")
    reviewed_at = _timestamp(review_input.get("reviewedAt"), "reviewedAt")
    eligible: list[dict[str, Any]] = []
    bindings: list[dict[str, str]] = []
    entries: list[dict[str, Any]] = []
    rejected_reasons: set[str] = set()
    for certification in certifications:
        if not isinstance(certification, dict) or not isinstance(certification.get("id"), str):
            continue
        binding = {
            "certificationID": certification["id"],
            "ingredientObservationID": ingredient_id,
            "matchBasis": str(certification.get("matchBasis", "")),
        }
        result = certificate_eligibility(
            certification,
            registry,
            gtin=str(report.get("gtin", "")),
            market=str(report.get("market", "")),
            ingredient_observation_id=ingredient_id,
            at=reviewed_at,
            binding=binding,
        )
        if result["eligible"]:
            eligible.append(certification)
            bindings.append(binding)
            entry = next(
                item for item in registry["entries"]
                if item["certifierKey"] == result["registryEntry"]["certifierKey"]
                and item["schemeKey"] == result["registryEntry"]["schemeKey"]
            )
            entries.append(entry)
        else:
            rejected_reasons.update(result["reasons"])
    if not eligible:
        suffix = f": {', '.join(sorted(rejected_reasons))}" if rejected_reasons else ""
        raise MethodologyError(
            "halal-certified requires current exact-scope certification from the accepted registry" + suffix
        )

    transient_methodology = copy.deepcopy(methodology)
    transient_methodology["certificationPolicy"]["acceptedCertifiers"] = [
        {
            "certifier": entry["certifier"],
            "scheme": entry["scheme"],
            "markets": list(entry["markets"]),
            "reviewedAt": entry["reviewedAt"],
            "expiresAt": entry["nextReviewAt"],
        }
        for entry in entries
    ]
    result = complete_review(
        report=report,
        methodology=transient_methodology,
        review_input=review_input,
        certifications=eligible,
    )
    artifact = result["reviewArtifact"]
    artifact.pop("reviewArtifactSha256", None)
    artifact["certifierRegistryVersion"] = registry["registryVersion"]
    artifact["certifierRegistrySha256"] = digest(registry)
    artifact["certificationBindings"] = sorted(bindings, key=lambda item: item["certificationID"])
    artifact["reviewArtifactSha256"] = digest(artifact)
    return result


def certification_status_report(
    *, envelope: dict[str, Any], registry: dict[str, Any], evaluated_at: str
) -> dict[str, Any]:
    """Audit current certified assessments and report deterministic invalidations."""
    validate_registry(registry)
    at = _timestamp(evaluated_at, "evaluatedAt")
    certifications = {
        item["id"]: item for item in envelope.get("certifications", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    assessments = {
        item["id"]: item for item in envelope.get("assessments", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    decisions: list[dict[str, Any]] = []
    for selection in sorted(
        [item for item in envelope.get("currentSelections", []) if isinstance(item, dict)],
        key=lambda item: (str(item.get("gtin", "")), str(item.get("market", ""))),
    ):
        assessment_id = selection.get("assessmentID")
        assessment = assessments.get(assessment_id) if isinstance(assessment_id, str) else None
        if assessment is None or assessment.get("status") != "halal-certified":
            continue
        ingredient_id = selection.get("ingredientObservationID")
        cert_ids = [item for item in assessment.get("certificationIDs", []) if isinstance(item, str)]
        results: list[dict[str, Any]] = []
        for cert_id in cert_ids:
            cert = certifications.get(cert_id)
            if cert is None:
                results.append({"certificateID": cert_id, "eligible": False, "reasons": ["certificate-record-missing"]})
                continue
            binding = {
                "certificationID": cert_id,
                "ingredientObservationID": assessment.get("ingredientObservationID"),
                "matchBasis": cert.get("matchBasis"),
            }
            result = certificate_eligibility(
                cert,
                registry,
                gtin=str(selection.get("gtin", "")),
                market=str(selection.get("market", "")),
                ingredient_observation_id=str(ingredient_id or ""),
                at=at,
                binding=binding,
            )
            results.append({"certificateID": cert_id, "eligible": result["eligible"], "reasons": result["reasons"]})
        eligible_ids = sorted(item["certificateID"] for item in results if item["eligible"])
        certificate_reasons = sorted({reason for item in results for reason in item["reasons"]})
        blocking_reasons: list[str] = []
        if sorted(cert_ids) != sorted(selection.get("certificationIDs", [])):
            blocking_reasons.append("certification-selection-changed")
        if selection.get("conflictFlags"):
            blocking_reasons.append("current-formulation-conflict")
        if not eligible_ids:
            blocking_reasons.extend(certificate_reasons)
            if not certificate_reasons:
                blocking_reasons.append("no-eligible-certification")
        action = "carry-forward" if eligible_ids and not blocking_reasons else "invalidate"
        decisions.append({
            "gtin": selection.get("gtin"),
            "market": selection.get("market"),
            "assessmentID": assessment_id,
            "action": action,
            "eligibleCertificationIDs": eligible_ids,
            "reasons": sorted(set(certificate_reasons + blocking_reasons)),
        })
    report = {
        "schemaVersion": 1,
        "registryVersion": registry["registryVersion"],
        "evaluatedAt": evaluated_at,
        "decisions": decisions,
        "invalidated": sum(item["action"] == "invalidate" for item in decisions),
        "carriedForward": sum(item["action"] == "carry-forward" for item in decisions),
    }
    report["statusReportSha256"] = digest(report)
    return report


def merge_status_into_migration(
    migration: dict[str, Any], certification_status: dict[str, Any]
) -> dict[str, Any]:
    """Merge certification invalidation into the normal methodology migration decision set."""
    result = copy.deepcopy(migration)
    certification_by_assessment = {
        item["assessmentID"]: item
        for item in certification_status.get("decisions", [])
        if isinstance(item, dict) and isinstance(item.get("assessmentID"), str)
    }
    for decision in result.get("decisions", []):
        if not isinstance(decision, dict):
            continue
        certification = certification_by_assessment.get(decision.get("assessmentID"))
        if certification is None or certification.get("action") != "invalidate":
            continue
        reasons = set(decision.get("reasons", []))
        reasons.update(certification.get("reasons", []))
        decision["reasons"] = sorted(reasons)
        decision["action"] = "invalidate"
    result["invalidated"] = sum(item.get("action") == "invalidate" for item in result.get("decisions", []) if isinstance(item, dict))
    result["carriedForward"] = sum(item.get("action") == "carry-forward" for item in result.get("decisions", []) if isinstance(item, dict))
    result["certifierRegistryVersion"] = certification_status["registryVersion"]
    result["certificationEvaluatedAt"] = certification_status["evaluatedAt"]
    result.pop("migrationSha256", None)
    result["migrationSha256"] = digest(result)
    return result


def validity_events_from_status_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    occurred_at = _nonblank(report.get("evaluatedAt"), "evaluatedAt")
    _timestamp(occurred_at, "evaluatedAt")
    for decision in report.get("decisions", []):
        if not isinstance(decision, dict) or decision.get("action") != "invalidate":
            continue
        event: dict[str, Any] = {
            "assessmentID": decision["assessmentID"],
            "kind": "invalidated",
            "occurredAt": occurred_at,
            "reason": "; ".join(decision.get("reasons", [])) or "certification eligibility invalidated",
        }
        event["id"] = derive_id("validityEvents", event)
        events.append(event)
    return events


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    audit = sub.add_parser("audit-envelope")
    audit.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    audit.add_argument("--evidence", type=Path, required=True)
    audit.add_argument("--evaluated-at", required=True)
    audit.add_argument("--report-output", type=Path, required=True)
    audit.add_argument("--events-output", type=Path, required=True)
    args = parser.parse_args()
    registry = load_registry(args.registry)
    if args.command == "validate":
        print(f"Validated certifier registry {registry['registryVersion']} with {len(registry['entries'])} entries")
        return
    envelope = json.loads(args.evidence.read_text(encoding="utf-8"))
    report = certification_status_report(envelope=envelope, registry=registry, evaluated_at=args.evaluated_at)
    events = validity_events_from_status_report(report)
    _write_json(args.report_output, report)
    _write_json(args.events_output, {"schemaVersion": 1, "events": events})
    print(json.dumps({"invalidated": report["invalidated"], "carriedForward": report["carriedForward"]}, sort_keys=True))


if __name__ == "__main__":
    main()
