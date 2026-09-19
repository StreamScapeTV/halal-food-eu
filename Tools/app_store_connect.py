#!/usr/bin/env python3
"""Read-only App Store Connect reconciliation for one exact TestFlight build identity."""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

API_ROOT = "https://api.appstoreconnect.apple.com"
BUNDLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]{1,254}$")
KEY_ID = re.compile(r"^[A-Za-z0-9]{5,64}$")
ISSUER_ID = re.compile(r"^[0-9A-Fa-f-]{8,128}$")
MARKETING_VERSION = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
BUILD_NUMBER = re.compile(r"^[1-9][0-9]{0,9}$")


class AppStoreConnectError(RuntimeError):
    """Raised when release-state discovery cannot be trusted."""


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _read_der_length(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise AppStoreConnectError("ES256 signature is truncated")
    first = data[offset]
    if first < 0x80:
        return first, offset + 1
    length_bytes = first & 0x7F
    if length_bytes < 1 or length_bytes > 2 or offset + 1 + length_bytes > len(data):
        raise AppStoreConnectError("ES256 signature uses an unsupported DER length")
    value = int.from_bytes(data[offset + 1 : offset + 1 + length_bytes], "big")
    return value, offset + 1 + length_bytes


def _read_der_integer(data: bytes, offset: int) -> tuple[bytes, int]:
    if offset >= len(data) or data[offset] != 0x02:
        raise AppStoreConnectError("ES256 signature is missing a DER integer")
    length, value_offset = _read_der_length(data, offset + 1)
    end = value_offset + length
    if length < 1 or end > len(data):
        raise AppStoreConnectError("ES256 signature integer is truncated")
    value = data[value_offset:end]
    if value[0] == 0:
        value = value[1:]
    if not value or len(value) > 32:
        raise AppStoreConnectError("ES256 signature integer is out of range")
    return value.rjust(32, b"\0"), end


def der_es256_to_raw(signature: bytes) -> bytes:
    if not signature or signature[0] != 0x30:
        raise AppStoreConnectError("ES256 signature is not a DER sequence")
    sequence_length, offset = _read_der_length(signature, 1)
    sequence_end = offset + sequence_length
    if sequence_end != len(signature):
        raise AppStoreConnectError("ES256 signature DER sequence length is invalid")
    r_value, offset = _read_der_integer(signature, offset)
    s_value, offset = _read_der_integer(signature, offset)
    if offset != sequence_end:
        raise AppStoreConnectError("ES256 signature has trailing DER data")
    return r_value + s_value


def make_token(*, issuer_id: str, key_id: str, key_path: Path, now: int | None = None) -> str:
    if not ISSUER_ID.fullmatch(issuer_id):
        raise AppStoreConnectError("App Store Connect issuer ID is invalid")
    if not KEY_ID.fullmatch(key_id):
        raise AppStoreConnectError("App Store Connect key ID is invalid")
    if not key_path.is_file() or key_path.is_symlink():
        raise AppStoreConnectError("App Store Connect API key file is missing or unsafe")

    issued_at = int(time.time()) if now is None else now
    header = {"alg": "ES256", "kid": key_id, "typ": "JWT"}
    payload = {
        "iss": issuer_id,
        "iat": issued_at - 5,
        "exp": issued_at + 600,
        "aud": "appstoreconnect-v1",
    }
    encoded_header = _b64url(json.dumps(header, separators=(",", ":"), sort_keys=True).encode())
    encoded_payload = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signing_input = f"{encoded_header}.{encoded_payload}".encode("ascii")
    try:
        completed = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(key_path)],
            input=signing_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        raise AppStoreConnectError("OpenSSL is required for App Store Connect API authentication") from exc
    if completed.returncode != 0 or not completed.stdout:
        raise AppStoreConnectError("App Store Connect API token signing failed")
    return f"{encoded_header}.{encoded_payload}.{_b64url(der_es256_to_raw(completed.stdout))}"


