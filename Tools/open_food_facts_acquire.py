"""Streaming, bounded acquisition for Open Food Facts bulk JSONL exports."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Iterator
from urllib.parse import urlparse

from manufacturer_evidence import METADATA_KEY, project_source_record
from open_food_facts_common import (
    AdapterError,
    DEFAULT_FIXTURE,
    EXPECTED_API_VERSION,
    EXPECTED_PRODUCT_SCHEMA_VERSION,
    SELECTION_MARKET,
    SOURCE_KEY,
    SourcePolicy,
    market_for_record,
)

MAX_LINE_BYTES = 4 * 1024 * 1024
MAX_DEFAULT_COMPRESSED_BYTES = 16 * 1024 * 1024 * 1024
MAX_DEFAULT_EXPANDED_BYTES = 128 * 1024 * 1024 * 1024
MAX_DEFAULT_RECORDS = 10_000_000
MIN_FULL_COMPRESSED_BYTES = 256 * 1024 * 1024
MAX_DEFAULT_MALFORMED_RATE = 0.001
DEFAULT_RETRY_DELAYS = (5, 10, 20, 40)


class _DigestingReader:
    def __init__(self, raw: BinaryIO, max_bytes: int) -> None:
        self.raw = raw
        self.max_bytes = max_bytes
        self.sha256 = hashlib.sha256()
        self.bytes_read = 0

    def _account(self, chunk: bytes) -> bytes:
        if chunk:
            self.bytes_read += len(chunk)
            if self.bytes_read > self.max_bytes:
                raise AdapterError(f"compressed export exceeded {self.max_bytes} bytes")
            self.sha256.update(chunk)
        return chunk

    def read(self, size: int = -1) -> bytes:
        return self._account(self.raw.read(size))

    def readline(self, size: int = -1) -> bytes:
        return self._account(self.raw.readline(size))

    def __iter__(self) -> "_DigestingReader":
        return self

    def __next__(self) -> bytes:
        chunk = self.readline()
        if not chunk:
            raise StopIteration
        return chunk

    def close(self) -> None:
        close = getattr(self.raw, "close", None)
        if close is not None:
            close()


class _AllowlistedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects outside the reviewed HTTPS acquisition-host allowlist."""

    def __init__(self, allowed_hosts: tuple[str, ...]) -> None:
        super().__init__()
        self.allowed_hosts = frozenset(host.casefold() for host in allowed_hosts)

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: BinaryIO,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        parsed = urlparse(newurl)
        hostname = parsed.hostname.casefold() if parsed.hostname else None
        if (
            parsed.scheme != "https"
            or hostname not in self.allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise AdapterError(f"redirect target is not an admitted HTTPS OFF host: {newurl!r}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


@dataclass
class Counters:
    examined: int = 0
    emitted: int = 0
    wrong_market: int = 0
    unsupported_schema: int = 0
    malformed: int = 0
    oversized: int = 0
    lines_seen: int = 0
    expanded_bytes: int = 0

    @property
    def malformed_rate(self) -> float:
        denominator = self.examined + self.malformed
        return 0.0 if denominator == 0 else self.malformed / denominator


def _retry_after_seconds(headers: Any, fallback: int) -> int:
    value = headers.get("Retry-After") if headers is not None else None
    if isinstance(value, str) and value.strip().isdigit():
        return min(max(int(value), 1), 60)
    return fallback


def _open_network_export(
    url: str,
    policy: SourcePolicy,
    user_agent: str,
    *,
    retry_delays: tuple[int, ...] = DEFAULT_RETRY_DELAYS,
    sleeper=time.sleep,
) -> tuple[BinaryIO, dict[str, str], int]:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in policy.acquisition_hosts:
        raise AdapterError("acquisition URL is not an admitted HTTPS OFF host")
    request = urllib.request.Request(
        url,
        headers={"User-Agent": user_agent, "Accept": "application/gzip, application/octet-stream"},
        method="GET",
    )
    opener = urllib.request.build_opener(_AllowlistedRedirectHandler(policy.acquisition_hosts))
    attempts = len(retry_delays) + 1
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = opener.open(request, timeout=120)
            status = getattr(response, "status", 200)
            if status != 200:
                response.close()
                raise AdapterError(f"Open Food Facts export returned unexpected HTTP {status}")
            headers = {
                key.lower(): value
                for key, value in response.headers.items()
                if key.lower() in {"etag", "last-modified", "content-length", "content-type"}
            }
            return response, headers, status
        except urllib.error.HTTPError as exc:
            last_error = exc
            if exc.code != 429 and not 500 <= exc.code <= 599:
                raise AdapterError(f"Open Food Facts export returned HTTP {exc.code}") from exc
            if attempt >= len(retry_delays):
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                suffix = f"; Retry-After={retry_after}" if retry_after else ""
                raise AdapterError(f"Open Food Facts export returned HTTP {exc.code}{suffix} after retries") from exc
            sleeper(_retry_after_seconds(exc.headers, retry_delays[attempt]))
        except urllib.error.URLError as exc:
            last_error = exc
            if attempt >= len(retry_delays):
                raise AdapterError(f"failed to acquire Open Food Facts export after retries: {exc}") from exc
            sleeper(retry_delays[attempt])
    raise AdapterError(f"failed to acquire Open Food Facts export: {last_error}")


def _account_expanded(counters: Counters, chunk: bytes, max_expanded_bytes: int) -> None:
    counters.expanded_bytes += len(chunk)
    if counters.expanded_bytes > max_expanded_bytes:
        raise AdapterError(f"expanded export exceeded {max_expanded_bytes} bytes")


def _drain_oversized_line(
    stream: BinaryIO,
    counters: Counters,
    max_expanded_bytes: int,
) -> None:
    while True:
        tail = stream.readline(MAX_LINE_BYTES + 1)
        if not tail:
            return
        _account_expanded(counters, tail, max_expanded_bytes)
        if tail.endswith(b"\n"):
            return


def _iter_json_lines(
    stream: BinaryIO,
    counters: Counters,
    *,
    max_expanded_bytes: int,
    max_records: int,
) -> Iterator[dict[str, Any]]:
    while True:
        raw = stream.readline(MAX_LINE_BYTES + 1)
        if not raw:
            return
        counters.lines_seen += 1
        if counters.lines_seen > max_records:
            raise AdapterError(f"expanded export exceeded {max_records} logical records")
        _account_expanded(counters, raw, max_expanded_bytes)
        if len(raw) > MAX_LINE_BYTES:
            counters.malformed += 1
            counters.oversized += 1
            if not raw.endswith(b"\n"):
                _drain_oversized_line(stream, counters, max_expanded_bytes)
            continue
        if not raw.strip():
            continue
        try:
            value = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            counters.malformed += 1
            continue
        if not isinstance(value, dict):
            counters.malformed += 1
            continue
        counters.examined += 1
        yield value


def _schema_version(record: dict[str, Any]) -> str | None:
    value = record.get("schema_version")
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value
    return None


def acquire(
    *,
    output: Path,
    snapshot_id: str,
    mode: str,
    policy: SourcePolicy,
    fixture: Path = DEFAULT_FIXTURE,
    url: str | None = None,
    user_agent: str | None = None,
    sample_records: int = 10_000,
    max_compressed_bytes: int = MAX_DEFAULT_COMPRESSED_BYTES,
    max_expanded_bytes: int = MAX_DEFAULT_EXPANDED_BYTES,
    max_records: int = MAX_DEFAULT_RECORDS,
    max_malformed_rate: float = MAX_DEFAULT_MALFORMED_RATE,
    retrieved_at: str | None = None,
) -> dict[str, Any]:
    if mode not in {"fixture", "sample", "full"}:
        raise AdapterError(f"unsupported acquisition mode {mode!r}")
    if not snapshot_id or len(snapshot_id) > 120:
        raise AdapterError("snapshot_id must be a non-empty bounded identifier")
    if (
        sample_records < 1
        or max_compressed_bytes < 1
        or max_expanded_bytes < 1
        or max_records < 1
        or not 0 <= max_malformed_rate <= 0.05
    ):
        raise AdapterError("invalid acquisition bounds")

    counters = Counters()
    schemas: Counter[str] = Counter()
    retrieved_at = retrieved_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    http_headers: dict[str, str] = {}
    http_status: int | None = None
    source_url = url or policy.export_url
    transport_encoding = "identity-fixture"
    download_complete = True
    raw_stream: BinaryIO | None = None
    decoded_stream: BinaryIO | None = None
    digest_reader: _DigestingReader | None = None
    output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = output.with_name(output.name + ".tmp")
    temp_output.unlink(missing_ok=True)

    try:
        if mode == "fixture":
            raw_stream = fixture.open("rb")
            digest_reader = _DigestingReader(raw_stream, max_compressed_bytes)
            decoded_stream = digest_reader
            source_url = f"fixture:{fixture.as_posix()}"
        else:
            agent = user_agent or os.environ.get("OPEN_FOOD_FACTS_USER_AGENT", "").strip()
            if not agent:
                raise AdapterError(
                    "OPEN_FOOD_FACTS_USER_AGENT is required for network acquisition "
                    "(format: app/version (URL or contact))"
                )
            raw_stream, http_headers, http_status = _open_network_export(source_url, policy, agent)
            digest_reader = _DigestingReader(raw_stream, max_compressed_bytes)
            decoded_stream = gzip.GzipFile(fileobj=digest_reader, mode="rb")
            transport_encoding = "gzip"

        with temp_output.open("wb") as destination:
            try:
                for record in _iter_json_lines(
                    decoded_stream,
                    counters,
                    max_expanded_bytes=max_expanded_bytes,
                    max_records=max_records,
                ):
                    version = _schema_version(record)
                    schemas[version or "missing"] += 1
                    market = market_for_record(record)
                    if market != SELECTION_MARKET:
                        counters.wrong_market += 1
                        if mode != "fixture":
                            continue
                    if version != policy.product_schema_version:
                        counters.unsupported_schema += 1
                        continue
                    projected = project_source_record(record)
                    destination.write(json.dumps(projected, ensure_ascii=False, separators=(",", ":")).encode("utf-8") + b"\n")
                    counters.emitted += 1
                    if mode == "sample" and counters.emitted >= sample_records:
                        download_complete = False
                        break
            except (EOFError, gzip.BadGzipFile, OSError) as exc:
                raise AdapterError(f"compressed export is truncated or corrupt: {exc}") from exc

            if mode == "full" and counters.examined == 0:
                raise AdapterError("full export contained no JSON product records")
            if counters.malformed_rate > max_malformed_rate:
                raise AdapterError(
                    f"malformed rate {counters.malformed_rate:.6f} exceeds {max_malformed_rate:.6f}"
                )
            if mode == "fixture" and counters.unsupported_schema:
                raise AdapterError(
                    f"fixture contains {counters.unsupported_schema} unsupported schema records; "
                    f"expected {policy.product_schema_version}"
                )
            if mode == "full" and counters.emitted == 0:
                raise AdapterError(
                    f"full export contained no {SELECTION_MARKET} records on supported schema "
                    f"{policy.product_schema_version}"
                )

            assert digest_reader is not None
            if mode == "full":
                expected = http_headers.get("content-length")
                if expected and expected.isdigit() and int(expected) != digest_reader.bytes_read:
                    raise AdapterError(
                        f"truncated export: Content-Length={expected}, received={digest_reader.bytes_read}"
                    )
                if digest_reader.bytes_read < MIN_FULL_COMPRESSED_BYTES:
                    raise AdapterError(
                        f"full export unexpectedly small ({digest_reader.bytes_read} bytes); refusing partial snapshot"
                    )

            metadata = {
                "sourceKey": SOURCE_KEY,
                "snapshotID": snapshot_id,
                "mode": mode,
                "exportURL": source_url,
                "retrievedAt": retrieved_at,
                "httpStatus": http_status,
                "httpMetadata": http_headers,
                "transportEncoding": transport_encoding,
                "transportSha256": digest_reader.sha256.hexdigest(),
                "transportBytes": digest_reader.bytes_read,
                "expandedBytes": counters.expanded_bytes,
                "recordsSeen": counters.lines_seen,
                "downloadComplete": download_complete,
                "sourceSchemaVersions": dict(sorted(schemas.items())),
                "expectedProductSchemaVersion": policy.product_schema_version,
                "apiVersion": policy.api_version,
                "tagSchema": policy.raw["tagSchema"],
                "recordsExamined": counters.examined,
                "recordsEmitted": counters.emitted,
                "coarseExcludedWrongMarket": counters.wrong_market,
                "unsupportedSchemaRecords": counters.unsupported_schema,
                "malformedRecords": counters.malformed,
                "oversizedLines": counters.oversized,
                "malformedRate": counters.malformed_rate,
                "noCompletenessClaim": True,
                "imageBinaryDownloads": False,
                "licenseIdentifiers": {
                    "database": policy.raw["databaseLicense"]["identifier"],
                    "contents": policy.raw["databaseContentsLicense"]["identifier"],
                    "images": policy.raw["imageLicense"]["identifier"],
                },
            }
            destination.write(
                json.dumps({METADATA_KEY: metadata}, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            )
        temp_output.replace(output)
        return metadata
    except Exception:
        temp_output.unlink(missing_ok=True)
        raise
    finally:
        if decoded_stream is not None:
            try:
                decoded_stream.close()
            except OSError:
                pass
        elif raw_stream is not None:
            try:
                raw_stream.close()
            except OSError:
                pass
