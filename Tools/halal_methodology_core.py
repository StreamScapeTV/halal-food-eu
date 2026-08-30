#!/usr/bin/env python3
"""Deterministic, evidence-linked halal methodology analysis and review helpers.

Parser output from this module is deliberately non-authoritative. It can flag
candidate evidence and route review work, but it cannot create a positive or a
final negative assessment without an explicit review artifact.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

from evidence_model_core import derive_id

ALLOWED_OUTCOMES = {
    "prohibited-candidate",
    "acceptable-with-evidence",
    "ambiguous-review-required",
    "informational",
}
ALLOWED_MATCHES = {"token", "token-prefix", "phrase", "e-number"}
ALLOWED_PARSER_STATUSES = {"unknown", "questionable"}
FINAL_STATUSES = {"halal-certified", "halal-reviewed", "not-halal", "questionable", "unknown"}
POSITIVE_STATUSES = {"halal-certified", "halal-reviewed"}
FRESHNESS_STATES = {"fresh", "refresh-recommended", "stale", "date-unknown", "changed-unreviewed"}
QUEUE_IDS = {
    "clear-prohibited-confirmation",
    "ambiguous-origin-process",
    "new-changed-formulation",
    "positive-ingredient-review",
    "certification-validity",
    "conflicting-formulation",
    "package-text-verification",
    "methodology-migration",
}


class MethodologyError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MethodologyError(f"{field} must be a non-blank RFC3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MethodologyError(f"{field} must be RFC3339") from exc
    if parsed.tzinfo is None:
        raise MethodologyError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc)


def validate_methodology(methodology: dict[str, Any]) -> None:
    expected = {
        "schemaVersion", "methodologyVersion", "marketScope", "languages", "reviewedAt",
        "principles", "certificationPolicy", "rules", "reviewQueues", "changeLog",
    }
    if set(methodology) != expected or methodology.get("schemaVersion") != 1:
        raise MethodologyError("methodology has unsupported schema or fields")
    version = methodology.get("methodologyVersion")
    if not isinstance(version, str) or not version.strip():
        raise MethodologyError("methodologyVersion must be non-blank")
    markets = methodology.get("marketScope")
    if not isinstance(markets, list) or not markets or len(markets) != len(set(markets)) or any(not isinstance(x, str) or not re.fullmatch(r"[A-Z]{2}", x) for x in markets):
        raise MethodologyError("marketScope must be unique uppercase alpha-2 values")
    languages = methodology.get("languages")
    if not isinstance(languages, list) or not languages or len(languages) != len(set(languages)) or any(not isinstance(x, str) or not x.strip() for x in languages):
        raise MethodologyError("languages must be unique non-blank values")
    _timestamp(methodology.get("reviewedAt"), "reviewedAt")

    principles = methodology.get("principles")
    required_principles = {
        "noMatchStatus", "parserMayCreatePositiveStatus", "parserMayCreateFinalNegativeStatus",
        "translatedOnlyStatus", "ocrOnlyStatus", "staleOrDateUnknownStatus", "conflictStatus",
    }
    if not isinstance(principles, dict) or set(principles) != required_principles:
        raise MethodologyError("methodology principles are incomplete")
    if principles["noMatchStatus"] != "unknown" or principles["parserMayCreatePositiveStatus"] is not False or principles["parserMayCreateFinalNegativeStatus"] is not False:
        raise MethodologyError("parser safety principles may not permit positive/final-negative automation")
    if principles["conflictStatus"] != "questionable":
        raise MethodologyError("conflicts must route to questionable")
    for field in ("translatedOnlyStatus", "ocrOnlyStatus", "staleOrDateUnknownStatus"):
        if principles[field] not in {"unknown", "questionable"}:
            raise MethodologyError(f"{field} must fail closed")

    queues = methodology.get("reviewQueues")
    if not isinstance(queues, list) or not queues:
        raise MethodologyError("reviewQueues must be non-empty")
    queue_map: dict[str, list[str]] = {}
    for index, queue in enumerate(queues):
        if not isinstance(queue, dict) or set(queue) != {"id", "checklist"}:
            raise MethodologyError(f"reviewQueues[{index}] fields mismatch")
        qid = queue.get("id")
        checklist = queue.get("checklist")
        if not isinstance(qid, str) or qid not in QUEUE_IDS or qid in queue_map:
            raise MethodologyError(f"reviewQueues[{index}].id is unknown or duplicated")
        if not isinstance(checklist, list) or not checklist or any(not isinstance(x, str) or not x.strip() for x in checklist):
            raise MethodologyError(f"reviewQueues[{index}].checklist must contain non-blank text")
        queue_map[qid] = checklist

    rules = methodology.get("rules")
    if not isinstance(rules, list) or not rules:
        raise MethodologyError("rules must be non-empty")
    seen_rule_ids: set[str] = set()
    seen_reason_codes: set[str] = set()
    for index, rule in enumerate(rules):
        required = {"id", "category", "outcome", "aliases", "reasonCode", "title", "reviewQueue", "references", "reviewedAt"}
        allowed = required | {"excludeContexts"}
        if not isinstance(rule, dict) or not required.issubset(rule) or not set(rule).issubset(allowed):
            raise MethodologyError(f"rules[{index}] fields mismatch")
        rid, reason = rule.get("id"), rule.get("reasonCode")
        if not isinstance(rid, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", rid) or rid in seen_rule_ids:
            raise MethodologyError(f"rules[{index}].id is invalid or duplicated")
        if not isinstance(reason, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", reason):
            raise MethodologyError(f"rules[{index}].reasonCode is invalid")
        seen_rule_ids.add(rid)
        seen_reason_codes.add(reason)
        if rule.get("outcome") not in ALLOWED_OUTCOMES:
            raise MethodologyError(f"rules[{index}].outcome unsupported")
        if rule.get("reviewQueue") not in queue_map:
            raise MethodologyError(f"rules[{index}] references unknown review queue")
        aliases = rule.get("aliases")
        if not isinstance(aliases, list) or not aliases:
            raise MethodologyError(f"rules[{index}].aliases must be non-empty")
        alias_keys: set[tuple[str, str, str]] = set()
        for alias_index, alias in enumerate(aliases):
            if not isinstance(alias, dict) or set(alias) != {"language", "text", "match"}:
                raise MethodologyError(f"rules[{index}].aliases[{alias_index}] fields mismatch")
            if alias.get("match") not in ALLOWED_MATCHES:
                raise MethodologyError(f"rules[{index}].aliases[{alias_index}] match unsupported")
            if not isinstance(alias.get("language"), str) or alias["language"] not in set(languages) | {"und"}:
                raise MethodologyError(f"rules[{index}].aliases[{alias_index}] language unsupported")
            if not isinstance(alias.get("text"), str) or not alias["text"].strip():
                raise MethodologyError(f"rules[{index}].aliases[{alias_index}] text blank")
            key = (alias["language"], alias["text"].casefold(), alias["match"])
            if key in alias_keys:
                raise MethodologyError(f"rules[{index}] repeats an alias")
            alias_keys.add(key)
        excluded = rule.get("excludeContexts", [])
        if not isinstance(excluded, list) or any(not isinstance(x, str) or not x.strip() for x in excluded):
            raise MethodologyError(f"rules[{index}].excludeContexts invalid")
        refs = rule.get("references")
        if not isinstance(refs, list) or not refs or any(not isinstance(x, str) or not x.strip() for x in refs):
            raise MethodologyError(f"rules[{index}].references invalid")
        _timestamp(rule.get("reviewedAt"), f"rules[{index}].reviewedAt")

    cert = methodology.get("certificationPolicy")
    if not isinstance(cert, dict) or set(cert) != {"policyVersion", "defaultDecision", "acceptedCertifiers"}:
        raise MethodologyError("certificationPolicy fields mismatch")
    if cert.get("defaultDecision") != "review-required":
        raise MethodologyError("unknown certifiers/schemes must require review")
    if not isinstance(cert.get("acceptedCertifiers"), list):
        raise MethodologyError("acceptedCertifiers must be an array")

    log = methodology.get("changeLog")
    if not isinstance(log, list) or not log or not any(isinstance(item, dict) and item.get("version") == version for item in log):
        raise MethodologyError("changeLog must document the current methodology version")


def _normalize_character(character: str) -> str:
    decomposed = unicodedata.normalize("NFKD", character)
    pieces = []
    for item in decomposed:
        if unicodedata.combining(item):
            continue
        folded = item.casefold()
        for folded_item in folded:
            pieces.append(folded_item if folded_item.isalnum() else " ")
    return "".join(pieces)


def normalize_with_offsets(text: str) -> tuple[str, list[int]]:
    """Normalize for matching while retaining a map back to exact source offsets."""
    normalized: list[str] = []
    offsets: list[int] = []
    last_space = False
    for index, character in enumerate(text):
        piece = _normalize_character(character)
        if not piece:
            continue
        for normalized_character in piece:
            if normalized_character == " ":
                if last_space:
                    continue
                last_space = True
            else:
                last_space = False
            normalized.append(normalized_character)
            offsets.append(index)
    while normalized and normalized[0] == " ":
        normalized.pop(0)
        offsets.pop(0)
    while normalized and normalized[-1] == " ":
        normalized.pop()
        offsets.pop()
    return "".join(normalized), offsets


def _normalized_alias(value: str) -> str:
    return normalize_with_offsets(value)[0]


def _alias_pattern(alias: dict[str, Any]) -> re.Pattern[str]:
    normalized = _normalized_alias(alias["text"])
    parts = [re.escape(part) for part in normalized.split()]
    body = r"\s+".join(parts)
    if alias["match"] == "e-number":
        digits = "".join(character for character in normalized if character.isdigit())
        body = rf"e\s*{re.escape(digits)}"
        return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")
    if alias["match"] == "token-prefix":
        return re.compile(rf"(?<![a-z0-9]){body}[a-z0-9]*(?![a-z0-9])")
    return re.compile(rf"(?<![a-z0-9]){body}(?![a-z0-9])")


def _overlapping_exclusion(normalized_text: str, start: int, end: int, exclusions: list[str]) -> str | None:
    for excluded in exclusions:
        normalized_excluded = _normalized_alias(excluded)
        if not normalized_excluded:
            continue
        cursor = 0
        while True:
            index = normalized_text.find(normalized_excluded, cursor)
            if index < 0:
                break
            excluded_end = index + len(normalized_excluded)
            if index <= start < excluded_end or index < end <= excluded_end or (start <= index and excluded_end <= end):
                return excluded
            cursor = index + 1
    return None


def _source_span(text: str, offsets: list[int], start: int, end: int) -> dict[str, Any]:
    if not offsets or start >= len(offsets) or end <= 0:
        raise MethodologyError("cannot map normalized candidate back to source text")
    source_start = offsets[start]
    source_end = offsets[min(end - 1, len(offsets) - 1)] + 1
    return {"start": source_start, "end": source_end, "text": text[source_start:source_end]}


def _queue_map(methodology: dict[str, Any]) -> dict[str, list[str]]:
    return {item["id"]: list(item["checklist"]) for item in methodology["reviewQueues"]}


def analyze_ingredient(
    ingredient: dict[str, Any] | None,
    methodology: dict[str, Any],
    *,
    gtin: str,
    market: str,
    freshness_state: str = "fresh",
    conflict_flags: list[str] | None = None,
) -> dict[str, Any]:
    """Return a non-authoritative parser report and deterministic review queues."""
    validate_methodology(methodology)
    if freshness_state not in FRESHNESS_STATES:
        raise MethodologyError(f"unsupported freshness state {freshness_state!r}")
    if market not in methodology["marketScope"]:
        raise MethodologyError(f"market {market!r} is outside methodology scope")
    conflicts = sorted(set(conflict_flags or []))
    queues = _queue_map(methodology)
    candidate_findings: list[dict[str, Any]] = []
    queue_reasons: dict[str, set[str]] = {}
    safety_flags: set[str] = set()

    def require_queue(queue_id: str, reason: str) -> None:
        queue_reasons.setdefault(queue_id, set()).add(reason)

    if ingredient is None:
        safety_flags.add("ingredients-missing")
        report = {
            "schemaVersion": 1,
            "methodologyVersion": methodology["methodologyVersion"],
            "gtin": gtin,
            "market": market,
            "ingredientObservationID": None,
            "ingredientContentHash": None,
            "sourceLanguage": None,
            "sourceText": None,
            "sourceTextSha256": None,
            "freshnessState": freshness_state,
            "conflictFlags": conflicts,
            "parserStatus": "unknown",
            "candidateFindings": [],
            "reviewQueues": [],
            "safetyFlags": sorted(safety_flags),
        }
        report["analysisSha256"] = digest(report)
        return report

    for required in ("id", "gtin", "market", "ingredientsText", "languageCode", "contentHash", "captureMethod", "verificationState"):
        if required not in ingredient:
            raise MethodologyError(f"ingredient observation is missing {required}")
    if ingredient["gtin"] != gtin or ingredient["market"] != market:
        raise MethodologyError("ingredient observation does not match requested GTIN/market")
    source_text = ingredient["ingredientsText"]
    if not isinstance(source_text, str) or not source_text.strip():
        raise MethodologyError("ingredient source text must be non-blank")
    language = str(ingredient["languageCode"])
    base_language = language.split("-")[0].casefold()
    normalized_text, offsets = normalize_with_offsets(source_text)

    for rule in methodology["rules"]:
        for alias in rule["aliases"]:
            alias_language = alias["language"].casefold()
            if alias_language != "und" and alias_language != base_language:
                continue
            pattern = _alias_pattern(alias)
            for match in pattern.finditer(normalized_text):
                excluded = _overlapping_exclusion(normalized_text, match.start(), match.end(), rule.get("excludeContexts", []))
                if excluded is not None:
                    continue
                span = _source_span(source_text, offsets, match.start(), match.end())
                candidate_findings.append({
                    "ruleID": rule["id"],
                    "category": rule["category"],
                    "outcome": rule["outcome"],
                    "reasonCode": rule["reasonCode"],
                    "title": rule["title"],
                    "ingredientObservationID": ingredient["id"],
                    "ingredientContentHash": ingredient["contentHash"],
                    "sourceSpan": span,
                    "alias": {"language": alias["language"], "text": alias["text"]},
                    "references": sorted(rule["references"]),
                })
                if rule["outcome"] != "informational":
                    require_queue(rule["reviewQueue"], rule["reasonCode"])

    unique: dict[str, dict[str, Any]] = {canonical_json(item): item for item in candidate_findings}
    candidate_findings = [unique[key] for key in sorted(unique)]

    if ingredient.get("supersedesID"):
        safety_flags.add("formulation-changed")
        require_queue("new-changed-formulation", "formulation-changed")
    if conflicts:
        safety_flags.add("formulation-conflict")
        require_queue("conflicting-formulation", "formulation-conflict")
    if ingredient.get("captureMethod") == "ocr" or ingredient.get("verificationState") != "human-verified":
        safety_flags.add("package-text-verification-required")
        require_queue("package-text-verification", "package-text-verification-required")
    if isinstance(ingredient.get("transformation"), dict):
        safety_flags.add("transformed-text-requires-verification")
        require_queue("package-text-verification", "transformed-text-requires-verification")
    if freshness_state in {"stale", "date-unknown", "changed-unreviewed"}:
        safety_flags.add(f"formulation-{freshness_state}")
        require_queue("new-changed-formulation", f"formulation-{freshness_state}")
    elif freshness_state == "refresh-recommended":
        safety_flags.add("formulation-refresh-recommended")
    if not candidate_findings and not queue_reasons and freshness_state in {"fresh", "refresh-recommended"}:
        require_queue("positive-ingredient-review", "no-parser-candidate-human-review-required")

    has_material_candidate = any(item["outcome"] in {"prohibited-candidate", "ambiguous-review-required", "acceptable-with-evidence"} for item in candidate_findings)
    parser_status = "questionable" if has_material_candidate or any(flag in safety_flags for flag in {"formulation-conflict", "package-text-verification-required", "transformed-text-requires-verification"}) else "unknown"
    if freshness_state in {"stale", "date-unknown", "changed-unreviewed"} and parser_status != "questionable":
        parser_status = methodology["principles"]["staleOrDateUnknownStatus"]
    if parser_status not in ALLOWED_PARSER_STATUSES:
        raise MethodologyError("methodology parser attempted an authoritative status")

    review_queues = [
        {
            "id": queue_id,
            "reasons": sorted(reasons),
            "checklist": queues[queue_id],
            "ingredientObservationID": ingredient["id"],
            "ingredientContentHash": ingredient["contentHash"],
        }
        for queue_id, reasons in sorted(queue_reasons.items())
    ]
    report = {
        "schemaVersion": 1,
        "methodologyVersion": methodology["methodologyVersion"],
        "gtin": gtin,
        "market": market,
        "ingredientObservationID": ingredient["id"],
        "ingredientContentHash": ingredient["contentHash"],
        "sourceLanguage": language,
        "sourceText": source_text,
        "sourceTextSha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
        "freshnessState": freshness_state,
        "conflictFlags": conflicts,
        "parserStatus": parser_status,
        "candidateFindings": candidate_findings,
        "reviewQueues": review_queues,
        "safetyFlags": sorted(safety_flags),
    }
    report["analysisSha256"] = digest(report)
    return report


def validate_review_input(review: dict[str, Any], report: dict[str, Any]) -> None:
    required = {"decision", "reviewerID", "reviewedAt", "nextReviewAt", "limitations", "reason", "resolvedQueues", "additionalEvidenceIDs"}
    if not isinstance(review, dict) or set(review) != required:
        raise MethodologyError("review input fields mismatch")
    if review.get("decision") not in FINAL_STATUSES:
        raise MethodologyError("review decision is unsupported")
    if not isinstance(review.get("reviewerID"), str) or not review["reviewerID"].strip():
        raise MethodologyError("reviewerID must be non-blank")
    reviewed_at = _timestamp(review.get("reviewedAt"), "reviewedAt")
    next_review = _timestamp(review.get("nextReviewAt"), "nextReviewAt")
    if next_review <= reviewed_at:
        raise MethodologyError("nextReviewAt must follow reviewedAt")
    for field in ("limitations", "reason"):
        if not isinstance(review.get(field), str) or not review[field].strip():
            raise MethodologyError(f"{field} must be non-blank")
    resolved = review.get("resolvedQueues")
    if not isinstance(resolved, dict):
        raise MethodologyError("resolvedQueues must be an object")
    open_queues = {item["id"] for item in report.get("reviewQueues", []) if isinstance(item, dict)}
    for queue_id, evidence_ids in resolved.items():
        if queue_id not in open_queues:
            raise MethodologyError(f"review resolves queue {queue_id!r} that is not open")
        if not isinstance(evidence_ids, list) or not evidence_ids or any(not isinstance(item, str) or not item.startswith("hfeu:") for item in evidence_ids):
            raise MethodologyError(f"resolved queue {queue_id!r} must cite evidence IDs")
    extra = review.get("additionalEvidenceIDs")
    if not isinstance(extra, list) or any(not isinstance(item, str) or not item.startswith("hfeu:") for item in extra):
        raise MethodologyError("additionalEvidenceIDs must contain evidence IDs")


def _accepted_certifier(methodology: dict[str, Any], certification: dict[str, Any], reviewed_at: datetime) -> bool:
    for accepted in methodology["certificationPolicy"]["acceptedCertifiers"]:
        if accepted.get("certifier") != certification.get("certifier") or accepted.get("scheme") != certification.get("scheme"):
            continue
        if certification.get("market") not in accepted.get("markets", []):
            continue
        accepted_at = _timestamp(accepted.get("reviewedAt"), "acceptedCertifier.reviewedAt")
        if accepted_at > reviewed_at:
            continue
        expires = accepted.get("expiresAt")
        if expires is not None and reviewed_at >= _timestamp(expires, "acceptedCertifier.expiresAt"):
            continue
        return True
    return False


def _certification_valid(certification: dict[str, Any], *, gtin: str, market: str, at: datetime) -> bool:
    if certification.get("gtin") != gtin or certification.get("market") != market:
        return False
    effective = certification.get("effectiveAt")
    expiry = certification.get("expiryAt")
    revoked = certification.get("revokedAt")
    suspended = certification.get("suspendedAt")
    if effective is not None and _timestamp(effective, "certification.effectiveAt") > at:
        return False
    if expiry is not None and _timestamp(expiry, "certification.expiryAt") < at:
        return False
    if revoked is not None and _timestamp(revoked, "certification.revokedAt") <= at:
        return False
    if suspended is not None and _timestamp(suspended, "certification.suspendedAt") <= at:
        return False
    return True


def complete_review(
    *,
    report: dict[str, Any],
    methodology: dict[str, Any],
    review_input: dict[str, Any],
    certifications: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create immutable review/assessment records only from explicit human input."""
    validate_methodology(methodology)
    if report.get("methodologyVersion") != methodology["methodologyVersion"]:
        raise MethodologyError("analysis report methodology does not match current methodology")
    if report.get("ingredientObservationID") is None:
        if review_input.get("decision") in POSITIVE_STATUSES | {"not-halal"}:
            raise MethodologyError("missing ingredient evidence cannot support this decision")
    validate_review_input(review_input, report)
    decision = review_input["decision"]
    reviewed_at = _timestamp(review_input["reviewedAt"], "reviewedAt")
    open_queues = {item["id"] for item in report.get("reviewQueues", []) if isinstance(item, dict)}
    resolved = set(review_input["resolvedQueues"])
    unresolved = open_queues - resolved
    findings = [item for item in report.get("candidateFindings", []) if isinstance(item, dict)]

    if decision in POSITIVE_STATUSES and any(item.get("outcome") == "prohibited-candidate" for item in findings):
        raise MethodologyError("positive review cannot preserve an unresolved prohibited candidate")
    if decision in POSITIVE_STATUSES and unresolved:
        raise MethodologyError("positive review requires every open review queue to be explicitly resolved with evidence")
    if decision in POSITIVE_STATUSES and report.get("freshnessState") != "fresh":
        raise MethodologyError("positive review requires fresh exact formulation evidence")
    if decision in POSITIVE_STATUSES and report.get("conflictFlags"):
        raise MethodologyError("positive review cannot hide unresolved formulation conflicts")
    if decision == "not-halal":
        if "clear-prohibited-confirmation" not in resolved:
            raise MethodologyError("not-halal requires confirmed explicit prohibited evidence")
        if not any(item.get("outcome") == "prohibited-candidate" for item in findings):
            raise MethodologyError("not-halal requires a prohibited candidate in the exact source observation")
    if decision == "halal-reviewed" and report.get("ingredientObservationID") is None:
        raise MethodologyError("halal-reviewed requires exact ingredient evidence")

    certs = certifications or []
    selected_certification_ids: list[str] = []
    if decision == "halal-certified":
        valid = [
            cert for cert in certs
            if isinstance(cert, dict)
            and _certification_valid(cert, gtin=report["gtin"], market=report["market"], at=reviewed_at)
            and _accepted_certifier(methodology, cert, reviewed_at)
        ]
        if not valid:
            raise MethodologyError("halal-certified requires current exact-scope certification from an accepted certifier/scheme")
        selected_certification_ids = sorted(str(cert["id"]) for cert in valid if isinstance(cert.get("id"), str))
        if not selected_certification_ids:
            raise MethodologyError("accepted certification evidence must have immutable IDs")

    ingredient_id = report.get("ingredientObservationID")
    evidence_ids = set(review_input["additionalEvidenceIDs"])
    if isinstance(ingredient_id, str):
        evidence_ids.add(ingredient_id)
    for values in review_input["resolvedQueues"].values():
        evidence_ids.update(values)
    evidence_ids.update(selected_certification_ids)
    if not evidence_ids:
        raise MethodologyError("reviewed assessment must cite evidence")

    reason_code = {
        "halal-reviewed": "completed-methodology-review",
        "halal-certified": "current-exact-certification",
        "not-halal": "confirmed-prohibited-evidence",
        "questionable": "reviewed-unresolved-ambiguity",
        "unknown": "reviewed-insufficient-evidence",
    }[decision]
    severity = {
        "halal-reviewed": "positive",
        "halal-certified": "positive",
        "not-halal": "prohibitive",
        "questionable": "caution",
        "unknown": "caution",
    }[decision]
    source_ingredient = None
    if decision == "not-halal":
        prohibited = next(item for item in findings if item.get("outcome") == "prohibited-candidate")
        source_ingredient = prohibited.get("sourceSpan", {}).get("text")
    reason = {
        "code": reason_code,
        "title": {
            "halal-reviewed": "Completed ingredient review under the named methodology",
            "halal-certified": "Current exact-scope certification accepted under the named policy",
            "not-halal": "Confirmed prohibited evidence in the exact ingredient observation",
            "questionable": "Review completed with material ambiguity remaining",
            "unknown": "Review completed with insufficient usable evidence",
        }[decision],
        "detail": review_input["reason"],
        "severity": severity,
        "evidenceIDs": sorted(evidence_ids),
    }
    if source_ingredient:
        reason["ingredient"] = source_ingredient

    assessment: dict[str, Any] = {
        "gtin": report["gtin"],
        "market": report["market"],
        "status": decision,
        "methodologyVersion": methodology["methodologyVersion"],
        "assessedAt": review_input["reviewedAt"],
        "certificationIDs": selected_certification_ids,
        "evidenceIDs": sorted(evidence_ids),
        "reasons": [reason],
        "recheckAt": review_input["nextReviewAt"],
    }
    if isinstance(ingredient_id, str):
        assessment["ingredientObservationID"] = ingredient_id
    assessment["id"] = derive_id("assessments", assessment)

    review_record: dict[str, Any] = {
        "targetID": assessment["id"],
        "targetType": "assessment",
        "state": "approved",
        "reviewerID": review_input["reviewerID"],
        "reviewedAt": review_input["reviewedAt"],
        "decisionCode": reason_code,
        "reason": review_input["reason"],
        "methodologyVersion": methodology["methodologyVersion"],
        "toolContext": f"halal-methodology:{methodology['methodologyVersion']} analysis:{report.get('analysisSha256', 'unknown')}",
    }
    review_record["id"] = derive_id("reviews", review_record)

    review_artifact = {
        "schemaVersion": 1,
        "methodologyVersion": methodology["methodologyVersion"],
        "analysisSha256": report.get("analysisSha256"),
        "assessmentID": assessment["id"],
        "reviewID": review_record["id"],
        "decision": decision,
        "reviewerID": review_input["reviewerID"],
        "reviewedAt": review_input["reviewedAt"],
        "nextReviewAt": review_input["nextReviewAt"],
        "limitations": review_input["limitations"],
        "reason": review_input["reason"],
        "resolvedQueues": {key: sorted(value) for key, value in sorted(review_input["resolvedQueues"].items())},
        "openQueuesAtReview": sorted(open_queues),
        "evidenceIDs": sorted(evidence_ids),
    }
    review_artifact["reviewArtifactSha256"] = digest(review_artifact)
    return {"assessment": assessment, "review": review_record, "reviewArtifact": review_artifact}


