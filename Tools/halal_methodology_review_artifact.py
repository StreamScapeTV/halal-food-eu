"""Immutable review-artifact enrichment for explicit methodology reviews."""
from __future__ import annotations

import re
from typing import Any

from halal_methodology_core import MethodologyError, digest

POSITIVE_DECISIONS = {"halal-certified", "halal-reviewed"}
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")


def _validate_analysis_integrity(analysis: dict[str, Any]) -> None:
    supplied = analysis.get("analysisSha256")
    if not isinstance(supplied, str) or not SHA256_RE.fullmatch(supplied):
        raise MethodologyError("analysis report lacks a valid analysisSha256")
    unsigned = dict(analysis)
    unsigned.pop("analysisSha256", None)
    if digest(unsigned) != supplied:
        raise MethodologyError("analysis report content does not match analysisSha256")


def attach_checklist_snapshot(
    result: dict[str, Any],
    analysis: dict[str, Any],
    methodology: dict[str, Any],
) -> dict[str, Any]:
    """Bind immutable source/hash/checklist evidence to one explicit review artifact."""
    _validate_analysis_integrity(analysis)
    artifact = result.get("reviewArtifact")
    if not isinstance(artifact, dict):
        raise MethodologyError("review result lacks reviewArtifact")
    findings = [
        item
        for item in analysis.get("candidateFindings", [])
        if isinstance(item, dict)
    ]
    if artifact.get("decision") in POSITIVE_DECISIONS and any(
        item.get("outcome") == "prohibited-candidate" for item in findings
    ):
        raise MethodologyError("positive review cannot preserve an unresolved prohibited candidate")
    queue_defs = {
        item["id"]: item["checklist"]
        for item in methodology.get("reviewQueues", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("checklist"), list)
    }
    open_queues = [item["id"] for item in analysis.get("reviewQueues", []) if isinstance(item, dict) and isinstance(item.get("id"), str)]
    missing = sorted(set(open_queues) - set(queue_defs))
    if missing:
        raise MethodologyError(f"analysis references review queues missing from methodology: {', '.join(missing)}")
    artifact["ingredientContentHash"] = analysis.get("ingredientContentHash")
    artifact["checklists"] = [
        {"queueID": queue_id, "items": list(queue_defs[queue_id])}
        for queue_id in sorted(set(open_queues))
    ]
    artifact.pop("reviewArtifactSha256", None)
    artifact["reviewArtifactSha256"] = digest(artifact)
    return result