def api_get(path: str, *, token: str) -> dict[str, Any]:
    if not path.startswith("/"):
        raise AppStoreConnectError("App Store Connect API path must be absolute")
    request = urllib.request.Request(
        API_ROOT + path,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "HalalFoodEU-TestFlight/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.load(response)
    except urllib.error.HTTPError as exc:
        raise AppStoreConnectError(f"App Store Connect API request failed with HTTP {exc.code}") from None
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise AppStoreConnectError("App Store Connect API request failed") from exc
    if not isinstance(payload, dict):
        raise AppStoreConnectError("App Store Connect API returned a non-object response")
    return payload


def _resource_list(payload: dict[str, Any], *, label: str) -> list[dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, list) or any(not isinstance(item, dict) for item in data):
        raise AppStoreConnectError(f"App Store Connect {label} response has no valid resource list")
    links = payload.get("links")
    if isinstance(links, dict) and links.get("next") not in (None, ""):
        raise AppStoreConnectError(f"App Store Connect exact {label} history exceeded the bounded lookup page")
    meta = payload.get("meta")
    if isinstance(meta, dict):
        paging = meta.get("paging")
        if isinstance(paging, dict):
            total = paging.get("total")
            if isinstance(total, int) and total > len(data):
                raise AppStoreConnectError(f"App Store Connect exact {label} history exceeded the bounded lookup page")
    return data


def _app_id(*, bundle_id: str, token: str, getter: Callable[..., dict[str, Any]]) -> str:
    app_query = urllib.parse.urlencode({"filter[bundleId]": bundle_id, "limit": "2"})
    apps = _resource_list(getter(f"/v1/apps?{app_query}", token=token), label="app")
    if len(apps) != 1:
        raise AppStoreConnectError("App Store Connect app identity is not unique")
    app = apps[0]
    app_id = app.get("id")
    attributes = app.get("attributes")
    if (
        app.get("type") != "apps"
        or not isinstance(app_id, str)
        or not app_id
        or not isinstance(attributes, dict)
        or attributes.get("bundleId") != bundle_id
    ):
        raise AppStoreConnectError("App Store Connect app bundle identity mismatch")
    return app_id


def build_reconciliation_state(
    *,
    bundle_id: str,
    marketing_version: str,
    build_number: str,
    token: str,
    getter: Callable[..., dict[str, Any]] = api_get,
) -> str:
    """Classify an exact TestFlight identity as accepted, processing, retryable, or absent."""

    if not BUNDLE_ID.fullmatch(bundle_id):
        raise AppStoreConnectError("bundle identifier is invalid")
    if not MARKETING_VERSION.fullmatch(marketing_version):
        raise AppStoreConnectError("marketing version must be three numeric components")
    if not BUILD_NUMBER.fullmatch(build_number) or int(build_number) >= 9_999_999_999:
        raise AppStoreConnectError("tag-driven build number is outside the supported range")

    app_id = _app_id(bundle_id=bundle_id, token=token, getter=getter)

    upload_query = urllib.parse.urlencode(
        {
            "filter[cfBundleShortVersionString]": marketing_version,
            "filter[cfBundleVersion]": build_number,
            "filter[platform]": "IOS",
            "fields[buildUploads]": "cfBundleShortVersionString,cfBundleVersion,state,platform",
            "limit": "200",
        }
    )
    uploads = _resource_list(
        getter(f"/v1/apps/{urllib.parse.quote(app_id, safe='')}/buildUploads?{upload_query}", token=token),
        label="build-upload",
    )
    upload_states: set[str] = set()
    for upload in uploads:
        attributes = upload.get("attributes")
        if upload.get("type") != "buildUploads" or not isinstance(attributes, dict):
            raise AppStoreConnectError("App Store Connect build-upload identity is invalid")
        if (
            attributes.get("cfBundleShortVersionString") != marketing_version
            or attributes.get("cfBundleVersion") != build_number
            or attributes.get("platform") != "IOS"
        ):
            raise AppStoreConnectError("App Store Connect build-upload identity mismatch")
        state_value = attributes.get("state")
        state = state_value.get("state") if isinstance(state_value, dict) else state_value
        if state not in {"AWAITING_UPLOAD", "PROCESSING", "FAILED", "COMPLETE"}:
            raise AppStoreConnectError("App Store Connect build-upload state is missing or unsupported")
        upload_states.add(state)

    build_query = urllib.parse.urlencode(
        {
            "filter[app]": app_id,
            "filter[version]": build_number,
            "filter[preReleaseVersion.version]": marketing_version,
            "filter[preReleaseVersion.platform]": "IOS",
            "fields[builds]": "version,processingState,preReleaseVersion",
            "include": "preReleaseVersion",
            "limit": "200",
        }
    )
    build_payload = getter(f"/v1/builds?{build_query}", token=token)
    builds = _resource_list(build_payload, label="build")
    included = build_payload.get("included")
    if builds and not isinstance(included, list):
        raise AppStoreConnectError("App Store Connect build response omitted prerelease-version metadata")

    build_states: set[str] = set()
    for build in builds:
        attributes = build.get("attributes")
        if build.get("type") != "builds" or not isinstance(attributes, dict):
            raise AppStoreConnectError("App Store Connect build identity is invalid")
        if attributes.get("version") != build_number:
            raise AppStoreConnectError("App Store Connect build number mismatch")
        processing_state = attributes.get("processingState")
        if processing_state not in {"PROCESSING", "FAILED", "INVALID", "VALID"}:
            raise AppStoreConnectError("App Store Connect build processing state is missing or unsupported")
        build_states.add(processing_state)
        relationship = build.get("relationships", {}).get("preReleaseVersion", {}).get("data")
        if not isinstance(relationship, dict) or not isinstance(relationship.get("id"), str):
            raise AppStoreConnectError("App Store Connect build prerelease-version identity is missing")
        prerelease_id = relationship["id"]
        matches = [
            item
            for item in included
            if isinstance(item, dict)
            and item.get("type") == "preReleaseVersions"
            and item.get("id") == prerelease_id
            and isinstance(item.get("attributes"), dict)
        ]
        if len(matches) != 1 or matches[0]["attributes"].get("version") != marketing_version:
            raise AppStoreConnectError("App Store Connect prerelease-version identity mismatch")

    # A completed upload or VALID processed build proves that this exact identity
    # reached a provider-accepted state suitable for the source build-number bump.
    if "COMPLETE" in upload_states or "VALID" in build_states:
        return "accepted"
    # Never duplicate an upload while Apple still owns an in-flight exact identity.
    if upload_states & {"AWAITING_UPLOAD", "PROCESSING"} or "PROCESSING" in build_states:
        return "processing"
    # Apple documents that a FAILED upload may reuse the same build number. Processed
    # FAILED/INVALID deliveries are likewise terminal non-acceptance.
    if upload_states or build_states:
        if upload_states <= {"FAILED"} and build_states <= {"FAILED", "INVALID"}:
            return "retryable"
        raise AppStoreConnectError("App Store Connect exact build states are inconsistent")
    return "absent"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    check = parser.add_subparsers(dest="command", required=True).add_parser(
        "build-state", help="Print accepted/processing/retryable/absent for one exact TestFlight build identity"
    )
    check.add_argument("--bundle-id", required=True)
    check.add_argument("--marketing-version", required=True)
    check.add_argument("--build-number", required=True)
    check.add_argument("--issuer-id", required=True)
    check.add_argument("--key-id", required=True)
    check.add_argument("--key-path", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        token = make_token(issuer_id=args.issuer_id, key_id=args.key_id, key_path=args.key_path)
        state = build_reconciliation_state(
            bundle_id=args.bundle_id,
            marketing_version=args.marketing_version,
            build_number=args.build_number,
            token=token,
        )
        print(state)
        return 0
    except AppStoreConnectError as exc:
        print(f"App Store Connect lookup failed: {exc}", file=__import__("sys").stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
