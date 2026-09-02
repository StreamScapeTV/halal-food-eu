import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Tools"))

from catalog_refresh import evaluate, promote_state
from catalog_refresh_operational_state import (
    OperationalRefreshError,
    apply_operational_clock,
    validate_operational_report,
    validate_operational_state,
)

POLICY = json.loads((ROOT / "Data" / "refresh" / "catalog-refresh-policy-v1.json").read_text(encoding="utf-8"))
QUALITY_POLICY = {"freshness": {"formulation": {"refreshRecommendedMonths": 9, "staleMonths": 12}}}
SOURCE_POLICY = {"schemaVersion": 1, "sourceKey": "open-food-facts"}
QUALITY = {"status": "pass", "releaseBlockingFindings": []}
EVIDENCE = {"currentSelections": [], "ingredients": [], "assessments": [], "certifications": []}


def metadata(snapshot="off-1", retrieved="2026-09-02T00:00:00Z", digest="a" * 64, *, complete=True, mode="full"):
    return {
        "sourceKey": "open-food-facts",
        "snapshotID": snapshot,
        "mode": mode,
        "retrievedAt": retrieved,
        "downloadComplete": complete,
        "recordsEmitted": 10,
        "transportSha256": digest,
        "httpMetadata": {},
    }


def evaluated(meta, *, quality=QUALITY, previous=None, at=None):
    at = at or meta["retrievedAt"]
    state, report, _ = evaluate(
        policy=copy.deepcopy(POLICY),
        quality_policy=QUALITY_POLICY,
        source_policy=SOURCE_POLICY,
        acquisition=copy.deepcopy(meta),
        evidence=copy.deepcopy(EVIDENCE),
        quality=copy.deepcopy(quality),
        change=None,
        previous=copy.deepcopy(previous),
        evaluated_at=at,
    )
    return state, report


