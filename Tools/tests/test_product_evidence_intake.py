from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import product_evidence_intake as intake

NOW = datetime(2026, 9, 1, 16, 0, tzinfo=timezone.utc)
SUBMISSION_ID = "hfeu-submission-12345678-1234-1234-1234-123456789abc"
ADMISSION_ID = "hfeu-admission-12345678-1234-1234-1234-123456789abc"
REVIEWER = "github:mimranfaruqi"
GTIN = "00200000000004"


def jpeg(width: int = 1200, height: int = 800, *, entropy_byte: int = 1, metadata: bool = False) -> bytes:
    """Create a bounded marker-valid synthetic JPEG for the stdlib intake parser.

    It is intentionally not used by the Apple decoding smoke test; that test
    creates real CGImage-backed JPEGs on macOS.
    """
    chunks = [b"\xff\xd8"]
    if metadata:
        payload = b"Exif\x00\x00test"
        chunks.append(b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload)
    frame = bytes([8]) + height.to_bytes(2, "big") + width.to_bytes(2, "big") + bytes([1, 1, 0x11, 0])
    chunks.append(b"\xff\xc0" + (len(frame) + 2).to_bytes(2, "big") + frame)
    scan = bytes([1, 1, 0, 0, 63, 0])
    chunks.append(b"\xff\xda" + (len(scan) + 2).to_bytes(2, "big") + scan)
    chunks.append(bytes([entropy_byte & 0x7F or 1, 0x11, 0x22]))
    chunks.append(b"\xff\xd9")
    return b"".join(chunks)


def attachment(file_name: str, purpose: str, data: bytes, *, width: int = 1200, height: int = 800) -> dict:
    return {
        "fileName": file_name,
        "purpose": purpose,
        "mimeType": "image/jpeg",
        "pixelWidth": width,
        "pixelHeight": height,
        "byteSize": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "ownershipState": "user-owned-or-authorized",
        "privacyState": "user-confirmed-package-evidence-only",
        "metadataState": "reencoded-metadata-stripped",
    }


def submission_for(images: dict[str, tuple[str, bytes]], *, issue_type: str = "missing-product") -> dict:
    return {
        "schemaVersion": 1,
        "sourceType": "user-package-evidence",
        "submissionID": SUBMISSION_ID,
        "appVersion": "0.1.0",
        "catalogVersion": "2026.09.0",
        "gtin": GTIN,
        "issueType": issue_type,
        "market": "DE",
        "product": {"name": "Test product", "brand": "Test brand", "quantity": "250 g"},
        "retailer": {"retailer": "Example market", "city": "Koblenz"},
        "observedAt": "2026-09-01T12:00:00Z",
        "attachments": [attachment(name, purpose, data) for name, (purpose, data) in images.items()],
        "consent": {
            "version": "product-evidence-consent-v1",
            "acceptedAt": "2026-09-01T12:05:00Z",
            "ownsOrMaySubmitPhotos": True,
            "packageEvidenceOnly": True,
            "projectMayReviewAndRedact": True,
            "redistributionRequiresReview": True,
            "noGuaranteedCatalogOrHalalOutcome": True,
        },
        "notes": "Package front and ingredient panel photographed today.",
    }


def admission(*, public_staging: bool = True) -> dict:
    return {
        "schemaVersion": 1,
        "submissionID": SUBMISSION_ID,
        "admissionID": ADMISSION_ID,
        "admittedAt": "2026-09-01T13:00:00Z",
        "reviewerID": REVIEWER,
        "privacyReview": {
            "state": "screened",
            "personalInformationRemoved": True,
            "locationMetadataRemoved": True,
            "rawEmailExcluded": True,
        },
        "rightsReview": {
            "ownershipOrPermissionConfirmed": True,
            "projectReviewUseConfirmed": True,
            "publicRepositoryStagingApproved": public_staging,
            "redistributionStatus": "review-required",
        },
        "securityReview": {
            "state": "screened",
            "mailboxArtifactsExcluded": True,
            "unexpectedFilesExcluded": True,
        },
    }


