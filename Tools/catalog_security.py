#!/usr/bin/env python3
"""Fail-closed security primitives for hostile catalog inputs and CI dependencies."""

from __future__ import annotations

import argparse
import csv
import io
import ipaddress
import json
import math
import re
import stat
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlsplit

SHA40 = re.compile(r"^[0-9a-f]{40}$")
ACTION_TARGET = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@(?P<sha>[0-9a-f]{40})(?:\s+#.*)?$")
USES_LINE = re.compile(r"^-?\s*uses\s*:\s*(?P<target>.+?)\s*$")
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
ANSI_ESCAPE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))")
FORMULA_PREFIXES = ("=", "+", "-", "@")


class SecurityError(ValueError):
    """Raised when hostile or unsupported input fails a security boundary."""


def _safe_error(label: str, reason: str) -> SecurityError:
    return SecurityError(f"{label}: {reason}")


def _is_disallowed_ip(host: str) -> bool:
    candidate = host.strip("[]")
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return any(
        (
            address.is_loopback,
            address.is_private,
            address.is_link_local,
            address.is_multicast,
            address.is_reserved,
            address.is_unspecified,
        )
    )


def validate_https_url(
    value: str,
    *,
    allowed_hosts: set[str] | frozenset[str],
    allowed_path_prefixes: tuple[str, ...] = ("/",),
    max_length: int = 2048,
) -> str:
    """Validate a configured outbound URL without DNS or network access."""
    if not isinstance(value, str) or not value or len(value) > max_length:
        raise _safe_error("URL", "missing or exceeds the configured length limit")
    if CONTROL.search(value) or "\\" in value:
        raise _safe_error("URL", "contains control characters or backslashes")

    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https":
        raise _safe_error("URL", "only HTTPS is allowed")
    if parsed.username is not None or parsed.password is not None:
        raise _safe_error("URL", "embedded credentials are forbidden")
    if parsed.fragment:
        raise _safe_error("URL", "fragments are forbidden")
    if not parsed.hostname:
        raise _safe_error("URL", "host is required")
    try:
        port = parsed.port
    except ValueError as exc:
        raise _safe_error("URL", "port is invalid") from exc
    if port not in {None, 443}:
        raise _safe_error("URL", "only the default HTTPS port is allowed")

    host = parsed.hostname.rstrip(".").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".local") or _is_disallowed_ip(host):
        raise _safe_error("URL", "local, private, link-local, reserved, or metadata targets are forbidden")

    normalized_hosts = {item.rstrip(".").lower() for item in allowed_hosts}
    if host not in normalized_hosts:
        raise _safe_error("URL", "host is not admitted by source policy")

    decoded_path = unquote(parsed.path or "/")
    if unquote(decoded_path) != decoded_path:
        raise _safe_error("URL", "path contains multiply encoded segments")
    if "\\" in decoded_path or CONTROL.search(decoded_path):
        raise _safe_error("URL", "path is unsafe")
    path_segments = decoded_path.split("/")
    if any(segment in {".", ".."} for segment in path_segments):
        raise _safe_error("URL", "path contains dot-segment traversal")
    if any(character in decoded_path for character in ("?", "#")):
        raise _safe_error("URL", "path contains encoded query or fragment delimiters")
    if not any(decoded_path == prefix or decoded_path.startswith(prefix.rstrip("/") + "/") for prefix in allowed_path_prefixes):
        raise _safe_error("URL", "path is outside the admitted source prefix")
    return value


def validate_resolved_addresses(addresses: list[str] | tuple[str, ...]) -> None:
    if not addresses:
        raise _safe_error("network", "host resolution returned no addresses")
    for value in addresses:
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise _safe_error("network", "host resolution returned an invalid address") from exc
        if any(
            (
                address.is_loopback,
                address.is_private,
                address.is_link_local,
                address.is_multicast,
                address.is_reserved,
                address.is_unspecified,
            )
        ):
            raise _safe_error("network", "resolved destination is not public")


def validate_redirect_chain(
    urls: list[str] | tuple[str, ...],
    *,
    allowed_hosts: set[str] | frozenset[str],
    allowed_path_prefixes: tuple[str, ...] = ("/",),
    max_redirects: int = 3,
) -> None:
    if not urls or len(urls) - 1 > max_redirects:
        raise _safe_error("network", "redirect chain exceeds the configured limit")
    for value in urls:
        validate_https_url(
            value,
            allowed_hosts=allowed_hosts,
            allowed_path_prefixes=allowed_path_prefixes,
        )