def assessment_migration_report(
    *,
    envelope: dict[str, Any],
    methodology: dict[str, Any],
) -> dict[str, Any]:
    """Determine which existing assessments can remain attached to current selections."""
    validate_methodology(methodology)
    assessments = {item["id"]: item for item in envelope.get("assessments", []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
    ingredients = {item["id"]: item for item in envelope.get("ingredients", []) if isinstance(item, dict) and isinstance(item.get("id"), str)}
    decisions: list[dict[str, Any]] = []
    for selection in sorted(
        [item for item in envelope.get("currentSelections", []) if isinstance(item, dict)],
        key=lambda item: (str(item.get("gtin", "")), str(item.get("market", ""))),
    ):
        assessment_id = selection.get("assessmentID")
        if not isinstance(assessment_id, str):
            continue
        assessment = assessments.get(assessment_id)
        if assessment is None:
            continue
        reasons: list[str] = []
        selected_ingredient_id = selection.get("ingredientObservationID")
        assessment_ingredient_id = assessment.get("ingredientObservationID")
        if selected_ingredient_id != assessment_ingredient_id:
            reasons.append("selected-formulation-changed")
        if assessment.get("methodologyVersion") != methodology["methodologyVersion"]:
            reasons.append("methodology-version-changed")
        if assessment.get("status") == "halal-certified":
            if sorted(assessment.get("certificationIDs", [])) != sorted(selection.get("certificationIDs", [])):
                reasons.append("certification-selection-changed")
        if selection.get("conflictFlags") and assessment.get("status") in POSITIVE_STATUSES:
            reasons.append("current-formulation-conflict")
        ingredient = ingredients.get(selected_ingredient_id) if isinstance(selected_ingredient_id, str) else None
        if ingredient is not None and ingredient.get("supersedesID") == assessment_ingredient_id:
            if "selected-formulation-changed" not in reasons:
                reasons.append("selected-formulation-changed")
        decisions.append({
            "gtin": selection.get("gtin"),
            "market": selection.get("market"),
            "assessmentID": assessment_id,
            "action": "invalidate" if reasons else "carry-forward",
            "reasons": sorted(set(reasons)),
        })
    report = {
        "schemaVersion": 1,
        "methodologyVersion": methodology["methodologyVersion"],
        "decisions": decisions,
        "invalidated": sum(item["action"] == "invalidate" for item in decisions),
        "carriedForward": sum(item["action"] == "carry-forward" for item in decisions),
    }
    report["migrationSha256"] = digest(report)
    return report


def validity_events_from_migration(report: dict[str, Any], *, occurred_at: str) -> list[dict[str, Any]]:
    _timestamp(occurred_at, "occurredAt")
    events: list[dict[str, Any]] = []
    for decision in report.get("decisions", []):
        if not isinstance(decision, dict) or decision.get("action") != "invalidate":
            continue
        event: dict[str, Any] = {
            "assessmentID": decision["assessmentID"],
            "kind": "invalidated",
            "occurredAt": occurred_at,
            "reason": "; ".join(decision.get("reasons", [])) or "methodology compatibility invalidated",
        }
        event["id"] = derive_id("validityEvents", event)
        events.append(event)
    return events
