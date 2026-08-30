"""Bounded acquisition for official Open Prices JSONL exports."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import os
import socket
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from urllib.parse import urlparse

import catalog_security
import open_prices_common as common

MAX_DEFAULT_COMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MAX_DEFAULT_EXPANDED_BYTES = 8 * 1024 * 1024 * 1024
MAX_DEFAULT_RECORDS = 20_000_000
MAX_DEFAULT_PAYLOAD_BYTES = 5 * 1024 * 1024 * 1024
MAX_LINE_BYTES = 2 * 1024 * 1024
MAX_MALFORMED_RATE = 0.001
MAX_JSON_DEPTH = 32
MAX_JSON_NODES = 100_000
ALLOWED_EXPORT_PATH_PREFIXES = ("/data",)

PROJECTED_FIELDS = {
    "locations": {
        "id", "type", "osm_id", "osm_type", "osm_name",
        "osm_tag_key", "osm_tag_value", "osm_brand", "osm_address_postcode",
        "osm_address_city", "osm_address_country", "osm_address_country_code",
        "osm_version", "source", "created", "updated",
    },
    "proofs": {"id", "location_id", "type", "date", "currency", "created", "updated"},
    "prices": {
        "id", "product_code", "product_id", "location_id", "proof_id", "date",
        "currency", "price", "price_is_discounted", "price_without_discount",
        "price_per", "receipt_quantity", "type", "created", "updated",
    },
}


class _RejectRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects before urllib can issue a second network request."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise common.AdapterError("Open Prices export redirects are forbidden by source policy")


def _project(kind: str, record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in sorted(PROJECTED_FIELDS[kind]) if key in record}


def _strict_json_object(raw: bytes) -> dict[str, Any] | None:
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None

    def reject_constant(_: str) -> Any:
        raise ValueError("non-finite JSON constant")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, value in pairs:
            if key in output:
                raise ValueError("duplicate JSON key")
            output[key] = value
        return output

    try:
        value = json.loads(text, parse_constant=reject_constant, object_pairs_hook=unique_object)
    except (json.JSONDecodeError, RecursionError, ValueError):
        return None
    if not isinstance(value, dict):
        return None

    nodes = 0
    stack: list[tuple[Any, int]] = [(value, 0)]
    while stack:
        item, depth = stack.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            return None
        if isinstance(item, str):
            if common.CONTROL.search(item):
                return None
        elif isinstance(item, dict):
            stack.extend((key, depth + 1) for key in item)
            stack.extend((nested, depth + 1) for nested in item.values())
        elif isinstance(item, list):
            stack.extend((nested, depth + 1) for nested in item)
        elif isinstance(item, float):
            if not math.isfinite(item):
                return None
        elif item is None or isinstance(item, (bool, int)):
            continue
        else:
            return None
    return value


def _iter_jsonl(
    stream: BinaryIO,
    *,
    record_limit: int,
    max_expanded_bytes: int,
    allow_partial: bool,
) -> Iterator[tuple[dict[str, Any] | None, int]]:
    records = 0
    expanded = 0
    while True:
        line = stream.readline(MAX_LINE_BYTES + 1)
        if not line:
            break
        expanded += len(line)
        if expanded > max_expanded_bytes:
            raise common.AdapterError("expanded Open Prices export exceeds configured byte limit")
        if len(line) > MAX_LINE_BYTES:
            raise common.AdapterError("Open Prices JSONL line exceeds configured bound")
        if not line.strip():
            continue
        if records >= record_limit:
            if allow_partial:
                break
            raise common.AdapterError("Open Prices export exceeds configured record limit")
        records += 1
        value = _strict_json_object(line)
        yield value, len(line)


def _fixture_records(path: Path, *, record_limit: int, max_expanded_bytes: int, allow_partial: bool) -> Iterator[dict[str, Any] | None]:
    with path.open("rb") as stream:
        for record, _ in _iter_jsonl(
            stream, record_limit=record_limit, max_expanded_bytes=max_expanded_bytes, allow_partial=allow_partial
        ):
            yield record


def _validate_network_target(kind: str, url: str, policy: common.SourcePolicy) -> None:
    if kind not in common.EXPORT_KINDS or policy.export_urls.get(kind) != url:
        raise common.AdapterError("Open Prices export URL does not match admitted source policy")
    try:
        catalog_security.validate_https_url(
            url,
            allowed_hosts=frozenset(policy.allowed_hosts),
            allowed_path_prefixes=ALLOWED_EXPORT_PATH_PREFIXES,
        )
        host = urlparse(url).hostname
        if not host:
            raise common.AdapterError("Open Prices export URL is missing a host")
        try:
            resolved = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise common.AdapterError("Open Prices export host resolution failed") from exc
        addresses = sorted({entry[4][0] for entry in resolved if entry[4]})
        catalog_security.validate_resolved_addresses(addresses)
    except catalog_security.SecurityError as exc:
        raise common.AdapterError("Open Prices export failed network security policy") from exc


def _download(kind: str, url: str, policy: common.SourcePolicy, *, max_compressed_bytes: int, retries: int = 4) -> tuple[Path, dict[str, Any]]:
    opener = urllib.request.build_opener(_RejectRedirect())
    request = urllib.request.Request(url, headers={"Accept": "application/octet-stream", "User-Agent": "HalalFoodEU/1.0 (Open Prices bulk importer)"})
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        temp_path: Path | None = None
        try:
            _validate_network_target(kind, url, policy)
            with opener.open(request, timeout=60) as response:
                final_url = response.geturl()
                if final_url != url:
                    raise common.AdapterError(f"{kind} export response URL did not match its admitted exact URL")
                declared_raw = response.headers.get("Content-Length")
                declared: int | None = None
                if declared_raw:
                    try:
                        declared = int(declared_raw)
                    except ValueError as exc:
                        raise common.AdapterError(f"{kind} export has invalid Content-Length") from exc
                    if declared <= 0 or declared > max_compressed_bytes:
                        raise common.AdapterError(f"{kind} export Content-Length exceeds configured bound or is empty")
                fd, name = tempfile.mkstemp(prefix=f"hfeu-open-prices-{kind}-", suffix=".jsonl.gz")
                os.close(fd)
                temp_path = Path(name)
                digest = hashlib.sha256()
                total = 0
                with temp_path.open("wb") as output:
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_compressed_bytes:
                            raise common.AdapterError(f"{kind} export exceeds configured compressed byte limit")
                        digest.update(chunk)
                        output.write(chunk)
                if total == 0:
                    raise common.AdapterError(f"{kind} export was empty")
                if declared is not None and total != declared:
                    raise common.AdapterError(f"{kind} export byte count did not match Content-Length")
                return temp_path, {
                    "url": url,
                    "sha256": digest.hexdigest(),
                    "compressedBytes": total,
                    "etag": response.headers.get("ETag"),
                    "lastModified": response.headers.get("Last-Modified"),
                }
        except common.AdapterError:
            if temp_path:
                temp_path.unlink(missing_ok=True)
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_error = exc
            if temp_path:
                temp_path.unlink(missing_ok=True)
            if attempt >= retries:
                break
            time.sleep(min(5 * (2 ** attempt), 60))
    raise common.AdapterError(f"failed to acquire {kind} export after bounded retries: {last_error}")


def acquire(
    *,
    output: Path,
    metadata_output: Path,
    snapshot_id: str,
    mode: str,
    policy: common.SourcePolicy,
    fixtures: dict[str, Path] | None = None,
    sample_records: int = 10_000,
    max_compressed_bytes: int = MAX_DEFAULT_COMPRESSED_BYTES,
    max_expanded_bytes: int = MAX_DEFAULT_EXPANDED_BYTES,
    max_records: int = MAX_DEFAULT_RECORDS,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    if mode not in {"fixture", "sample", "full"}:
        raise common.AdapterError(f"unsupported acquisition mode {mode!r}")
    if not snapshot_id or len(snapshot_id) > 128 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for ch in snapshot_id):
        raise common.AdapterError("snapshot ID is not a safe bounded identifier")
    if sample_records <= 0 or max_compressed_bytes <= 0 or max_expanded_bytes <= 0 or max_records <= 0:
        raise common.AdapterError("acquisition bounds must be positive")

    output.parent.mkdir(parents=True, exist_ok=True)
    metadata_output.parent.mkdir(parents=True, exist_ok=True)
    retrieval = common.parse_retrieved_at(retrieved_at)
    fixtures = fixtures or common.DEFAULT_FIXTURES
    upstream: dict[str, Any] = {}
    counts: dict[str, int] = {kind: 0 for kind in common.EXPORT_KINDS}
    malformed: dict[str, int] = {kind: 0 for kind in common.EXPORT_KINDS}
    temp_paths: list[Path] = []

    try:
        payload_written = 0
        with output.open("w", encoding="utf-8", newline="\n") as destination:
            for kind in common.EXPORT_KINDS:
                if mode == "sample":
                    limit = sample_records
                else:
                    remaining = max_records - sum(counts.values())
                    if remaining <= 0:
                        raise common.AdapterError("combined Open Prices snapshot exceeds configured record limit")
                    limit = remaining
                if mode == "fixture":
                    path = fixtures[kind]
                    data = path.read_bytes()
                    upstream[kind] = {
                        "url": f"fixture:{path.as_posix()}",
                        "sha256": hashlib.sha256(data).hexdigest(),
                        "compressedBytes": len(data),
                        "etag": None,
                        "lastModified": None,
                    }
                    records = _fixture_records(path, record_limit=limit, max_expanded_bytes=max_expanded_bytes, allow_partial=mode == "sample")
                else:
                    temp, metadata = _download(kind, policy.export_urls[kind], policy, max_compressed_bytes=max_compressed_bytes)
                    temp_paths.append(temp)
                    upstream[kind] = metadata
                    stream = gzip.open(temp, "rb")
                    iterator = _iter_jsonl(stream, record_limit=limit, max_expanded_bytes=max_expanded_bytes, allow_partial=mode == "sample")

                    def generated() -> Iterator[dict[str, Any] | None]:
                        try:
                            for value, _ in iterator:
                                yield value
                        finally:
                            stream.close()

                    records = generated()

                seen = 0
                bad = 0
                for raw in records:
                    seen += 1
                    if raw is None:
                        bad += 1
                        continue
                    projected = _project(kind, raw)
                    serialized = json.dumps({"kind": kind[:-1], "record": projected}, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
                    payload_written += len(serialized.encode("utf-8"))
                    if payload_written > MAX_DEFAULT_PAYLOAD_BYTES:
                        raise common.AdapterError("combined Open Prices source snapshot exceeds workflow artifact byte limit")
                    destination.write(serialized)
                    counts[kind] += 1
                malformed[kind] = bad
                if seen and bad / seen > MAX_MALFORMED_RATE:
                    raise common.AdapterError(f"{kind} malformed-record rate exceeds {MAX_MALFORMED_RATE:.3%}")
                if seen == 0:
                    raise common.AdapterError(f"{kind} export emitted no bounded records")
                if mode != "fixture":
                    temp.unlink(missing_ok=True)
                    temp_paths.remove(temp)

        digest = hashlib.sha256()
        payload_bytes = 0
        with output.open("rb") as payload_stream:
            for chunk in iter(lambda: payload_stream.read(1024 * 1024), b""):
                digest.update(chunk)
                payload_bytes += len(chunk)
        complete = mode != "sample"
        metadata: dict[str, Any] = {
            "schemaVersion": 1,
            "sourceKey": common.SOURCE_KEY,
            "snapshotID": snapshot_id,
            "mode": mode,
            "retrievedAt": retrieval,
            "downloadComplete": complete,
            "recordsEmitted": sum(counts.values()),
            "recordCounts": counts,
            "malformedRecords": malformed,
            "payloadSha256": digest.hexdigest(),
            "payloadBytes": payload_bytes,
            "upstreamExports": upstream,
            "proofImageBinariesIncluded": False,
            "personalContributorFieldsIncluded": False,
            "noCompletenessClaim": True,
        }
        metadata_output.write_text(json.dumps(metadata, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        return metadata
    finally:
        for path in temp_paths:
            path.unlink(missing_ok=True)
