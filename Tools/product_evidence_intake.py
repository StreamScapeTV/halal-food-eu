#!/usr/bin/env python3
"""Validate owner-admitted product-evidence packages and materialize review proposals.

The tool is intentionally stdlib-only and never performs network access.  Raw
mailbox material is out of scope.  Input image bytes are treated as hostile;
the production workflow independently decodes/re-encodes them before OCR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
SUBMISSION_ID_RE = re.compile(r"^hfeu-submission-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
ADMISSION_ID_RE = re.compile(r"^hfeu-admission-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
REVIEWER_ID_RE = re.compile(r"^github:[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MARKET_RE = re.compile(r"^[A-Z]{2}$")
LANGUAGE_RE = re.compile(r"^(?:und|[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*)$")
ATTACHMENT_NAME_RE = re.compile(r"^(barcode|front|ingredients|certification|nutrition)-([1-9][0-9]*)\.jpg$")
SAFE_TEXT_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
EMAIL_LIKE_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
URL_LIKE_RE = re.compile(r"https?://", re.IGNORECASE)
LONG_PHONE_LIKE_RE = re.compile(r"(?<!\d)(?:\+?\d[ .()-]?){8,}\d(?!\d)")

ISSUE_TYPES = {
    "missing-product",
    "ingredients-correction",
    "identity-correction",
    "status-certification-correction",
}
PURPOSES = {"barcode", "front", "ingredients", "certification", "nutrition"}
REQUIRED_PURPOSES = {
    "missing-product": {"barcode", "front", "ingredients"},
    "ingredients-correction": {"barcode", "ingredients"},
    "identity-correction": {"barcode", "front"},
    "status-certification-correction": {"barcode", "front"},
}
MAX_JSON_BYTES = 1_000_000
MAX_ATTACHMENT_COUNT = 8
MAX_ATTACHMENT_BYTES = 4_000_000
MAX_TOTAL_ATTACHMENT_BYTES = 18_000_000
MAX_DIMENSION = 2_400
MAX_DECODED_PIXELS = 12_000_000
MIN_LONG_DIMENSION = 800
MIN_SHORT_DIMENSION = 400


class IntakeValidationError(ValueError):
    """Raised when an intake package fails a fail-closed contract check."""


def fail(path: str, message: str) -> None:
    raise IntakeValidationError(f"{path}: {message}")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def derive_evidence_id(kind: str, record: dict[str, Any]) -> str:
    value = dict(record)
    value.pop("id", None)
    digest = sha256_text(canonical_json(value))
    return f"hfeu:{kind}:sha256:{digest}"


def formulation_hash(ingredients_text: str, allergens_text: str | None = None, traces_text: str | None = None) -> str:
    return sha256_text(
        canonical_json(
            {
                "ingredientsText": ingredients_text,
                "allergensText": allergens_text,
                "tracesText": traces_text,
            }
        )
    )


def _object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise IntakeValidationError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def load_json(path: Path, *, max_bytes: int = MAX_JSON_BYTES) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as error:
        raise IntakeValidationError(f"{path}: cannot read file: {error}") from error
    if len(raw) > max_bytes:
        fail(str(path), f"JSON file exceeds {max_bytes} bytes")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_object_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise IntakeValidationError(f"{path}: invalid UTF-8 JSON: {error}") from error
    if not isinstance(value, dict):
        fail(str(path), "must contain a JSON object")
    return value


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        fail(path, "must be an object")
    return value


def _require_array(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        fail(path, "must be an array")
    return value


def _require_string(value: Any, path: str, *, max_length: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        fail(path, "must be a string")
    if not allow_empty and not value.strip():
        fail(path, "must not be blank")
    if len(value) > max_length:
        fail(path, f"exceeds {max_length} characters")
    if SAFE_TEXT_CONTROL_RE.search(value):
        fail(path, "contains prohibited control characters")
    return value


def _closed_shape(value: Any, path: str, required: Iterable[str], optional: Iterable[str] = ()) -> dict[str, Any]:
    obj = _require_object(value, path)
    required_set = set(required)
    optional_set = set(optional)
    missing = sorted(required_set - set(obj))
    unknown = sorted(set(obj) - required_set - optional_set)
    if missing:
        fail(path, f"missing required fields {missing}")
    if unknown:
        fail(path, f"unknown fields {unknown}")
    return obj


def parse_timestamp(value: Any, path: str) -> datetime:
    text = _require_string(value, path, max_length=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise IntakeValidationError(f"{path}: invalid ISO-8601 timestamp") from error
    if parsed.tzinfo is None:
        fail(path, "timestamp must include an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _valid_gtin_check_digit(value: str) -> bool:
    total = 0
    for offset, character in enumerate(reversed(value[:-1])):
        total += int(character) * (3 if offset % 2 == 0 else 1)
    return (10 - total % 10) % 10 == int(value[-1])


def validate_gtin(value: Any, path: str) -> str:
    gtin = _require_string(value, path, max_length=14)
    if len(gtin) != 14 or not gtin.isascii() or not gtin.isdigit():
        fail(path, "must be the canonical 14-digit GTIN")
    if not _valid_gtin_check_digit(gtin):
        fail(path, "has an invalid GTIN check digit")
    return gtin


def _validate_optional_text_fields(obj: dict[str, Any], path: str, limits: dict[str, int]) -> None:
    for key, limit in limits.items():
        if key in obj:
            _require_string(obj[key], f"{path}.{key}", max_length=limit)


def validate_submission(raw: Any, *, now: datetime) -> dict[str, Any]:
    submission = _closed_shape(
        raw,
        "submission",
        {
            "schemaVersion", "sourceType", "submissionID", "appVersion", "catalogVersion", "gtin",
            "issueType", "market", "product", "retailer", "observedAt", "attachments", "consent",
        },
        {"currentCatalogEvidence", "notes"},
    )
    if submission["schemaVersion"] != 1:
        fail("submission.schemaVersion", "must equal 1")
    if submission["sourceType"] != "user-package-evidence":
        fail("submission.sourceType", "must equal 'user-package-evidence'")
    submission_id = _require_string(submission["submissionID"], "submission.submissionID", max_length=60)
    if not SUBMISSION_ID_RE.fullmatch(submission_id):
        fail("submission.submissionID", "invalid stable submission identifier")
    _require_string(submission["appVersion"], "submission.appVersion", max_length=80)
    _require_string(submission["catalogVersion"], "submission.catalogVersion", max_length=80)
    validate_gtin(submission["gtin"], "submission.gtin")
    issue_type = _require_string(submission["issueType"], "submission.issueType", max_length=64)
    if issue_type not in ISSUE_TYPES:
        fail("submission.issueType", f"unsupported issue type {issue_type!r}")
    market = _require_string(submission["market"], "submission.market", max_length=2)
    if not MARKET_RE.fullmatch(market):
        fail("submission.market", "must be an uppercase alpha-2 market")
    product = _closed_shape(submission["product"], "submission.product", set(), {"name", "brand", "quantity"})
    _validate_optional_text_fields(product, "submission.product", {"name": 200, "brand": 160, "quantity": 80})
    for key, value in product.items():
        if _contains_pii_like_text(value):
            fail(f"submission.product.{key}", "appears to contain unrelated personal/contact data")
    retailer = _closed_shape(submission["retailer"], "submission.retailer", set(), {"retailer", "city", "store"})
    _validate_optional_text_fields(retailer, "submission.retailer", {"retailer": 120, "city": 120, "store": 160})
    for key, value in retailer.items():
        if _contains_pii_like_text(value):
            fail(f"submission.retailer.{key}", "appears to contain unrelated personal/contact data")
    observed_at = parse_timestamp(submission["observedAt"], "submission.observedAt")
    if observed_at > now:
        fail("submission.observedAt", "cannot be in the future")
    if "currentCatalogEvidence" in submission:
        context = _closed_shape(
            submission["currentCatalogEvidence"], "submission.currentCatalogEvidence", {"catalogVersion"},
            {"sourceName", "sourceKind", "sourceReference", "observedAt", "retrievedAt", "contentHash"},
        )
        _require_string(context["catalogVersion"], "submission.currentCatalogEvidence.catalogVersion", max_length=80)
        _validate_optional_text_fields(
            context,
            "submission.currentCatalogEvidence",
            {"sourceName": 200, "sourceKind": 120, "sourceReference": 1000},
        )
        for key in ("observedAt", "retrievedAt"):
            if key in context:
                parse_timestamp(context[key], f"submission.currentCatalogEvidence.{key}")
        if "contentHash" in context and not SHA256_RE.fullmatch(str(context["contentHash"])):
            fail("submission.currentCatalogEvidence.contentHash", "must be a lowercase SHA-256 digest")
    attachments = _require_array(submission["attachments"], "submission.attachments")
    if not 1 <= len(attachments) <= MAX_ATTACHMENT_COUNT:
        fail("submission.attachments", f"must contain 1..{MAX_ATTACHMENT_COUNT} entries")
    seen_names: set[str] = set()
    seen_purposes: set[str] = set()
    total_declared = 0
    for index, raw_attachment in enumerate(attachments):
        path = f"submission.attachments[{index}]"
        attachment = _closed_shape(
            raw_attachment, path,
            {"fileName", "purpose", "mimeType", "pixelWidth", "pixelHeight", "byteSize", "sha256",
             "ownershipState", "privacyState", "metadataState"},
        )
        file_name = _require_string(attachment["fileName"], f"{path}.fileName", max_length=80)
        match = ATTACHMENT_NAME_RE.fullmatch(file_name)
        if not match:
            fail(f"{path}.fileName", "must use the stable purpose-N.jpg filename contract")
        if file_name in seen_names:
            fail(f"{path}.fileName", "duplicate attachment filename")
        seen_names.add(file_name)
        purpose = _require_string(attachment["purpose"], f"{path}.purpose", max_length=32)
        if purpose not in PURPOSES or purpose != match.group(1):
            fail(f"{path}.purpose", "does not match the filename purpose")
        seen_purposes.add(purpose)
        if attachment["mimeType"] != "image/jpeg":
            fail(f"{path}.mimeType", "only image/jpeg is allowed")
        for key in ("pixelWidth", "pixelHeight", "byteSize"):
            if not isinstance(attachment[key], int) or isinstance(attachment[key], bool):
                fail(f"{path}.{key}", "must be an integer")
        width = attachment["pixelWidth"]
        height = attachment["pixelHeight"]
        if width < 1 or height < 1 or width > MAX_DIMENSION or height > MAX_DIMENSION:
            fail(path, "declared image dimensions are outside the reviewed bounds")
        if width * height > MAX_DECODED_PIXELS:
            fail(path, "declared decoded pixel count exceeds the reviewed bound")
        if attachment["byteSize"] < 1 or attachment["byteSize"] > MAX_ATTACHMENT_BYTES:
            fail(f"{path}.byteSize", "declared byte size exceeds the reviewed bound")
        total_declared += attachment["byteSize"]
        if not SHA256_RE.fullmatch(str(attachment["sha256"])):
            fail(f"{path}.sha256", "must be a lowercase SHA-256 digest")
        if attachment["ownershipState"] != "user-owned-or-authorized":
            fail(f"{path}.ownershipState", "unexpected ownership state")
        if attachment["privacyState"] != "user-confirmed-package-evidence-only":
            fail(f"{path}.privacyState", "unexpected privacy declaration")
        if attachment["metadataState"] != "reencoded-metadata-stripped":
            fail(f"{path}.metadataState", "unexpected metadata state")
    if total_declared > MAX_TOTAL_ATTACHMENT_BYTES:
        fail("submission.attachments", "declared total attachment size exceeds the reviewed bound")
    missing_purposes = sorted(REQUIRED_PURPOSES[issue_type] - seen_purposes)
    if missing_purposes:
        fail("submission.attachments", f"missing required photo purposes {missing_purposes}")
    consent = _closed_shape(
        submission["consent"], "submission.consent",
        {"version", "acceptedAt", "ownsOrMaySubmitPhotos", "packageEvidenceOnly", "projectMayReviewAndRedact",
         "redistributionRequiresReview", "noGuaranteedCatalogOrHalalOutcome"},
    )
    if consent["version"] != "product-evidence-consent-v1":
        fail("submission.consent.version", "unsupported consent version")
    accepted_at = parse_timestamp(consent["acceptedAt"], "submission.consent.acceptedAt")
    if accepted_at > now:
        fail("submission.consent.acceptedAt", "cannot be in the future")
    for key in (
        "ownsOrMaySubmitPhotos", "packageEvidenceOnly", "projectMayReviewAndRedact",
        "redistributionRequiresReview", "noGuaranteedCatalogOrHalalOutcome",
    ):
        if consent[key] is not True:
            fail(f"submission.consent.{key}", "must be explicitly true")
    if "notes" in submission:
        notes = _require_string(submission["notes"], "submission.notes", max_length=2000)
        if _contains_pii_like_text(notes):
            fail("submission.notes", "appears to contain unrelated personal/contact data")
    return submission


def validate_admission(
    raw: Any,
    *,
    submission: dict[str, Any],
    now: datetime,
    expected_reviewer: str | None,
    require_public_staging: bool = True,
) -> dict[str, Any]:
    admission = _closed_shape(
        raw,
        "admission",
        {"schemaVersion", "submissionID", "admissionID", "admittedAt", "reviewerID", "privacyReview", "rightsReview", "securityReview"},
    )
    if admission["schemaVersion"] != 1:
        fail("admission.schemaVersion", "must equal 1")
    if admission["submissionID"] != submission["submissionID"]:
        fail("admission.submissionID", "must match submission.json")
    admission_id = _require_string(admission["admissionID"], "admission.admissionID", max_length=59)
    if not ADMISSION_ID_RE.fullmatch(admission_id):
        fail("admission.admissionID", "invalid admission identifier")
    admitted_at = parse_timestamp(admission["admittedAt"], "admission.admittedAt")
    if admitted_at > now:
        fail("admission.admittedAt", "cannot be in the future")
    reviewer = _require_string(admission["reviewerID"], "admission.reviewerID", max_length=46)
    if not REVIEWER_ID_RE.fullmatch(reviewer):
        fail("admission.reviewerID", "must be a github:<login> reviewer identifier")
    if expected_reviewer is not None and reviewer != expected_reviewer:
        fail("admission.reviewerID", f"must equal authenticated workflow reviewer {expected_reviewer!r}")
    privacy = _closed_shape(
        admission["privacyReview"], "admission.privacyReview",
        {"state", "personalInformationRemoved", "locationMetadataRemoved", "rawEmailExcluded"},
    )
    if privacy["state"] not in {"screened", "redacted"}:
        fail("admission.privacyReview.state", "must be screened or redacted")
    for key in ("personalInformationRemoved", "locationMetadataRemoved", "rawEmailExcluded"):
        if privacy[key] is not True:
            fail(f"admission.privacyReview.{key}", "must be explicitly true")
    rights = _closed_shape(
        admission["rightsReview"], "admission.rightsReview",
        {"ownershipOrPermissionConfirmed", "projectReviewUseConfirmed", "publicRepositoryStagingApproved", "redistributionStatus"},
    )
    for key in ("ownershipOrPermissionConfirmed", "projectReviewUseConfirmed"):
        if rights[key] is not True:
            fail(f"admission.rightsReview.{key}", "must be explicitly true")
    if not isinstance(rights["publicRepositoryStagingApproved"], bool):
        fail("admission.rightsReview.publicRepositoryStagingApproved", "must be a boolean")
    if require_public_staging and rights["publicRepositoryStagingApproved"] is not True:
        fail(
            "admission.rightsReview.publicRepositoryStagingApproved",
            "trusted public-repository workflow requires explicit public staging approval; otherwise run locally",
        )
    if rights["redistributionStatus"] not in {"review-required", "approved"}:
        fail("admission.rightsReview.redistributionStatus", "unsupported redistribution status")
    security = _closed_shape(
        admission["securityReview"], "admission.securityReview",
        {"state", "mailboxArtifactsExcluded", "unexpectedFilesExcluded"},
    )
    if security["state"] != "screened":
        fail("admission.securityReview.state", "must equal screened")
    for key in ("mailboxArtifactsExcluded", "unexpectedFilesExcluded"):
        if security[key] is not True:
            fail(f"admission.securityReview.{key}", "must be explicitly true")
    return admission


@dataclass(frozen=True)
class JpegInspection:
    width: int
    height: int
    has_exif_or_xmp: bool
    has_comment: bool


def inspect_jpeg(data: bytes, path: str) -> JpegInspection:
    if len(data) < 4 or data[:2] != b"\xff\xd8" or data[-2:] != b"\xff\xd9":
        fail(path, "content is not a bounded JPEG or has trailing payload bytes")
    index = 2
    width: int | None = None
    height: int | None = None
    has_metadata = False
    has_comment = False
    while index < len(data) - 2:
        if data[index] != 0xFF:
            fail(path, "malformed JPEG marker stream before scan data")
        while index < len(data) and data[index] == 0xFF:
            index += 1
        if index >= len(data):
            fail(path, "truncated JPEG marker")
        marker = data[index]
        index += 1
        if marker == 0xD9:
            break
        if marker in {0x01, *range(0xD0, 0xD8)}:
            continue
        if index + 2 > len(data):
            fail(path, "truncated JPEG segment length")
        segment_length = int.from_bytes(data[index:index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            fail(path, "invalid JPEG segment length")
        payload_start = index + 2
        payload_end = index + segment_length
        payload = data[payload_start:payload_end]
        if marker == 0xDA:  # SOS; entropy-coded bytes follow until the final EOI.
            break
        if marker == 0xE1 and (payload.startswith(b"Exif\x00\x00") or b"http://ns.adobe.com/xap/1.0/" in payload):
            has_metadata = True
        if marker == 0xFE:
            has_comment = True
        if marker in {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}:
            if len(payload) < 6:
                fail(path, "truncated JPEG frame header")
            height = int.from_bytes(payload[1:3], "big")
            width = int.from_bytes(payload[3:5], "big")
        index = payload_end
    if width is None or height is None or width <= 0 or height <= 0:
        fail(path, "JPEG dimensions could not be established")
    return JpegInspection(width=width, height=height, has_exif_or_xmp=has_metadata, has_comment=has_comment)


def _reject_symlink_components(path: Path) -> None:
    current = path
    while True:
        try:
            mode = current.lstat().st_mode
        except OSError as error:
            raise IntakeValidationError(f"{current}: cannot stat: {error}") from error
        if stat.S_ISLNK(mode):
            fail(str(current), "symlinks are forbidden in intake paths")
        if current.parent == current:
            break
        current = current.parent


def validate_package_directory(
    package_dir: Path,
    *,
    now: datetime,
    expected_reviewer: str | None = None,
    registry: dict[str, Any] | None = None,
    require_public_staging: bool = True,
) -> dict[str, Any]:
    # Check the path as supplied before resolving it so a symlinked intake root
    # cannot disappear from the audit boundary through Path.resolve().
    supplied_dir = package_dir.absolute()
    _reject_symlink_components(supplied_dir)
    package_dir = supplied_dir.resolve(strict=True)
    if not package_dir.is_dir():
        fail(str(package_dir), "must be a directory")
    submission_path = package_dir / "submission.json"
    admission_path = package_dir / "admission.json"
    for required in (submission_path, admission_path):
        if not required.is_file() or required.is_symlink():
            fail(str(required), "required regular file is missing or unsafe")
    submission = validate_submission(load_json(submission_path), now=now)
    admission = validate_admission(
        load_json(admission_path),
        submission=submission,
        now=now,
        expected_reviewer=expected_reviewer,
        require_public_staging=require_public_staging,
    )
    manifest_by_name = {item["fileName"]: item for item in submission["attachments"]}
    allowed_names = {"submission.json", "admission.json", *manifest_by_name}
    actual_names: set[str] = set()
    with os.scandir(package_dir) as entries:
        for entry in entries:
            actual_names.add(entry.name)
            if entry.is_symlink():
                fail(str(package_dir / entry.name), "symlinks are forbidden")
            if not entry.is_file(follow_symlinks=False):
                fail(str(package_dir / entry.name), "nested directories and non-regular files are forbidden")
    unexpected = sorted(actual_names - allowed_names)
    missing = sorted(allowed_names - actual_names)
    if unexpected:
        fail(str(package_dir), f"unexpected files {unexpected}")
    if missing:
        fail(str(package_dir), f"missing declared files {missing}")
    total_actual = 0
    inspections: list[dict[str, Any]] = []
    sanitized_input_hashes: set[str] = set()
    for file_name in sorted(manifest_by_name):
        manifest = manifest_by_name[file_name]
        image_path = package_dir / file_name
        if image_path.is_symlink() or not image_path.is_file():
            fail(str(image_path), "must be a regular file")
        data = image_path.read_bytes()
        total_actual += len(data)
        if len(data) != manifest["byteSize"]:
            fail(str(image_path), "actual byte size does not match the signed manifest")
        digest = sha256_bytes(data)
        if digest != manifest["sha256"]:
            fail(str(image_path), "actual SHA-256 does not match the manifest")
        inspection = inspect_jpeg(data, str(image_path))
        if (inspection.width, inspection.height) != (manifest["pixelWidth"], manifest["pixelHeight"]):
            fail(str(image_path), "actual JPEG dimensions do not match the manifest")
        if max(inspection.width, inspection.height) < MIN_LONG_DIMENSION or min(inspection.width, inspection.height) < MIN_SHORT_DIMENSION:
            fail(str(image_path), "image is too small for reliable package review")
        if inspection.width > MAX_DIMENSION or inspection.height > MAX_DIMENSION or inspection.width * inspection.height > MAX_DECODED_PIXELS:
            fail(str(image_path), "decoded dimensions exceed the reviewed security bound")
        if digest in sanitized_input_hashes:
            fail(str(image_path), "duplicates another submitted attachment byte-for-byte")
        sanitized_input_hashes.add(digest)
        inspections.append(
            {
                "fileName": file_name,
                "purpose": manifest["purpose"],
                "sha256": digest,
                "byteSize": len(data),
                "pixelWidth": inspection.width,
                "pixelHeight": inspection.height,
                "metadataDetected": inspection.has_exif_or_xmp or inspection.has_comment,
            }
        )
    if total_actual > MAX_TOTAL_ATTACHMENT_BYTES:
        fail(str(package_dir), "actual total attachment bytes exceed the reviewed security bound")
    if registry is not None:
        validate_registry(registry)
        for entry in registry["entries"]:
            if entry["submissionID"] == submission["submissionID"]:
                fail("registry", "submission ID has already been admitted")
            duplicate_hashes = sanitized_input_hashes.intersection(entry["inputAttachmentHashes"])
            if duplicate_hashes:
                fail("registry", f"submitted attachment hash already admitted: {sorted(duplicate_hashes)[0]}")
    return {
        "schemaVersion": 1,
        "status": "valid-owner-admitted-package",
        "submissionID": submission["submissionID"],
        "admissionID": admission["admissionID"],
        "gtin": submission["gtin"],
        "market": submission["market"],
        "issueType": submission["issueType"],
        "processingMode": "trusted-public-workflow" if require_public_staging else "local-private",
        "submission": submission,
        "admission": admission,
        "attachments": inspections,
    }


def validate_registry(raw: Any) -> dict[str, Any]:
    registry = _closed_shape(raw, "registry", {"schemaVersion", "entries"})
    if registry["schemaVersion"] != 1:
        fail("registry.schemaVersion", "must equal 1")
    entries = _require_array(registry["entries"], "registry.entries")
    seen_submission_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for index, raw_entry in enumerate(entries):
        path = f"registry.entries[{index}]"
        entry = _closed_shape(
            raw_entry,
            path,
            {"submissionID", "admissionID", "reviewedAt", "inputAttachmentHashes", "admittedAttachmentHashes", "proposalDigest"},
        )
        if not SUBMISSION_ID_RE.fullmatch(str(entry["submissionID"])):
            fail(f"{path}.submissionID", "invalid submission ID")
        if not ADMISSION_ID_RE.fullmatch(str(entry["admissionID"])):
            fail(f"{path}.admissionID", "invalid admission ID")
        parse_timestamp(entry["reviewedAt"], f"{path}.reviewedAt")
        if entry["submissionID"] in seen_submission_ids:
            fail(path, "duplicate submission ID in registry")
        seen_submission_ids.add(entry["submissionID"])
        entry_hashes: set[str] = set()
        for field in ("inputAttachmentHashes", "admittedAttachmentHashes"):
            hashes = _require_array(entry[field], f"{path}.{field}")
            if not hashes:
                fail(f"{path}.{field}", "must not be empty")
            field_hashes: set[str] = set()
            for hash_index, digest in enumerate(hashes):
                if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                    fail(f"{path}.{field}[{hash_index}]", "invalid SHA-256")
                if digest in field_hashes:
                    fail(f"{path}.{field}[{hash_index}]", "duplicate attachment hash within registry field")
                field_hashes.add(digest)
            # Input and re-encoded hashes may legitimately be identical.  Treat
            # them as one fingerprint for cross-submission duplicate detection.
            entry_hashes.update(field_hashes)
        overlap = seen_hashes.intersection(entry_hashes)
        if overlap:
            fail(path, f"attachment hash is reused by multiple registry entries: {sorted(overlap)[0]}")
        seen_hashes.update(entry_hashes)
        if not isinstance(entry["proposalDigest"], str) or not SHA256_RE.fullmatch(entry["proposalDigest"]):
            fail(f"{path}.proposalDigest", "invalid proposal digest")
    return registry


def build_ocr_request(validation_report: dict[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "submissionID": validation_report["submissionID"],
        "admissionID": validation_report["admissionID"],
        "languageHints": ["de-DE", "en-US"],
        "attachments": [
            {
                "fileName": item["fileName"],
                "purpose": item["purpose"],
                "inputSha256": item["sha256"],
            }
            for item in validation_report["attachments"]
        ],
    }


def validate_ocr_report(raw: Any, *, validation_report: dict[str, Any]) -> dict[str, Any]:
    report = _closed_shape(raw, "ocrReport", {"schemaVersion", "submissionID", "admissionID", "engine", "engineVersion", "generatedAt", "verificationState", "attachments"})
    if report["schemaVersion"] != 1:
        fail("ocrReport.schemaVersion", "must equal 1")
    if report["submissionID"] != validation_report["submissionID"] or report["admissionID"] != validation_report["admissionID"]:
        fail("ocrReport", "submission/admission identity does not match the validated package")
    if report["engine"] != "apple-vision-local":
        fail("ocrReport.engine", "must equal apple-vision-local")
    _require_string(report["engineVersion"], "ocrReport.engineVersion", max_length=200)
    parse_timestamp(report["generatedAt"], "ocrReport.generatedAt")
    if report["verificationState"] != "unverified":
        fail("ocrReport.verificationState", "OCR must remain unverified until human review")
    expected = {item["fileName"]: item for item in validation_report["attachments"]}
    raw_attachments = _require_array(report["attachments"], "ocrReport.attachments")
    if len(raw_attachments) != len(expected):
        fail("ocrReport.attachments", "must report every validated attachment exactly once")
    seen: set[str] = set()
    seen_sanitized_hashes: set[str] = set()
    for index, raw_attachment in enumerate(raw_attachments):
        path = f"ocrReport.attachments[{index}]"
        attachment = _closed_shape(
            raw_attachment, path,
            {"fileName", "purpose", "inputSha256", "sanitizedSha256", "sanitizedByteSize", "pixelWidth", "pixelHeight", "ocrState", "recognitionLanguages", "lines"},
        )
        name = _require_string(attachment["fileName"], f"{path}.fileName", max_length=80)
        if name in seen or name not in expected:
            fail(f"{path}.fileName", "unexpected or duplicate OCR attachment")
        seen.add(name)
        expected_attachment = expected[name]
        if attachment["purpose"] != expected_attachment["purpose"]:
            fail(f"{path}.purpose", "does not match validated package purpose")
        if attachment["inputSha256"] != expected_attachment["sha256"]:
            fail(f"{path}.inputSha256", "does not match validated input bytes")
        if not isinstance(attachment["sanitizedSha256"], str) or not SHA256_RE.fullmatch(attachment["sanitizedSha256"]):
            fail(f"{path}.sanitizedSha256", "invalid re-encoded SHA-256")
        if attachment["sanitizedSha256"] in seen_sanitized_hashes:
            fail(f"{path}.sanitizedSha256", "duplicates another re-encoded attachment")
        seen_sanitized_hashes.add(attachment["sanitizedSha256"])
        if not isinstance(attachment["sanitizedByteSize"], int) or not 1 <= attachment["sanitizedByteSize"] <= MAX_ATTACHMENT_BYTES:
            fail(f"{path}.sanitizedByteSize", "re-encoded bytes exceed the reviewed bound")
        for dimension in ("pixelWidth", "pixelHeight"):
            if not isinstance(attachment[dimension], int) or not 1 <= attachment[dimension] <= MAX_DIMENSION:
                fail(f"{path}.{dimension}", "invalid re-encoded dimension")
        if attachment["pixelWidth"] * attachment["pixelHeight"] > MAX_DECODED_PIXELS:
            fail(path, "re-encoded decoded pixels exceed the reviewed bound")
        if attachment["ocrState"] not in {"not-requested", "recognized", "unreadable"}:
            fail(f"{path}.ocrState", "unsupported OCR state")
        languages = _require_array(attachment["recognitionLanguages"], f"{path}.recognitionLanguages")
        for language_index, language in enumerate(languages):
            if language not in {"de-DE", "en-US"}:
                fail(f"{path}.recognitionLanguages[{language_index}]", "unsupported initial OCR language")
        lines = _require_array(attachment["lines"], f"{path}.lines")
        if attachment["purpose"] != "ingredients" and (attachment["ocrState"] != "not-requested" or lines):
            fail(path, "OCR text may only be produced for ingredient-panel attachments")
        if attachment["purpose"] == "ingredients" and attachment["ocrState"] == "recognized" and not lines:
            fail(path, "recognized OCR state requires at least one line")
        if attachment["ocrState"] == "unreadable" and lines:
            fail(path, "unreadable OCR state cannot contain invented text")
        for line_index, raw_line in enumerate(lines):
            line_path = f"{path}.lines[{line_index}]"
            line = _closed_shape(raw_line, line_path, {"text", "confidence", "boundingBox"})
            _require_string(line["text"], f"{line_path}.text", max_length=2000)
            if not isinstance(line["confidence"], (int, float)) or isinstance(line["confidence"], bool) or not 0 <= float(line["confidence"]) <= 1:
                fail(f"{line_path}.confidence", "must be between 0 and 1")
            box = _require_array(line["boundingBox"], f"{line_path}.boundingBox")
            if len(box) != 4 or any(not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= float(value) <= 1 for value in box):
                fail(f"{line_path}.boundingBox", "must contain four normalized coordinates")
    return report


def _contains_pii_like_text(value: str) -> bool:
    return bool(EMAIL_LIKE_RE.search(value) or URL_LIKE_RE.search(value) or LONG_PHONE_LIKE_RE.search(value))


def validate_human_review(
    raw: Any,
    *,
    validation_report: dict[str, Any],
    ocr_report: dict[str, Any],
    now: datetime,
    expected_reviewer: str | None,
) -> dict[str, Any]:
    review = _closed_shape(
        raw,
        "humanReview",
        {"schemaVersion", "submissionID", "admissionID", "reviewedAt", "reviewerID", "decision", "confirmations", "reviewReason"},
        {"verifiedProduct", "ingredientTranscription", "sanitizedSubmitterNotes", "conflictState"},
    )
    if review["schemaVersion"] != 1:
        fail("humanReview.schemaVersion", "must equal 1")
    if review["submissionID"] != validation_report["submissionID"] or review["admissionID"] != validation_report["admissionID"]:
        fail("humanReview", "submission/admission identity mismatch")
    reviewed_at = parse_timestamp(review["reviewedAt"], "humanReview.reviewedAt")
    admitted_at = parse_timestamp(validation_report["admission"]["admittedAt"], "admission.admittedAt")
    if reviewed_at < admitted_at or reviewed_at > now:
        fail("humanReview.reviewedAt", "must be between admission time and now")
    ocr_generated_at = parse_timestamp(ocr_report["generatedAt"], "ocrReport.generatedAt")
    if ocr_generated_at < admitted_at or ocr_generated_at > reviewed_at:
        fail("ocrReport.generatedAt", "must be between admission and human review")
    reviewer = _require_string(review["reviewerID"], "humanReview.reviewerID", max_length=46)
    if not REVIEWER_ID_RE.fullmatch(reviewer):
        fail("humanReview.reviewerID", "must be a github:<login> reviewer identifier")
    if expected_reviewer is not None and reviewer != expected_reviewer:
        fail("humanReview.reviewerID", f"must equal authenticated workflow reviewer {expected_reviewer!r}")
    if review["decision"] not in {"propose-observation", "reject"}:
        fail("humanReview.decision", "unsupported decision")
    _require_string(review["reviewReason"], "humanReview.reviewReason", max_length=1000)
    if _contains_pii_like_text(review["reviewReason"]):
        fail("humanReview.reviewReason", "must not contain email, URL, or phone-like personal data")
    confirmations = _closed_shape(
        review["confirmations"], "humanReview.confirmations",
        {"barcodeMatchesGTIN", "packageIdentityReviewed", "marketReviewed", "ingredientsReviewedWhenPresent", "privacyRechecked", "rightsRechecked"},
    )
    if review["decision"] == "propose-observation":
        for key, value in confirmations.items():
            if value is not True:
                fail(f"humanReview.confirmations.{key}", "must be explicitly true for proposal admission")
        product = _closed_shape(review.get("verifiedProduct"), "humanReview.verifiedProduct", {"name"}, {"brand", "quantity"})
        _require_string(product["name"], "humanReview.verifiedProduct.name", max_length=200)
        _validate_optional_text_fields(product, "humanReview.verifiedProduct", {"brand": 160, "quantity": 80})
        ingredient_files = {item["fileName"] for item in validation_report["attachments"] if item["purpose"] == "ingredients"}
        if ingredient_files:
            transcription = _closed_shape(
                review.get("ingredientTranscription"), "humanReview.ingredientTranscription",
                {"text", "languageCode", "attachmentFiles"},
            )
            text = _require_string(transcription["text"], "humanReview.ingredientTranscription.text", max_length=8000)
            if _contains_pii_like_text(text):
                fail("humanReview.ingredientTranscription.text", "appears to contain unrelated personal/contact data")
            language = _require_string(transcription["languageCode"], "humanReview.ingredientTranscription.languageCode", max_length=32)
            if not LANGUAGE_RE.fullmatch(language):
                fail("humanReview.ingredientTranscription.languageCode", "invalid language tag")
            source_files = _require_array(transcription["attachmentFiles"], "humanReview.ingredientTranscription.attachmentFiles")
            if set(source_files) != ingredient_files:
                fail("humanReview.ingredientTranscription.attachmentFiles", "must reference every and only ingredient-panel attachment")
        elif "ingredientTranscription" in review:
            fail("humanReview.ingredientTranscription", "cannot transcribe ingredients when no ingredient image was submitted")
        if "sanitizedSubmitterNotes" in review:
            notes = _require_string(review["sanitizedSubmitterNotes"], "humanReview.sanitizedSubmitterNotes", max_length=2000)
            if _contains_pii_like_text(notes):
                fail("humanReview.sanitizedSubmitterNotes", "must contain only identity-removed product context")
        if review.get("conflictState", "none") not in {"none", "conflicts-current-formulation", "current-formulation-unknown"}:
            fail("humanReview.conflictState", "unsupported conflict state")
    return review


def _ocr_by_name(ocr_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["fileName"]: item for item in ocr_report["attachments"]}


def build_proposal(
    *,
    validation_report: dict[str, Any],
    ocr_report: dict[str, Any],
    human_review: dict[str, Any],
) -> dict[str, Any]:
    if human_review["decision"] != "propose-observation":
        fail("humanReview.decision", "rejected submissions cannot materialize catalog evidence proposals")
    submission = validation_report["submission"]
    admission = validation_report["admission"]
    reviewer = human_review["reviewerID"]
    reviewed_at = human_review["reviewedAt"]
    source_key = "user-package-evidence"
    source = {
        "sourceKey": source_key,
        "operator": "Halal Food EU owner-admitted package evidence",
        "sourceClass": "package-photo",
        "reference": f"submission:{submission['submissionID']}",
        "accessMethod": "package",
        "markets": [submission["market"]],
        "retrievedAt": admission["admittedAt"],
        "sourceRevision": admission["admissionID"],
    }
    ocr_by_name = _ocr_by_name(ocr_report)
    package_records: list[dict[str, Any]] = []
    review_records: list[dict[str, Any]] = []
    for manifest in submission["attachments"]:
        ocr = ocr_by_name[manifest["fileName"]]
        package_record = {
            "gtin": submission["gtin"],
            "market": submission["market"],
            "purpose": manifest["purpose"],
            "sha256": ocr["sanitizedSha256"],
            "observedAt": submission["observedAt"],
            "consentState": "recorded",
            "privacyState": admission["privacyReview"]["state"],
            "verificationState": "human-verified",
            "internalReference": f"{submission['submissionID']}/{manifest['fileName']}@{ocr['sanitizedSha256']}",
            "redactionState": "owner-screened-and-workflow-reencoded",
        }
        package_record["id"] = derive_evidence_id("package-evidence", package_record)
        package_records.append(package_record)
        review_record = {
            "targetID": package_record["id"],
            "targetType": "package-evidence",
            "state": "approved",
            "reviewerID": reviewer,
            "reviewedAt": reviewed_at,
            "decisionCode": "owner-admitted-sanitized-package-evidence",
            "reason": human_review["reviewReason"],
            "toolContext": "product-evidence-intake-v1",
        }
        review_record["id"] = derive_evidence_id("review", review_record)
        review_records.append(review_record)
    product = human_review["verifiedProduct"]
    identity_record = {
        "gtin": submission["gtin"],
        "originalBarcode": submission["gtin"],
        "market": submission["market"],
        "sourceKey": source_key,
        "sourceRecordID": f"{submission['submissionID']}:identity",
        "sourceRevision": admission["admissionID"],
        "name": product["name"],
        "observedAt": submission["observedAt"],
        "retrievedAt": admission["admittedAt"],
        "confidence": "high",
    }
    for key in ("brand", "quantity"):
        if key in product:
            identity_record[key] = product[key]
    identity_record["id"] = derive_evidence_id("identity", identity_record)
    identity_review = {
        "targetID": identity_record["id"],
        "targetType": "identity",
        "state": "approved",
        "reviewerID": reviewer,
        "reviewedAt": reviewed_at,
        "decisionCode": "human-verified-package-identity",
        "reason": human_review["reviewReason"],
        "toolContext": "product-evidence-intake-v1",
    }
    identity_review["id"] = derive_evidence_id("review", identity_review)
    review_records.append(identity_review)
    ingredient_records: list[dict[str, Any]] = []
    if "ingredientTranscription" in human_review:
        transcription = human_review["ingredientTranscription"]
        ingredient_record = {
            "gtin": submission["gtin"],
            "market": submission["market"],
            "sourceKey": source_key,
            "sourceRecordID": f"{submission['submissionID']}:ingredients",
            "sourceRevision": admission["admissionID"],
            "ingredientsText": transcription["text"],
            "languageCode": transcription["languageCode"],
            "observedAt": submission["observedAt"],
            "retrievedAt": admission["admittedAt"],
            "contentHash": formulation_hash(transcription["text"]),
            "captureMethod": "package-transcription",
            "verificationState": "human-verified",
            "transformation": {
                "tool": "apple-vision-local+human-verification",
                "version": ocr_report["engineVersion"],
                "language": transcription["languageCode"],
            },
        }
        ingredient_record["id"] = derive_evidence_id("ingredient", ingredient_record)
        ingredient_records.append(ingredient_record)
        ingredient_review = {
            "targetID": ingredient_record["id"],
            "targetType": "ingredient",
            "state": "approved",
            "reviewerID": reviewer,
            "reviewedAt": reviewed_at,
            "decisionCode": "human-verified-package-transcription",
            "reason": human_review["reviewReason"],
            "toolContext": "product-evidence-intake-v1",
        }
        ingredient_review["id"] = derive_evidence_id("review", ingredient_review)
        review_records.append(ingredient_review)
    correction = submission["issueType"] != "missing-product"
    existing_context = submission.get("currentCatalogEvidence")
    impact = {
        # Any correction can change the identity/formulation/certification basis
        # for a current assessment. Invalidate first; methodology review decides
        # what can become current again.
        "mustInvalidateCurrentAssessment": correction,
        "mustRouteToMethodologyReview": bool(ingredient_records or correction),
        "reasonCode": "USER_PACKAGE_EVIDENCE_CORRECTION" if correction else "USER_PACKAGE_EVIDENCE_NEW_PRODUCT",
        "currentCatalogEvidence": existing_context,
        "conflictState": human_review.get("conflictState", "none"),
        "automaticHalalDecisionForbidden": True,
    }
    proposal_without_digest = {
        "schemaVersion": 1,
        "proposalType": "owner-admitted-user-package-evidence",
        "submissionID": submission["submissionID"],
        "admissionID": admission["admissionID"],
        "generatedAt": reviewed_at,
        "source": source,
        "records": {
            "identities": [identity_record],
            "ingredients": ingredient_records,
            "packageEvidence": package_records,
            "reviews": sorted(review_records, key=lambda item: item["id"]),
        },
        "assessmentImpact": impact,
        "retention": {
            "catalogStoresImageBytes": False,
            "rawEmailStored": False,
            "workflowArtifactRetentionDays": 7,
            "rejectedRawRetentionDays": 0,
        },
        "sanitizedSubmitterNotes": human_review.get("sanitizedSubmitterNotes"),
        "ocr": {
            "engine": ocr_report["engine"],
            "engineVersion": ocr_report["engineVersion"],
            "verificationState": "unverified-machine-output-human-reviewed-separately",
            "reportSha256": sha256_text(canonical_json(ocr_report)),
        },
    }
    proposal_digest = sha256_text(canonical_json(proposal_without_digest))
    proposal = dict(proposal_without_digest)
    proposal["proposalDigest"] = proposal_digest
    proposal["registryEntry"] = {
        "submissionID": submission["submissionID"],
        "admissionID": admission["admissionID"],
        "reviewedAt": reviewed_at,
        "inputAttachmentHashes": sorted(item["sha256"] for item in validation_report["attachments"]),
        "admittedAttachmentHashes": sorted(item["sha256"] for item in package_records),
        "proposalDigest": proposal_digest,
    }
    return proposal


def validate_proposal_records(proposal: dict[str, Any]) -> None:
    """Validate record compatibility with the existing immutable evidence model when available."""
    try:
        import evidence_model_core  # type: ignore
    except ImportError:
        return
    envelope = {
        "schemaVersion": 1,
        "sources": [proposal["source"]],
        "identities": proposal["records"]["identities"],
        "ingredients": proposal["records"]["ingredients"],
        "retailerEvidence": [],
        "remoteImages": [],
        "packageEvidence": proposal["records"]["packageEvidence"],
        "certifications": [],
        "reviews": proposal["records"]["reviews"],
        "assessments": [],
        "validityEvents": [],
        "currentSelections": [],
        "releases": [],
    }
    evidence_model_core.validate_envelope(envelope)




def finalize_review_decision(
    *,
    validation_report: dict[str, Any],
    ocr_report: dict[str, Any],
    human_review: dict[str, Any],
) -> dict[str, Any]:
    if human_review["decision"] == "propose-observation":
        return build_proposal(
            validation_report=validation_report,
            ocr_report=ocr_report,
            human_review=human_review,
        )
    return {
        "schemaVersion": 1,
        "resultType": "rejected-product-evidence",
        "submissionID": validation_report["submissionID"],
        "admissionID": validation_report["admissionID"],
        "reviewedAt": human_review["reviewedAt"],
        "reviewerID": human_review["reviewerID"],
        "reviewReason": human_review["reviewReason"],
        "catalogEvidenceCreated": False,
        "gitPatchCreated": False,
        "rawRepositoryRetentionDays": 0,
    }

def validate_proposal_output(proposal: Any) -> dict[str, Any]:
    proposal = _closed_shape(
        proposal,
        "proposal",
        {
            "schemaVersion", "proposalType", "submissionID", "admissionID", "generatedAt",
            "source", "records", "assessmentImpact", "retention", "ocr", "proposalDigest", "registryEntry",
        },
        {"sanitizedSubmitterNotes"},
    )
    if proposal["schemaVersion"] != 1 or proposal["proposalType"] != "owner-admitted-user-package-evidence":
        fail("proposal", "unsupported proposal contract")
    if not SUBMISSION_ID_RE.fullmatch(str(proposal["submissionID"])):
        fail("proposal.submissionID", "invalid submission ID")
    if not ADMISSION_ID_RE.fullmatch(str(proposal["admissionID"])):
        fail("proposal.admissionID", "invalid admission ID")
    parse_timestamp(proposal["generatedAt"], "proposal.generatedAt")
    if not isinstance(proposal["proposalDigest"], str) or not SHA256_RE.fullmatch(proposal["proposalDigest"]):
        fail("proposal.proposalDigest", "invalid SHA-256")
    digest_payload = dict(proposal)
    digest_payload.pop("proposalDigest", None)
    registry_entry = digest_payload.pop("registryEntry", None)
    expected_digest = sha256_text(canonical_json(digest_payload))
    if proposal["proposalDigest"] != expected_digest:
        fail("proposal.proposalDigest", f"does not match canonical proposal content; expected {expected_digest}")
    if not isinstance(registry_entry, dict):
        fail("proposal.registryEntry", "must be an object")
    validate_registry({"schemaVersion": 1, "entries": [registry_entry]})
    if registry_entry["submissionID"] != proposal["submissionID"] or registry_entry["admissionID"] != proposal["admissionID"]:
        fail("proposal.registryEntry", "submission/admission identity mismatch")
    if registry_entry["proposalDigest"] != proposal["proposalDigest"]:
        fail("proposal.registryEntry.proposalDigest", "must match proposalDigest")
    records = _closed_shape(proposal["records"], "proposal.records", {"identities", "ingredients", "packageEvidence", "reviews"})
    admitted_hashes = []
    for index, record in enumerate(_require_array(records["packageEvidence"], "proposal.records.packageEvidence")):
        record = _require_object(record, f"proposal.records.packageEvidence[{index}]")
        digest = record.get("sha256")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            fail(f"proposal.records.packageEvidence[{index}].sha256", "invalid admitted image hash")
        admitted_hashes.append(digest)
    if sorted(admitted_hashes) != sorted(registry_entry["admittedAttachmentHashes"]):
        fail("proposal.registryEntry.admittedAttachmentHashes", "must exactly match package-evidence hashes")
    impact = _closed_shape(
        proposal["assessmentImpact"],
        "proposal.assessmentImpact",
        {"mustInvalidateCurrentAssessment", "mustRouteToMethodologyReview", "reasonCode", "currentCatalogEvidence", "conflictState", "automaticHalalDecisionForbidden"},
    )
    if impact["automaticHalalDecisionForbidden"] is not True:
        fail("proposal.assessmentImpact.automaticHalalDecisionForbidden", "must be explicitly true")
    retention = _closed_shape(
        proposal["retention"],
        "proposal.retention",
        {"catalogStoresImageBytes", "rawEmailStored", "workflowArtifactRetentionDays", "rejectedRawRetentionDays"},
    )
    if retention != {
        "catalogStoresImageBytes": False,
        "rawEmailStored": False,
        "workflowArtifactRetentionDays": 7,
        "rejectedRawRetentionDays": 0,
    }:
        fail("proposal.retention", "must preserve the reviewed no-image/no-email retention contract")
    forbidden_keys = {"senderEmail", "rawEmail", "imageBytes", "halalStatus", "acceptedHalalVerdict"}
    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in forbidden_keys:
                    fail(path + "." + key, "forbidden sensitive or accepted-verdict field")
                walk(child, path + "." + key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")
    walk(proposal, "proposal")
    validate_proposal_records(proposal)
    return proposal


def materialize_patch_bundle(*, proposal: dict[str, Any], registry: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    proposal = validate_proposal_output(proposal)
    registry = validate_registry(registry)
    candidate = {"schemaVersion": 1, "entries": [*registry["entries"], proposal["registryEntry"]]}
    validate_registry(candidate)
    candidate["entries"] = sorted(candidate["entries"], key=lambda item: item["submissionID"])
    if output_dir.exists():
        if output_dir.is_symlink() or not output_dir.is_dir():
            fail(str(output_dir), "patch output must be a regular directory")
        for child in output_dir.iterdir():
            if child.is_dir():
                import shutil
                shutil.rmtree(child)
            else:
                child.unlink()
    output_dir.mkdir(parents=True, exist_ok=True)
    proposal_relative = Path("Data/submissions/admitted") / f"{proposal['submissionID']}.json"
    registry_relative = Path("Data/submissions/admitted-submission-registry-v1.json")
    proposal_path = output_dir / proposal_relative
    registry_path = output_dir / registry_relative
    dump_json(proposal_path, proposal)
    dump_json(registry_path, candidate)
    files = []
    for relative, path in ((proposal_relative, proposal_path), (registry_relative, registry_path)):
        data = path.read_bytes()
        files.append({"path": relative.as_posix(), "sha256": sha256_bytes(data), "byteSize": len(data)})
    manifest = {
        "schemaVersion": 1,
        "submissionID": proposal["submissionID"],
        "proposalDigest": proposal["proposalDigest"],
        "containsImageBytes": False,
        "containsRawEmail": False,
        "requiresReviewedGitPR": True,
        "files": sorted(files, key=lambda item: item["path"]),
    }
    dump_json(output_dir / "patch-manifest.json", manifest)
    return manifest

def render_review_markdown(validation_report: dict[str, Any], ocr_report: dict[str, Any]) -> str:
    lines = [
        "# Product evidence human review",
        "",
        f"- Submission: `{validation_report['submissionID']}`",
        f"- Admission: `{validation_report['admissionID']}`",
        f"- GTIN: `{validation_report['gtin']}`",
        f"- Market: `{validation_report['market']}`",
        "",
        "The images beside this file are workflow-re-encoded copies only. OCR is **unverified assistance**; compare every character with the image before creating human-review.json.",
        "",
    ]
    for attachment in ocr_report["attachments"]:
        lines.extend([
            f"## {attachment['fileName']} ({attachment['purpose']})",
            "",
            f"Sanitized SHA-256: `{attachment['sanitizedSha256']}`",
            f"OCR state: `{attachment['ocrState']}`",
            "",
        ])
        if attachment["lines"]:
            lines.append("```text")
            lines.extend(line["text"] for line in attachment["lines"])
            lines.extend(["```", ""])
        else:
            lines.extend(["No OCR text. Review the image manually.", ""])
    lines.extend([
        "## Required human checks",
        "",
        "- Barcode image matches the declared GTIN.",
        "- Product identity/variant/quantity and market match the package.",
        "- Every visible ingredient panel is transcribed exactly when present.",
        "- Personal information is absent after the workflow re-encode.",
        "- Rights allow the intended evidence use.",
        "- Any conflict with current catalog evidence is recorded.",
        "- Do not create or preserve an accepted halal verdict from OCR or submitter claims.",
        "",
    ])
    return "\n".join(lines)


def parse_now(value: str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    return parse_timestamp(value, "--now")


def dump_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def command_validate(args: argparse.Namespace) -> None:
    registry = load_json(Path(args.registry)) if args.registry else None
    report = validate_package_directory(
        Path(args.package_dir),
        now=parse_now(args.now),
        expected_reviewer=args.expected_reviewer,
        registry=registry,
        require_public_staging=not args.local_private_processing,
    )
    dump_json(Path(args.output), report)


def command_ocr_request(args: argparse.Namespace) -> None:
    report = load_json(Path(args.validation_report))
    dump_json(Path(args.output), build_ocr_request(report))


def command_validate_ocr(args: argparse.Namespace) -> None:
    validation_report = load_json(Path(args.validation_report))
    ocr_report = validate_ocr_report(load_json(Path(args.ocr_report)), validation_report=validation_report)
    dump_json(Path(args.output), ocr_report)


def command_render_review(args: argparse.Namespace) -> None:
    validation_report = load_json(Path(args.validation_report))
    ocr_report = validate_ocr_report(load_json(Path(args.ocr_report)), validation_report=validation_report)
    Path(args.output).write_text(render_review_markdown(validation_report, ocr_report), encoding="utf-8")


def command_finalize(args: argparse.Namespace) -> None:
    validation_report = load_json(Path(args.validation_report))
    ocr_report = validate_ocr_report(load_json(Path(args.ocr_report)), validation_report=validation_report)
    human_review = validate_human_review(
        load_json(Path(args.human_review)),
        validation_report=validation_report,
        ocr_report=ocr_report,
        now=parse_now(args.now),
        expected_reviewer=args.expected_reviewer,
    )
    result = finalize_review_decision(
        validation_report=validation_report,
        ocr_report=ocr_report,
        human_review=human_review,
    )
    if result.get("proposalType") == "owner-admitted-user-package-evidence":
        validate_proposal_output(result)
    dump_json(Path(args.output), result)



def command_make_patch(args: argparse.Namespace) -> None:
    proposal = load_json(Path(args.proposal))
    registry = load_json(Path(args.registry))
    materialize_patch_bundle(proposal=proposal, registry=registry, output_dir=Path(args.output_dir))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--package-dir", required=True)
    validate.add_argument("--registry")
    validate.add_argument("--expected-reviewer")
    validate.add_argument("--now")
    validate.add_argument(
        "--local-private-processing",
        action="store_true",
        help="Allow an owner-admitted package that was never approved for public Git staging.",
    )
    validate.add_argument("--output", required=True)
    validate.set_defaults(func=command_validate)
    request = sub.add_parser("ocr-request")
    request.add_argument("--validation-report", required=True)
    request.add_argument("--output", required=True)
    request.set_defaults(func=command_ocr_request)
    validate_ocr = sub.add_parser("validate-ocr")
    validate_ocr.add_argument("--validation-report", required=True)
    validate_ocr.add_argument("--ocr-report", required=True)
    validate_ocr.add_argument("--output", required=True)
    validate_ocr.set_defaults(func=command_validate_ocr)
    render = sub.add_parser("render-review")
    render.add_argument("--validation-report", required=True)
    render.add_argument("--ocr-report", required=True)
    render.add_argument("--output", required=True)
    render.set_defaults(func=command_render_review)
    finalize = sub.add_parser("finalize")
    finalize.add_argument("--validation-report", required=True)
    finalize.add_argument("--ocr-report", required=True)
    finalize.add_argument("--human-review", required=True)
    finalize.add_argument("--expected-reviewer")
    finalize.add_argument("--now")
    finalize.add_argument("--output", required=True)
    finalize.set_defaults(func=command_finalize)
    patch = sub.add_parser("make-patch")
    patch.add_argument("--proposal", required=True)
    patch.add_argument("--registry", required=True)
    patch.add_argument("--output-dir", required=True)
    patch.set_defaults(func=command_make_patch)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        args.func(args)
    except (IntakeValidationError, OSError) as error:
        print(f"product-evidence-intake: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
