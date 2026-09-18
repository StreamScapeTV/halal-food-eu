#!/usr/bin/env python3
"""Minimal App Store Connect build-identity lookup for release retry safety.

The helper deliberately supports one read-only operation: determine whether an exact
bundle/marketing-version/build-number identity already exists in App Store Connect.
Authentication uses the fixed App Store Connect ES256 API key supplied by Central CI.
"""

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
    if not key_path.is_file():
        raise AppStoreConnectError("App Store Connect API key file is missing")

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
    raw_signature = der_es256_to_raw(completed.stdout)
    return f"{encoded_header}.{encoded_payload}.{_b64url(raw_signature)}"


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


def _only_resource(payload: dict[str, Any], *, label: str, allow_empty: bool) -> dict[str, Any] | None:
    data = payload.get("data")
    if not isinstance(data, list):
        raise AppStoreConnectError(f"App Store Connect {label} response has no resource list")
    if not data and allow_empty:
        return None
    if len(data) != 1 or not isinstance(data[0], dict):
        raise AppStoreConnectError(f"App Store Connect {label} identity is not unique")
    return data[0]


def build_exists(
    *,
    bundle_id: str,
    marketing_version: str,
    build_number: str,
    token: str,
    getter: Callable[..., dict[str, Any]] = api_get,
) -> bool:
    if not BUNDLE_ID.fullmatch(bundle_id):
        raise AppStoreConnectError("bundle identifier is invalid")
    if not MARKETING_VERSION.fullmatch(marketing_version):
        raise AppStoreConnectError("marketing version must be three numeric components")
    if not BUILD_NUMBER.fullmatch(build_number) or int(build_number) >= 9_999_999_999:
        raise AppStoreConnectError("tag-driven build number is outside the supported range")

    app_query = urllib.parse.urlencode({"filter[bundleId]": bundle_id, "limit": "2"})
    app = _only_resource(getter(f"/v1/apps?{app_query}", token=token), label="app", allow_empty=False)
    assert app is not None
    app_id = app.get("id")
    app_attributes = app.get("attributes")
    if not isinstance(app_id, str) or not app_id:
        raise AppStoreConnectError("App Store Connect app identity is invalid")
    if not isinstance(app_attributes, dict) or app_attributes.get("bundleId") != bundle_id:
        raise AppStoreConnectError("App Store Connect app bundle identity mismatch")

    upload_query = urllib.parse.urlencode(
        {
            "filter[cfBundleShortVersionString]": marketing_version,
            "filter[cfBundleVersion]": build_number,
            "filter[platform]": "IOS",
            "fields[buildUploads]": "cfBundleShortVersionString,cfBundleVersion,state,platform",
            "limit": "200",
        }
    )
    upload_payload = getter(
        f"/v1/apps/{urllib.parse.quote(app_id, safe='')}/buildUploads?{upload_query}",
        token=token,
    )
    uploads = upload_payload.get("data")
    if not isinstance(uploads, list):
        raise AppStoreConnectError("App Store Connect build-upload response has no resource list")
    for upload in uploads:
        if not isinstance(upload, dict) or upload.get("type") != "buildUploads":
            raise AppStoreConnectError("App Store Connect build-upload identity is invalid")
        attributes = upload.get("attributes")
        if not isinstance(attributes, dict):
            raise AppStoreConnectError("App Store Connect build-upload attributes are invalid")
        if (
            attributes.get("cfBundleShortVersionString") != marketing_version
            or attributes.get("cfBundleVersion") != build_number
            or attributes.get("platform") != "IOS"
        ):
            raise AppStoreConnectError("App Store Connect build-upload identity mismatch")
        state_value = attributes.get("state")
        state = state_value.get("state") if isinstance(state_value, dict) else state_value
        if state in {"PROCESSING", "COMPLETE"}:
            return True
        if state == "FAILED":
            # Apple documents that a failed upload may reuse the same build number.
            continue
        if state == "AWAITING_UPLOAD":
            raise AppStoreConnectError("exact App Store Connect build upload is still awaiting upload")
        raise AppStoreConnectError("exact App Store Connect build upload has an unknown state")

    build_query = urllib.parse.urlencode(
        {
            "filter[app]": app_id,
            "filter[version]": build_number,
            "filter[preReleaseVersion.version]": marketing_version,
            "filter[preReleaseVersion.platform]": "IOS",
            "include": "preReleaseVersion",
            "limit": "2",
        }
    )
    build_payload = getter(f"/v1/builds?{build_query}", token=token)
    build = _only_resource(build_payload, label="build", allow_empty=True)
    if build is None:
        return False
    attributes = build.get("attributes")
    if not isinstance(attributes, dict) or attributes.get("version") != build_number:
        raise AppStoreConnectError("App Store Connect build number mismatch")
    relationship = build.get("relationships", {}).get("preReleaseVersion", {}).get("data")
    if not isinstance(relationship, dict) or not isinstance(relationship.get("id"), str):
        raise AppStoreConnectError("App Store Connect build prerelease-version identity is missing")
    prerelease_id = relationship["id"]
    included = build_payload.get("included")
    if not isinstance(included, list):
        raise AppStoreConnectError("App Store Connect build response omitted prerelease-version metadata")
    matches = [
        item
        for item in included
        if isinstance(item, dict)
        and item.get("type") == "preReleaseVersions"
        and item.get("id") == prerelease_id
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("attributes"), dict):
        raise AppStoreConnectError("App Store Connect prerelease-version metadata is not unique")
    if matches[0]["attributes"].get("version") != marketing_version:
        raise AppStoreConnectError("App Store Connect marketing-version mismatch")
    return True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    check = subcommands.add_parser("build-exists", help="Print present/absent for one exact TestFlight build identity")
    check.add_argument("--bundle-id", required=True)
    check.add_argument("--marketing-version", required=True)
    check.add_argument("--build-number", required=True)
    check.add_argument("--issuer-id", required=True)
    check.add_argument("--key-id", required=True)
    check.add_argument("--key-path", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        if args.command == "build-exists":
            token = make_token(issuer_id=args.issuer_id, key_id=args.key_id, key_path=args.key_path)
            exists = build_exists(
                bundle_id=args.bundle_id,
                marketing_version=args.marketing_version,
                build_number=args.build_number,
                token=token,
            )
            print("present" if exists else "absent")
            return
    except AppStoreConnectError as exc:
        raise SystemExit(str(exc)) from exc
    raise SystemExit("unsupported App Store Connect operation")


if __name__ == "__main__":
    main()
