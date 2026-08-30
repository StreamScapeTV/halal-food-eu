"""Immutable review-artifact enrichment for explicit methodology reviews."""
from __future__ import annotations

from typing import Any

from halal_methodology_core import MethodologyError, digest


def attach_checklist_snapshot(
    result: dict[str, Any],
    analysis: dict[str, Any],
    methodology: dict[str, Any],
) -> dict[str, Any]:
    """Snapshot the exact reviewed checklists so later methodology edits cannot rewrite history."""
    artifact = result.get("reviewArtifact")
    if not isinstance(artifact, dict):
        raise MethodologyError("review result lacks reviewArtifact")
    queue_defs = {
        item["id"]: item["checklist"]
        for item in methodology.get("reviewQueues", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("checklist"), list)
    }
    open_queues = [item["id"] for item in analysis.get("reviewQueues", []) if isinstance(item, dict) and isinstance(item.get("id"), str)]
    missing = sorted(set(open_queues) - set(queue_defs))
    if missing:
        raise MethodologyError(f"analysis references review queues missing from methodology: {', '.join(missing)}")
    artifact["checklists"] = [
        {"queueID": queue_id, "items": list(queue_defs[queue_id])}
        for queue_id in sorted(set(open_queues))
    ]
    artifact.pop("reviewArtifactSha256", None)
    artifact["reviewArtifactSha256"] = digest(artifact)
    return result