def read_bounded_stream(stream: Any, *, max_bytes: int, chunk_size: int = 64 * 1024) -> bytes:
    if max_bytes < 0 or chunk_size <= 0:
        raise _safe_error("network", "invalid response bounds")
    output = bytearray()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        output.extend(chunk)
        if len(output) > max_bytes:
            raise _safe_error("network", "response exceeds the configured byte limit")
    return bytes(output)


def load_bounded_csv(
    path: Path,
    *,
    max_bytes: int,
    max_rows: int = 1_000_000,
    max_columns: int = 256,
    max_field_length: int = 1_000_000,
) -> list[list[str]]:
    size = path.stat().st_size
    if size > max_bytes:
        raise _safe_error("CSV", "input exceeds the configured byte limit")
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise _safe_error("CSV", "input exceeds the configured byte limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _safe_error("CSV", "input is not strict UTF-8") from exc
    if "\x00" in text:
        raise _safe_error("CSV", "input contains a NUL byte")

    previous_limit = csv.field_size_limit()
    csv.field_size_limit(max_field_length)
    rows: list[list[str]] = []
    try:
        for index, row in enumerate(csv.reader(io.StringIO(text, newline=""), strict=True), start=1):
            if index > max_rows:
                raise _safe_error("CSV", "input exceeds the configured row limit")
            if len(row) > max_columns:
                raise _safe_error("CSV", "row exceeds the configured column limit")
            for field in row:
                if len(field) > max_field_length or CONTROL.search(field):
                    raise _safe_error("CSV", "field exceeds limits or contains forbidden controls")
            rows.append(row)
    except csv.Error as exc:
        raise _safe_error("CSV", "input is malformed") from exc
    finally:
        csv.field_size_limit(previous_limit)
    return rows


def require_media_type(value: str, *, allowed: set[str] | frozenset[str]) -> str:
    media_type = value.split(";", 1)[0].strip().lower()
    if media_type not in {item.lower() for item in allowed}:
        raise _safe_error("content type", "payload format is not admitted by source policy")
    return media_type


def load_bounded_json(
    path: Path,
    *,
    max_bytes: int,
    max_depth: int = 32,
    max_string_length: int = 1_000_000,
    max_collection_items: int = 1_000_000,
    max_nodes: int = 2_000_000,
) -> Any:
    """Read strict UTF-8 JSON with deterministic byte/shape/string limits."""
    size = path.stat().st_size
    if size > max_bytes:
        raise _safe_error("JSON", "input exceeds the configured byte limit")
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        raise _safe_error("JSON", "input exceeds the configured byte limit")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise _safe_error("JSON", "input is not strict UTF-8") from exc

    def reject_constant(_: str) -> Any:
        raise _safe_error("JSON", "non-finite numeric constants are forbidden")

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        output: dict[str, Any] = {}
        for key, item in pairs:
            if key in output:
                raise _safe_error("JSON", "duplicate object keys are forbidden")
            output[key] = item
        return output

    try:
        value = json.loads(
            text,
            parse_constant=reject_constant,
            object_pairs_hook=unique_object,
        )
    except SecurityError:
        raise
    except json.JSONDecodeError as exc:
        raise _safe_error("JSON", "input is malformed") from exc

    nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > max_nodes:
            raise _safe_error("JSON", "input exceeds the configured node limit")
        if depth > max_depth:
            raise _safe_error("JSON", "input exceeds the configured nesting limit")
        if isinstance(item, str):
            if len(item) > max_string_length:
                raise _safe_error("JSON", "string exceeds the configured length limit")
            if CONTROL.search(item):
                raise _safe_error("JSON", "decoded string contains forbidden control characters")
        elif isinstance(item, dict):
            if len(item) > max_collection_items:
                raise _safe_error("JSON", "object exceeds the configured item limit")
            for key, nested in item.items():
                if not isinstance(key, str):
                    raise _safe_error("JSON", "object key is not text")
                visit(key, depth + 1)
                visit(nested, depth + 1)
        elif isinstance(item, list):
            if len(item) > max_collection_items:
                raise _safe_error("JSON", "array exceeds the configured item limit")
            for nested in item:
                visit(nested, depth + 1)
        elif isinstance(item, float):
            if not math.isfinite(item):
                raise _safe_error("JSON", "numeric value is not finite")
        elif item is None or isinstance(item, (bool, int)):
            return
        else:
            raise _safe_error("JSON", "contains an unsupported value type")

    visit(value, 0)
    return value


def _safe_zip_member(name: str) -> PurePosixPath:
    if not name or CONTROL.search(name) or "\\" in name:
        raise _safe_error("archive", "member path is unsafe")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise _safe_error("archive", "member path is absolute or traverses directories")
    return path


def extract_bounded_zip(
    archive: Path,
    destination: Path,
    *,
    max_entries: int = 10_000,
    max_compressed_bytes: int = 512 * 1024 * 1024,
    max_expanded_bytes: int = 2 * 1024 * 1024 * 1024,
    max_file_bytes: int = 256 * 1024 * 1024,
    max_compression_ratio: int = 200,
) -> list[Path]:
    """Extract regular files only while enforcing path and decompression limits."""
    destination.mkdir(parents=True, exist_ok=True)
    destination_root = destination.resolve()
    extracted: list[Path] = []
    expanded_total = 0
    compressed_total = 0

    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > max_entries:
            raise _safe_error("archive", "contains too many entries")
        for info in entries:
            path = _safe_zip_member(info.filename)
            unix_mode = info.external_attr >> 16
            file_type = stat.S_IFMT(unix_mode)
            if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise _safe_error("archive", "contains a symlink, device, or other non-regular entry")
            if info.flag_bits & 0x1:
                raise _safe_error("archive", "encrypted entries are unsupported")
            if info.file_size > max_file_bytes:
                raise _safe_error("archive", "entry exceeds the configured expanded-file limit")
            expanded_total += info.file_size
            compressed_total += info.compress_size
            if expanded_total > max_expanded_bytes or compressed_total > max_compressed_bytes:
                raise _safe_error("archive", "archive exceeds configured aggregate limits")
            if info.file_size and info.compress_size == 0:
                raise _safe_error("archive", "entry has an invalid zero compressed size")
            if info.compress_size and info.file_size / info.compress_size > max_compression_ratio:
                raise _safe_error("archive", "entry exceeds the configured compression ratio")

            target = (destination_root / Path(*path.parts)).resolve()
            try:
                target.relative_to(destination_root)
            except ValueError as exc:
                raise _safe_error("archive", "member escapes extraction root") from exc

            if info.is_dir() or file_type == stat.S_IFDIR:
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            with bundle.open(info, "r") as source, target.open("wb") as output:
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    written += len(chunk)
                    if written > max_file_bytes or written > info.file_size:
                        raise _safe_error("archive", "entry exceeded declared or configured size")
                    output.write(chunk)
            if written != info.file_size:
                raise _safe_error("archive", "entry size did not match archive metadata")
            extracted.append(target)
    return extracted


def sanitize_log_text(value: str, *, max_length: int = 512) -> str:
    """Return one bounded terminal-safe line without echoing raw control sequences."""
    clean = ANSI_ESCAPE.sub("", str(value))
    clean = CONTROL.sub("?", clean).replace("\r", " ").replace("\n", " ").replace("\t", " ")
    if len(clean) > max_length:
        clean = clean[: max(0, max_length - 1)] + "…"
    return clean


def protect_csv_cell(value: str) -> str:
    """Neutralize spreadsheet formula interpretation while preserving visible text."""
    text = str(value)
    probe = text.lstrip()
    if text.startswith(("\t", "\r", "\n")) or probe.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def assert_no_secret_canaries(text: str, canaries: tuple[str, ...] | list[str]) -> None:
    for canary in canaries:
        if canary and canary in text:
            raise _safe_error("secret scan", "a configured canary was found in generated output")


def reject_product_image_bytes(data: bytes) -> None:
    """Keep the current product-image boundary metadata-only."""
    if data:
        raise _safe_error("product image", "binary image payloads are outside the admitted catalog contract")


def _load_dependency_manifest(path: Path) -> dict[str, Any]:
    raw = load_bounded_json(path, max_bytes=256 * 1024, max_depth=8, max_string_length=512, max_collection_items=100)
    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        raise _safe_error("tooling manifest", "unsupported schema")
    if set(raw) != {"schemaVersion", "reviewedAt", "pythonRuntimeDependencies", "xcodegen", "githubActions"}:
        raise _safe_error("tooling manifest", "unexpected fields")
    if raw["pythonRuntimeDependencies"] != []:
        raise _safe_error("tooling manifest", "builder tooling must remain standard-library-only")
    xcodegen = raw["xcodegen"]
    if not isinstance(xcodegen, dict) or not SHA40.fullmatch(str(xcodegen.get("commitSha", ""))):
        raise _safe_error("tooling manifest", "XcodeGen commit pin is invalid")
    return raw


def tooling_sbom(root: Path, dependency_manifest: Path) -> dict[str, Any]:
    manifest = _load_dependency_manifest(dependency_manifest)
    declared_actions = manifest["githubActions"]
    if not isinstance(declared_actions, dict):
        raise _safe_error("tooling manifest", "githubActions must be an object")

    observed: dict[str, str] = {}
    for workflow in sorted((root / ".github/workflows").glob("*.yml")):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            uses_match = USES_LINE.fullmatch(stripped)
            if not uses_match:
                continue
            target = uses_match.group("target").strip()
            if target.startswith("./"):
                continue
            match = ACTION_TARGET.fullmatch(target)
            if not match:
                raise _safe_error("workflow dependency", "an external action is not pinned to a full commit")
            name, sha = match.group("name"), match.group("sha")
            declared = declared_actions.get(name)
            if not isinstance(declared, dict) or declared.get("commitSha") != sha:
                raise _safe_error("workflow dependency", "an external action is absent from or differs from the reviewed manifest")
            observed[name] = sha

    if set(observed) != set(declared_actions):
        raise _safe_error("workflow dependency", "reviewed action manifest contains an unused or missing dependency")

    return {
        "schemaVersion": 1,
        "reviewedAt": manifest["reviewedAt"],
        "pythonRuntimeDependencies": [],
        "xcodegen": manifest["xcodegen"],
        "githubActions": [
            {
                "repository": name,
                "version": declared_actions[name]["version"],
                "commitSha": observed[name],
            }
            for name in sorted(observed)
        ],
    }


def source_policy_identity(source_policy: Path) -> dict[str, Any]:
    raw_bytes = source_policy.read_bytes()
    raw = load_bounded_json(
        source_policy,
        max_bytes=4 * 1024 * 1024,
        max_depth=16,
        max_string_length=4096,
        max_collection_items=100_000,
    )
    if not isinstance(raw, dict) or raw.get("schemaVersion") != 1:
        raise _safe_error("source policy", "unsupported or missing schemaVersion")
    contract_version = raw.get("contractVersion")
    if not isinstance(contract_version, str) or not contract_version:
        raise _safe_error("source policy", "missing contractVersion")
    import hashlib
    return {
        "schemaVersion": 1,
        "contractVersion": contract_version,
        "sha256": hashlib.sha256(raw_bytes).hexdigest(),
    }


def bind_manifest_source_policy(manifest_path: Path, source_policy: Path) -> dict[str, Any]:
    manifest = load_bounded_json(
        manifest_path,
        max_bytes=16 * 1024 * 1024,
        max_depth=16,
        max_string_length=1_000_000,
        max_collection_items=100_000,
    )
    if not isinstance(manifest, dict):
        raise _safe_error("catalog manifest", "root must be an object")
    manifest["sourcePolicy"] = source_policy_identity(source_policy)
    temporary = manifest_path.with_name(manifest_path.name + ".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(manifest_path)
    return manifest


def validate_manifest_source_policy(manifest_path: Path, source_policy: Path) -> dict[str, Any]:
    manifest = load_bounded_json(
        manifest_path,
        max_bytes=16 * 1024 * 1024,
        max_depth=16,
        max_string_length=1_000_000,
        max_collection_items=100_000,
    )
    if not isinstance(manifest, dict) or manifest.get("sourcePolicy") != source_policy_identity(source_policy):
        raise _safe_error("catalog manifest", "source-policy identity is missing or does not match reviewed policy")
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    sbom = subcommands.add_parser("tooling-sbom", help="validate reviewed tool pins and emit a deterministic SBOM")
    sbom.add_argument("--root", type=Path, default=Path("."))
    sbom.add_argument(
        "--dependency-manifest",
        type=Path,
        default=Path("Data/security/tooling-dependencies-v1.json"),
    )
    sbom.add_argument("--output", type=Path, required=True)

    bind_manifest = subcommands.add_parser("bind-manifest", help="bind a catalog manifest to the reviewed source policy")
    bind_manifest.add_argument("--manifest", type=Path, required=True)
    bind_manifest.add_argument("--source-policy", type=Path, required=True)

    validate_manifest = subcommands.add_parser("validate-manifest", help="verify catalog manifest source-policy binding")
    validate_manifest.add_argument("--manifest", type=Path, required=True)
    validate_manifest.add_argument("--source-policy", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.command == "tooling-sbom":
        payload = tooling_sbom(args.root, args.dependency_manifest)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Wrote reviewed tooling SBOM to {args.output}")
    elif args.command == "bind-manifest":
        bind_manifest_source_policy(args.manifest, args.source_policy)
        print(f"Bound {args.manifest} to reviewed source policy")
    elif args.command == "validate-manifest":
        validate_manifest_source_policy(args.manifest, args.source_policy)
        print(f"Validated source-policy binding for {args.manifest}")


if __name__ == "__main__":
    main()