def base_images() -> dict[str, tuple[str, bytes]]:
    return {
        "barcode-1.jpg": ("barcode", jpeg(entropy_byte=1)),
        "front-1.jpg": ("front", jpeg(entropy_byte=2)),
        "ingredients-1.jpg": ("ingredients", jpeg(entropy_byte=3)),
    }


class PackageFixture:
    def __init__(self, root: Path, *, images=None, public_staging=True, issue_type="missing-product"):
        self.path = root / "package"
        self.path.mkdir(parents=True)
        self.images = images or base_images()
        self.submission = submission_for(self.images, issue_type=issue_type)
        self.admission = admission(public_staging=public_staging)
        (self.path / "submission.json").write_text(json.dumps(self.submission), encoding="utf-8")
        (self.path / "admission.json").write_text(json.dumps(self.admission), encoding="utf-8")
        for name, (_, data) in self.images.items():
            (self.path / name).write_bytes(data)

    def rewrite(self):
        (self.path / "submission.json").write_text(json.dumps(self.submission), encoding="utf-8")
        (self.path / "admission.json").write_text(json.dumps(self.admission), encoding="utf-8")


class ProductEvidenceIntakePackageTests(unittest.TestCase):
    def test_valid_owner_admitted_public_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PackageFixture(Path(tmp))
            report = intake.validate_package_directory(
                fixture.path,
                now=NOW,
                expected_reviewer=REVIEWER,
                registry={"schemaVersion": 1, "entries": []},
            )
        self.assertEqual(report["processingMode"], "trusted-public-workflow")
        self.assertEqual(report["gtin"], GTIN)
        self.assertEqual({a["purpose"] for a in report["attachments"]}, {"barcode", "front", "ingredients"})

    def test_public_workflow_requires_explicit_public_staging_but_local_mode_does_not(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PackageFixture(Path(tmp), public_staging=False)
            with self.assertRaisesRegex(intake.IntakeValidationError, "public staging approval"):
                intake.validate_package_directory(fixture.path, now=NOW, expected_reviewer=REVIEWER)
            report = intake.validate_package_directory(
                fixture.path,
                now=NOW,
                expected_reviewer=REVIEWER,
                require_public_staging=False,
            )
        self.assertEqual(report["processingMode"], "local-private")

    def test_reviewer_identity_is_bound_to_authenticated_actor(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PackageFixture(Path(tmp))
            with self.assertRaisesRegex(intake.IntakeValidationError, "authenticated workflow reviewer"):
                intake.validate_package_directory(fixture.path, now=NOW, expected_reviewer="github:someone-else")

    def test_gtin_future_date_and_consent_fail_closed(self):
        raw = submission_for(base_images())
        bad_gtin = copy.deepcopy(raw)
        bad_gtin["gtin"] = "00200000000005"
        with self.assertRaisesRegex(intake.IntakeValidationError, "check digit"):
            intake.validate_submission(bad_gtin, now=NOW)
        future = copy.deepcopy(raw)
        future["observedAt"] = "2026-09-02T00:00:00Z"
        with self.assertRaisesRegex(intake.IntakeValidationError, "future"):
            intake.validate_submission(future, now=NOW)
        no_consent = copy.deepcopy(raw)
        no_consent["consent"]["packageEvidenceOnly"] = False
        with self.assertRaisesRegex(intake.IntakeValidationError, "explicitly true"):
            intake.validate_submission(no_consent, now=NOW)

    def test_owner_screening_is_backstopped_for_obvious_contact_data(self):
        raw = submission_for(base_images())
        raw["notes"] = "Please contact person@example.test"
        with self.assertRaisesRegex(intake.IntakeValidationError, "personal/contact"):
            intake.validate_submission(raw, now=NOW)
        raw = submission_for(base_images())
        raw["retailer"]["store"] = "+49 123 456 789"
        with self.assertRaisesRegex(intake.IntakeValidationError, "personal/contact"):
            intake.validate_submission(raw, now=NOW)

    def test_path_traversal_and_unexpected_files_are_rejected(self):
        raw = submission_for(base_images())
        raw["attachments"][0]["fileName"] = "../barcode-1.jpg"
        with self.assertRaisesRegex(intake.IntakeValidationError, "stable purpose"):
            intake.validate_submission(raw, now=NOW)
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PackageFixture(Path(tmp))
            (fixture.path / "raw-email.eml").write_text("private", encoding="utf-8")
            with self.assertRaisesRegex(intake.IntakeValidationError, "unexpected files"):
                intake.validate_package_directory(fixture.path, now=NOW)

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink support required")
    def test_symlinked_root_and_nested_content_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = PackageFixture(root)
            link = root / "linked-package"
            os.symlink(fixture.path, link)
            with self.assertRaisesRegex(intake.IntakeValidationError, "symlinks are forbidden"):
                intake.validate_package_directory(link, now=NOW)
            nested = fixture.path / "nested"
            nested.mkdir()
            with self.assertRaisesRegex(intake.IntakeValidationError, "non-regular files"):
                intake.validate_package_directory(fixture.path, now=NOW)

    def test_hash_size_dimension_and_polyglot_mismatches_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PackageFixture(Path(tmp))
            (fixture.path / "front-1.jpg").write_bytes(jpeg(entropy_byte=99))
            with self.assertRaisesRegex(intake.IntakeValidationError, "SHA-256"):
                intake.validate_package_directory(fixture.path, now=NOW)
        with tempfile.TemporaryDirectory() as tmp:
            images = base_images()
            images["front-1.jpg"] = ("front", images["front-1.jpg"][1] + b"PK\x03\x04")
            fixture = PackageFixture(Path(tmp), images=images)
            with self.assertRaisesRegex(intake.IntakeValidationError, "trailing payload"):
                intake.validate_package_directory(fixture.path, now=NOW)
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PackageFixture(Path(tmp))
            fixture.submission["attachments"][0]["pixelWidth"] = 1199
            fixture.rewrite()
            with self.assertRaisesRegex(intake.IntakeValidationError, "dimensions do not match"):
                intake.validate_package_directory(fixture.path, now=NOW)

    def test_tiny_and_duplicate_images_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            images = base_images()
            tiny = jpeg(width=600, height=300, entropy_byte=44)
            images["front-1.jpg"] = ("front", tiny)
            fixture = PackageFixture(Path(tmp), images=images)
            front = next(item for item in fixture.submission["attachments"] if item["fileName"] == "front-1.jpg")
            front["pixelWidth"] = 600
            front["pixelHeight"] = 300
            fixture.rewrite()
            with self.assertRaisesRegex(intake.IntakeValidationError, "too small"):
                intake.validate_package_directory(fixture.path, now=NOW)
        with tempfile.TemporaryDirectory() as tmp:
            images = base_images()
            images["front-1.jpg"] = ("front", images["barcode-1.jpg"][1])
            fixture = PackageFixture(Path(tmp), images=images)
            with self.assertRaisesRegex(intake.IntakeValidationError, "duplicates another submitted attachment"):
                intake.validate_package_directory(fixture.path, now=NOW)

    def test_registry_blocks_reused_submission_and_input_hashes(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = PackageFixture(Path(tmp))
            one_hash = fixture.submission["attachments"][0]["sha256"]
            entry = {
                "submissionID": "hfeu-submission-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "admissionID": "hfeu-admission-aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "reviewedAt": "2026-08-31T12:00:00Z",
                "inputAttachmentHashes": [one_hash],
                "admittedAttachmentHashes": ["f" * 64],
                "proposalDigest": "e" * 64,
            }
            with self.assertRaisesRegex(intake.IntakeValidationError, "submitted attachment hash already admitted"):
                intake.validate_package_directory(fixture.path, now=NOW, registry={"schemaVersion": 1, "entries": [entry]})


class ProductEvidenceIntakeReviewTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.fixture = PackageFixture(Path(tmp.name))
        self.validation = intake.validate_package_directory(self.fixture.path, now=NOW, expected_reviewer=REVIEWER)
        self.ocr = self._ocr_report()
        self.ocr = intake.validate_ocr_report(self.ocr, validation_report=self.validation)

    def _ocr_report(self):
        items = []
        for index, item in enumerate(self.validation["attachments"], start=1):
            is_ingredients = item["purpose"] == "ingredients"
            items.append({
                "fileName": item["fileName"],
                "purpose": item["purpose"],
                "inputSha256": item["sha256"],
                "sanitizedSha256": hashlib.sha256(("sanitized-" + str(index)).encode()).hexdigest(),
                "sanitizedByteSize": 1024 + index,
                "pixelWidth": item["pixelWidth"],
                "pixelHeight": item["pixelHeight"],
                "ocrState": "recognized" if is_ingredients else "not-requested",
                "recognitionLanguages": ["de-DE", "en-US"] if is_ingredients else [],
                "lines": ([{"text": "Zutaten: Wasser, Zucker", "confidence": 0.92, "boundingBox": [0.1, 0.2, 0.8, 0.1]}] if is_ingredients else []),
            })
        return {
            "schemaVersion": 1,
            "submissionID": SUBMISSION_ID,
            "admissionID": ADMISSION_ID,
            "engine": "apple-vision-local",
            "engineVersion": "Vision revision 3 / macOS synthetic",
            "generatedAt": "2026-09-01T14:00:00Z",
            "verificationState": "unverified",
            "attachments": items,
        }

    def _human_review(self, *, decision="propose-observation"):
        return {
            "schemaVersion": 1,
            "submissionID": SUBMISSION_ID,
            "admissionID": ADMISSION_ID,
            "reviewedAt": "2026-09-01T15:00:00Z",
            "reviewerID": REVIEWER,
            "decision": decision,
            "confirmations": {
                "barcodeMatchesGTIN": True,
                "packageIdentityReviewed": True,
                "marketReviewed": True,
                "ingredientsReviewedWhenPresent": True,
                "privacyRechecked": True,
                "rightsRechecked": True,
            },
            "reviewReason": "Exact package panels were compared manually.",
            "verifiedProduct": {"name": "Test product", "brand": "Test brand", "quantity": "250 g"},
            "ingredientTranscription": {
                "text": "Wasser, Zucker",
                "languageCode": "de",
                "attachmentFiles": ["ingredients-1.jpg"],
            },
            "conflictState": "current-formulation-unknown",
        }

    def test_ocr_is_ingredient_only_and_unverified(self):
        request = intake.build_ocr_request(self.validation)
        self.assertEqual(request["languageHints"], ["de-DE", "en-US"])
        bad = self._ocr_report()
        bad["verificationState"] = "human-verified"
        with self.assertRaisesRegex(intake.IntakeValidationError, "must remain unverified"):
            intake.validate_ocr_report(bad, validation_report=self.validation)
        bad = self._ocr_report()
        bad["attachments"][0]["ocrState"] = "recognized"
        bad["attachments"][0]["lines"] = [{"text": "barcode", "confidence": 1, "boundingBox": [0, 0, 1, 1]}]
        with self.assertRaisesRegex(intake.IntakeValidationError, "ingredient-panel"):
            intake.validate_ocr_report(bad, validation_report=self.validation)

    def test_unreadable_ocr_cannot_invent_lines(self):
        bad = self._ocr_report()
        ingredient = next(a for a in bad["attachments"] if a["purpose"] == "ingredients")
        ingredient["ocrState"] = "unreadable"
        with self.assertRaisesRegex(intake.IntakeValidationError, "cannot contain invented text"):
            intake.validate_ocr_report(bad, validation_report=self.validation)

    def test_human_review_is_actor_bound_and_requires_every_checkpoint(self):
        review = self._human_review()
        review["reviewerID"] = "github:other"
        with self.assertRaisesRegex(intake.IntakeValidationError, "authenticated workflow reviewer"):
            intake.validate_human_review(review, validation_report=self.validation, ocr_report=self.ocr, now=NOW, expected_reviewer=REVIEWER)
        review = self._human_review()
        review["confirmations"]["privacyRechecked"] = False
        with self.assertRaisesRegex(intake.IntakeValidationError, "explicitly true"):
            intake.validate_human_review(review, validation_report=self.validation, ocr_report=self.ocr, now=NOW, expected_reviewer=REVIEWER)

    def test_human_transcription_must_cover_every_ingredient_panel_and_exclude_contact_data(self):
        review = self._human_review()
        review["ingredientTranscription"]["attachmentFiles"] = []
        with self.assertRaisesRegex(intake.IntakeValidationError, "every and only"):
            intake.validate_human_review(review, validation_report=self.validation, ocr_report=self.ocr, now=NOW, expected_reviewer=REVIEWER)
        review = self._human_review()
        review["sanitizedSubmitterNotes"] = "email me at person@example.test"
        with self.assertRaisesRegex(intake.IntakeValidationError, "identity-removed"):
            intake.validate_human_review(review, validation_report=self.validation, ocr_report=self.ocr, now=NOW, expected_reviewer=REVIEWER)

    def test_proposal_contains_only_human_verified_evidence_and_no_halal_decision_or_image_bytes(self):
        review = intake.validate_human_review(self._human_review(), validation_report=self.validation, ocr_report=self.ocr, now=NOW, expected_reviewer=REVIEWER)
        proposal = intake.build_proposal(validation_report=self.validation, ocr_report=self.ocr, human_review=review)
        serialized = json.dumps(proposal, sort_keys=True)
        self.assertNotIn("halal-certified", serialized)
        self.assertNotIn("halal-reviewed", serialized)
        self.assertNotIn("senderEmail", serialized)
        self.assertNotIn("imageBytes", serialized)
        self.assertTrue(proposal["assessmentImpact"]["automaticHalalDecisionForbidden"])
        self.assertTrue(proposal["assessmentImpact"]["mustRouteToMethodologyReview"])
        self.assertFalse(proposal["retention"]["catalogStoresImageBytes"])
        self.assertEqual(proposal["records"]["ingredients"][0]["verificationState"], "human-verified")
        self.assertEqual(proposal["records"]["ingredients"][0]["captureMethod"], "package-transcription")
        self.assertEqual(len(proposal["registryEntry"]["inputAttachmentHashes"]), 3)
        self.assertEqual(len(proposal["registryEntry"]["admittedAttachmentHashes"]), 3)
        # On a real repository checkout this imports evidence_model_core and
        # validates deterministic IDs/record compatibility end to end.
        intake.validate_proposal_records(proposal)

    def test_patch_bundle_contains_only_reviewed_non_image_git_inputs(self):
        review = intake.validate_human_review(self._human_review(), validation_report=self.validation, ocr_report=self.ocr, now=NOW, expected_reviewer=REVIEWER)
        proposal = intake.build_proposal(validation_report=self.validation, ocr_report=self.ocr, human_review=review)
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "patch"
            manifest = intake.materialize_patch_bundle(
                proposal=proposal,
                registry={"schemaVersion": 1, "entries": []},
                output_dir=output,
            )
            self.assertFalse(manifest["containsImageBytes"])
            self.assertTrue(manifest["requiresReviewedGitPR"])
            admitted = output / "Data/submissions/admitted" / f"{SUBMISSION_ID}.json"
            registry = output / "Data/submissions/admitted-submission-registry-v1.json"
            self.assertTrue(admitted.is_file())
            self.assertTrue(registry.is_file())
            all_bytes = b"".join(path.read_bytes() for path in output.rglob("*") if path.is_file())
            self.assertNotIn(b"\xff\xd8", all_bytes)
            self.assertNotIn(b"person@example", all_bytes)

    def test_correction_marks_current_assessment_for_invalidation(self):
        self.validation["submission"]["issueType"] = "ingredients-correction"
        self.validation["issueType"] = "ingredients-correction"
        self.validation["submission"]["currentCatalogEvidence"] = {"catalogVersion": "2026.09.0", "contentHash": "a" * 64}
        review = intake.validate_human_review(self._human_review(), validation_report=self.validation, ocr_report=self.ocr, now=NOW, expected_reviewer=REVIEWER)
        proposal = intake.build_proposal(validation_report=self.validation, ocr_report=self.ocr, human_review=review)
        self.assertTrue(proposal["assessmentImpact"]["mustInvalidateCurrentAssessment"])
        self.assertTrue(proposal["assessmentImpact"]["mustRouteToMethodologyReview"])
        self.assertEqual(proposal["assessmentImpact"]["reasonCode"], "USER_PACKAGE_EVIDENCE_CORRECTION")

    def test_rejection_is_truthful_non_evidence_audit_and_never_gets_a_patch(self):
        review = self._human_review(decision="reject")
        review.pop("verifiedProduct")
        review.pop("ingredientTranscription")
        validated = intake.validate_human_review(review, validation_report=self.validation, ocr_report=self.ocr, now=NOW, expected_reviewer=REVIEWER)
        result = intake.finalize_review_decision(validation_report=self.validation, ocr_report=self.ocr, human_review=validated)
        self.assertEqual(result["resultType"], "rejected-product-evidence")
        self.assertFalse(result["catalogEvidenceCreated"])
        self.assertFalse(result["gitPatchCreated"])
        self.assertEqual(result["rawRepositoryRetentionDays"], 0)
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(intake.IntakeValidationError, "missing required fields|unsupported proposal"):
                intake.materialize_patch_bundle(proposal=result, registry={"schemaVersion": 1, "entries": []}, output_dir=Path(tmp) / "patch")

    def test_rejected_review_cannot_finalize(self):
        review = self._human_review(decision="reject")
        review.pop("verifiedProduct")
        review.pop("ingredientTranscription")
        validated = intake.validate_human_review(review, validation_report=self.validation, ocr_report=self.ocr, now=NOW, expected_reviewer=REVIEWER)
        with self.assertRaisesRegex(intake.IntakeValidationError, "rejected submissions"):
            intake.build_proposal(validation_report=self.validation, ocr_report=self.ocr, human_review=validated)

    def test_trusted_workflow_is_manual_read_only_and_short_retention(self):
        workflow = (ROOT / ".github/workflows/product-evidence-intake.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("\n  push:", workflow)
        self.assertIn("permissions:\n  contents: read", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertIn("if: github.ref == 'refs/heads/main'", workflow)
        self.assertIn("--expected-reviewer \"$EXPECTED_REVIEWER\"", workflow)
        self.assertIn("retention-days: 7", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("git push", workflow)
        self.assertIn("make-patch", workflow)

    def test_schema_and_registry_contracts_are_closed_and_empty_registry_is_valid(self):
        schema_dir = ROOT / "Data/submissions"
        # Tests are also runnable from the draft workspace before publication.
        if not schema_dir.exists():
            schema_dir = Path(__file__).resolve().parents[2] / "Data/submissions"
        names = [
            "product-evidence-admission-v1.schema.json",
            "product-evidence-ocr-request-v1.schema.json",
            "product-evidence-ocr-report-v1.schema.json",
            "product-evidence-human-review-v1.schema.json",
            "product-evidence-proposal-v1.schema.json",
            "product-evidence-rejection-v1.schema.json",
            "product-evidence-patch-manifest-v1.schema.json",
            "admitted-submission-registry-v1.schema.json",
        ]
        for name in names:
            raw = json.loads((schema_dir / name).read_text(encoding="utf-8"))
            self.assertFalse(raw["additionalProperties"], name)
        registry = json.loads((schema_dir / "admitted-submission-registry-v1.json").read_text(encoding="utf-8"))
        self.assertEqual(intake.validate_registry(registry), {"schemaVersion": 1, "entries": []})


if __name__ == "__main__":
    unittest.main()