class OperationalRefreshStateTests(unittest.TestCase):
    def test_successful_full_advances_acquisition_clock_without_accepting_candidate(self):
        state, report = evaluated(metadata())
        state, report = apply_operational_clock(
            state=state,
            report=report,
            policy=copy.deepcopy(POLICY),
        )
        self.assertIsNone(state["acceptedComplete"])
        self.assertEqual(state["candidateComplete"]["snapshotID"], "off-1")
        self.assertEqual(state["lastSuccessfulFullAcquisitionAt"], "2026-09-02T00:00:00Z")
        self.assertEqual(state["lastSuccessfulFullSnapshotID"], "off-1")
        self.assertEqual(state["nextFullDueAt"], "2026-09-09T00:00:00Z")
        self.assertEqual(report["nextFullDueAt"], "2026-09-09T00:00:00Z")
        validate_operational_state(state)
        validate_operational_report(report)

    def test_same_content_weekly_success_moves_only_operational_clock(self):
        first_state, first_report = evaluated(metadata())
        first_state, first_report = apply_operational_clock(
            state=first_state,
            report=first_report,
            policy=copy.deepcopy(POLICY),
        )
        accepted = promote_state(copy.deepcopy(POLICY), first_state)
        accepted_retrieved = accepted["acceptedComplete"]["retrievedAt"]

        second_meta = metadata(snapshot="off-2", retrieved="2026-09-09T00:00:00Z", digest="a" * 64)
        second_state, second_report = evaluated(second_meta, previous=accepted)
        second_state, second_report = apply_operational_clock(
            state=second_state,
            report=second_report,
            policy=copy.deepcopy(POLICY),
            previous=accepted,
        )
        self.assertFalse(second_state["candidateEligible"])
        self.assertIsNone(second_state["candidateComplete"])
        self.assertEqual(second_state["acceptedComplete"]["retrievedAt"], accepted_retrieved)
        self.assertEqual(second_state["lastSuccessfulFullAcquisitionAt"], "2026-09-09T00:00:00Z")
        self.assertEqual(second_state["lastSuccessfulFullSnapshotID"], "off-2")
        self.assertEqual(second_state["nextFullDueAt"], "2026-09-16T00:00:00Z")
        self.assertTrue(second_report["noFalseFreshness"])

    def test_partial_attempt_preserves_previous_operational_clock(self):
        first_state, first_report = evaluated(metadata())
        first_state, first_report = apply_operational_clock(
            state=first_state,
            report=first_report,
            policy=copy.deepcopy(POLICY),
        )
        partial_meta = metadata(snapshot="off-partial", retrieved="2026-09-09T00:00:00Z", digest="b" * 64, complete=False, mode="sample")
        partial_state, partial_report = evaluated(partial_meta, previous=first_state)
        partial_state, partial_report = apply_operational_clock(
            state=partial_state,
            report=partial_report,
            policy=copy.deepcopy(POLICY),
            previous=first_state,
        )
        self.assertEqual(partial_state["lastSuccessfulFullAcquisitionAt"], "2026-09-02T00:00:00Z")
        self.assertEqual(partial_state["nextFullDueAt"], "2026-09-09T00:00:00Z")
        self.assertEqual(partial_report["attemptStatus"], "partial")
        self.assertFalse(partial_report["deletionReconciliationAllowed"])

    def test_quality_block_preserves_previous_operational_clock(self):
        first_state, first_report = evaluated(metadata())
        first_state, first_report = apply_operational_clock(
            state=first_state,
            report=first_report,
            policy=copy.deepcopy(POLICY),
        )
        blocked = {"status": "blocked", "releaseBlockingFindings": [{"code": "SOURCE-TERMS"}]}
        state, report = evaluated(
            metadata(snapshot="off-blocked", retrieved="2026-09-09T00:00:00Z", digest="b" * 64),
            quality=blocked,
            previous=first_state,
        )
        state, report = apply_operational_clock(
            state=state,
            report=report,
            policy=copy.deepcopy(POLICY),
            previous=first_state,
        )
        self.assertEqual(state["lastSuccessfulFullAcquisitionAt"], "2026-09-02T00:00:00Z")
        self.assertEqual(state["nextFullDueAt"], "2026-09-09T00:00:00Z")

    def test_delta_does_not_reset_full_acquisition_clock(self):
        policy = copy.deepcopy(POLICY)
        policy["sources"]["open-food-facts"]["supportedAcquisitionModes"] = ["full", "delta"]
        first_state, first_report = evaluated(metadata())
        first_state, first_report = apply_operational_clock(
            state=first_state,
            report=first_report,
            policy=policy,
        )
        delta_meta = metadata(snapshot="off-delta", retrieved="2026-09-04T00:00:00Z", digest="b" * 64, mode="delta")
        state, report = evaluate(
            policy=policy,
            quality_policy=QUALITY_POLICY,
            source_policy=SOURCE_POLICY,
            acquisition=delta_meta,
            evidence=copy.deepcopy(EVIDENCE),
            quality=copy.deepcopy(QUALITY),
            change=None,
            previous=first_state,
            evaluated_at="2026-09-04T00:00:00Z",
        )[:2]
        state, report = apply_operational_clock(
            state=state,
            report=report,
            policy=policy,
            previous=first_state,
        )
        self.assertEqual(state["lastSuccessfulFullAcquisitionAt"], "2026-09-02T00:00:00Z")
        self.assertEqual(state["nextFullDueAt"], "2026-09-09T00:00:00Z")

    def test_timestamp_and_snapshot_are_atomic(self):
        state, report = evaluated(metadata())
        state, report = apply_operational_clock(state=state, report=report, policy=copy.deepcopy(POLICY))
        state["lastSuccessfulFullSnapshotID"] = None
        with self.assertRaises(OperationalRefreshError):
            validate_operational_state(state)


if __name__ == "__main__":
    unittest.main()
