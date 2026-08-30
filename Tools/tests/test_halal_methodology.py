import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOLS = ROOT / "Tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))
SPEC = importlib.util.spec_from_file_location("halal_methodology_core_test", TOOLS / "halal_methodology_core.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
METHODOLOGY = json.loads((ROOT / "Data" / "methodology" / "halal-methodology-v1.json").read_text(encoding="utf-8"))


def ingredient(text: str, *, language="en", capture="source-text", verification="human-verified", content_hash="a" * 64, supersedes=None, transformation=None):
    record = {
        "id": "hfeu:ingredient:sha256:" + "1" * 64,
        "gtin": "04006381333931",
        "market": "DE",
        "sourceKey": "synthetic-core",
        "sourceRecordID": "fixture",
        "ingredientsText": text,
        "languageCode": language,
        "retrievedAt": "2026-08-30T00:00:00Z",
        "contentHash": content_hash,
        "captureMethod": capture,
        "verificationState": verification,
    }
    if supersedes:
        record["supersedesID"] = supersedes
    if transformation:
        record["transformation"] = transformation
    return record


def analyze(record, *, freshness="fresh", conflicts=None):
    return MODULE.analyze_ingredient(
        record,
        METHODOLOGY,
        gtin="04006381333931",
        market="DE",
        freshness_state=freshness,
        conflict_flags=conflicts or [],
    )


def review_input(decision, report, *, resolve=True):
    resolved = {}
    if resolve:
        evidence = report.get("ingredientObservationID") or "hfeu:identity:sha256:" + "2" * 64
        resolved = {queue["id"]: [evidence] for queue in report.get("reviewQueues", [])}
    return {
        "decision": decision,
        "reviewerID": "reviewer:fixture",
        "reviewedAt": "2026-08-30T12:00:00Z",
        "nextReviewAt": "2027-02-28T12:00:00Z",
        "limitations": "Fixture review used only to exercise methodology invariants.",
        "reason": "Explicit fixture review completed against the exact source observation.",
        "resolvedQueues": resolved,
        "additionalEvidenceIDs": [],
    }


class MethodologyValidationTests(unittest.TestCase):
    def test_committed_methodology_validates(self):
        MODULE.validate_methodology(METHODOLOGY)

    def test_methodology_cannot_enable_positive_parser_automation(self):
        broken = copy.deepcopy(METHODOLOGY)
        broken["principles"]["parserMayCreatePositiveStatus"] = True
        with self.assertRaises(MODULE.MethodologyError):
            MODULE.validate_methodology(broken)


class CandidateAnalysisTests(unittest.TestCase):
    def test_explicit_english_pork_is_candidate_not_final_negative(self):
        report = analyze(ingredient("Water, pork fat, salt"))
        self.assertEqual(report["parserStatus"], "questionable")
        self.assertTrue(any(item["outcome"] == "prohibited-candidate" for item in report["candidateFindings"]))
        self.assertTrue(any(item["sourceSpan"]["text"].casefold() == "pork fat" for item in report["candidateFindings"]))
        self.assertIn("clear-prohibited-confirmation", {item["id"] for item in report["reviewQueues"]})
        self.assertNotIn(report["parserStatus"], {"not-halal", "halal-certified", "halal-reviewed"})

    def test_explicit_german_pork_is_candidate_with_exact_source_span(self):
        text = "Zutaten: Wasser, Schweinefleisch, Salz"
        report = analyze(ingredient(text, language="de"))
        finding = next(item for item in report["candidateFindings"] if item["reasonCode"] == "explicit-pork-ingredient")
        span = finding["sourceSpan"]
        self.assertEqual(text[span["start"]:span["end"]], "Schweinefleisch")

    def test_generic_gelatine_and_e471_require_origin_review(self):
        report = analyze(ingredient("Sugar, gelatine, emulsifier E471"))
        codes = {item["reasonCode"] for item in report["candidateFindings"]}
        self.assertIn("gelatine-origin-required", codes)
        self.assertIn("emulsifier-origin-required", codes)
        self.assertFalse(any(item["outcome"] == "prohibited-candidate" for item in report["candidateFindings"]))

    def test_alcohol_false_positive_context_is_excluded(self):
        report = analyze(ingredient("Sweetener: sugar alcohols (sorbitol), cocoa"))
        self.assertFalse(any(item["reasonCode"] == "alcohol-context-review-required" for item in report["candidateFindings"]))
        self.assertEqual(report["parserStatus"], "unknown")

    def test_direct_ethanol_is_review_required_not_auto_negative(self):
        report = analyze(ingredient("Water, ethanol, flavouring"))
        codes = {item["reasonCode"] for item in report["candidateFindings"]}
        self.assertIn("alcohol-context-review-required", codes)
        self.assertEqual(report["parserStatus"], "questionable")

    def test_german_zuckeralkohole_does_not_trigger_alcohol_rule(self):
        report = analyze(ingredient("Süßungsmittel: Zuckeralkohole, Aroma", language="de"))
        alcohol = [item for item in report["candidateFindings"] if item["reasonCode"] == "alcohol-context-review-required"]
        self.assertEqual(alcohol, [])
        self.assertTrue(any(item["reasonCode"] == "flavouring-origin-carrier-required" for item in report["candidateFindings"]))

    def test_allergen_and_traces_are_not_scanned_as_ingredients(self):
        record = ingredient("Water, sugar")
        record["allergensText"] = "pork"
        record["tracesText"] = "porcine"
        report = analyze(record)
        self.assertFalse(any(item["outcome"] == "prohibited-candidate" for item in report["candidateFindings"]))

    def test_no_match_stays_unknown_and_requires_explicit_positive_review(self):
        report = analyze(ingredient("Water, sugar, cocoa"))
        self.assertEqual(report["parserStatus"], "unknown")
        self.assertEqual(report["candidateFindings"], [])
        self.assertIn("positive-ingredient-review", {item["id"] for item in report["reviewQueues"]})

    def test_ocr_and_transformed_text_require_package_verification(self):
        record = ingredient(
            "Water, sugar",
            capture="ocr",
            verification="machine-assisted",
            transformation={"tool": "fixture-translator", "version": "1", "language": "en", "confidence": 0.9},
        )
        report = analyze(record)
        self.assertEqual(report["parserStatus"], "questionable")
        self.assertIn("package-text-verification", {item["id"] for item in report["reviewQueues"]})
        self.assertIn("transformed-text-requires-verification", report["safetyFlags"])

    def test_changed_stale_and_conflicting_formulation_routes_review(self):
        record = ingredient("Water, sugar", supersedes="hfeu:ingredient:sha256:" + "3" * 64)
        report = analyze(record, freshness="changed-unreviewed", conflicts=["source-formulation-conflict"])
        queues = {item["id"] for item in report["reviewQueues"]}
        self.assertIn("new-changed-formulation", queues)
        self.assertIn("conflicting-formulation", queues)
        self.assertEqual(report["parserStatus"], "questionable")

    def test_missing_ingredient_evidence_stays_unknown(self):
        report = analyze(None, freshness="date-unknown")
        self.assertEqual(report["parserStatus"], "unknown")
        self.assertIn("ingredients-missing", report["safetyFlags"])
        self.assertIsNone(report["ingredientObservationID"])

    def test_analysis_is_deterministic(self):
        record = ingredient("Water, gelatine, E471")
        self.assertEqual(analyze(record), analyze(copy.deepcopy(record)))


class ExplicitReviewTests(unittest.TestCase):
    def test_positive_review_cannot_be_created_with_open_queue(self):
        report = analyze(ingredient("Water, sugar"))
        with self.assertRaises(MODULE.MethodologyError):
            MODULE.complete_review(
                report=report,
                methodology=METHODOLOGY,
                review_input=review_input("halal-reviewed", report, resolve=False),
            )

    def test_explicit_human_review_can_create_halal_reviewed_not_certified(self):
        report = analyze(ingredient("Water, sugar"))
        result = MODULE.complete_review(
            report=report,
            methodology=METHODOLOGY,
            review_input=review_input("halal-reviewed", report),
        )
        self.assertEqual(result["assessment"]["status"], "halal-reviewed")
        self.assertEqual(result["assessment"]["certificationIDs"], [])
        self.assertEqual(result["review"]["state"], "approved")
        self.assertEqual(result["reviewArtifact"]["decision"], "halal-reviewed")
        self.assertIn(report["ingredientObservationID"], result["assessment"]["evidenceIDs"])

    def test_not_halal_requires_confirmed_explicit_prohibited_candidate(self):
        clean = analyze(ingredient("Water, sugar"))
        with self.assertRaises(MODULE.MethodologyError):
            MODULE.complete_review(
                report=clean,
                methodology=METHODOLOGY,
                review_input=review_input("not-halal", clean),
            )
        prohibited = analyze(ingredient("Water, porcine gelatine"))
        result = MODULE.complete_review(
            report=prohibited,
            methodology=METHODOLOGY,
            review_input=review_input("not-halal", prohibited),
        )
        self.assertEqual(result["assessment"]["status"], "not-halal")
        self.assertEqual(result["assessment"]["reasons"][0]["severity"], "prohibitive")

    def test_empty_certifier_policy_cannot_create_halal_certified(self):
        report = analyze(ingredient("Water, sugar"))
        cert = {
            "id": "hfeu:certification:sha256:" + "4" * 64,
            "gtin": report["gtin"],
            "market": "DE",
            "certifier": "fixture-certifier",
            "scheme": "fixture-scheme",
            "effectiveAt": "2026-01-01T00:00:00Z",
            "expiryAt": "2027-01-01T00:00:00Z",
        }
        with self.assertRaises(MODULE.MethodologyError):
            MODULE.complete_review(
                report=report,
                methodology=METHODOLOGY,
                review_input=review_input("halal-certified", report),
                certifications=[cert],
            )

    def test_positive_review_requires_fresh_formulation_and_no_conflict(self):
        stale = analyze(ingredient("Water, sugar"), freshness="stale")
        with self.assertRaises(MODULE.MethodologyError):
            MODULE.complete_review(
                report=stale,
                methodology=METHODOLOGY,
                review_input=review_input("halal-reviewed", stale),
            )
        conflict = analyze(ingredient("Water, sugar"), conflicts=["source-conflict"])
        with self.assertRaises(MODULE.MethodologyError):
            MODULE.complete_review(
                report=conflict,
                methodology=METHODOLOGY,
                review_input=review_input("halal-reviewed", conflict),
            )


class MigrationTests(unittest.TestCase):
    def test_methodology_and_formulation_changes_invalidate_current_assessment(self):
        assessment_id = "hfeu:assessment:sha256:" + "5" * 64
        old_ingredient_id = "hfeu:ingredient:sha256:" + "6" * 64
        new_ingredient_id = "hfeu:ingredient:sha256:" + "7" * 64
        envelope = {
            "assessments": [{
                "id": assessment_id,
                "status": "halal-reviewed",
                "methodologyVersion": "0.9.0",
                "ingredientObservationID": old_ingredient_id,
                "certificationIDs": [],
            }],
            "ingredients": [{"id": new_ingredient_id, "supersedesID": old_ingredient_id}],
            "currentSelections": [{
                "gtin": "04006381333931",
                "market": "DE",
                "assessmentID": assessment_id,
                "ingredientObservationID": new_ingredient_id,
                "certificationIDs": [],
                "conflictFlags": [],
            }],
        }
        report = MODULE.assessment_migration_report(envelope=envelope, methodology=METHODOLOGY)
        self.assertEqual(report["invalidated"], 1)
        reasons = report["decisions"][0]["reasons"]
        self.assertIn("methodology-version-changed", reasons)
        self.assertIn("selected-formulation-changed", reasons)
        events = MODULE.validity_events_from_migration(report, occurred_at="2026-08-30T12:00:00Z")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], "invalidated")
        self.assertEqual(events[0]["assessmentID"], assessment_id)


if __name__ == "__main__":
    unittest.main()
