#!/usr/bin/env python3
"""Validate the repository-owned primary iOS AppIcon release asset."""

from __future__ import annotations

import argparse
import json
import re
import struct
import zlib
from pathlib import Path
from typing import Any

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_ICON_BYTES = 5 * 1024 * 1024
EXPECTED_IMAGE_ENTRY = {
    "filename": "AppIcon.png",
    "idiom": "universal",
    "platform": "ios",
    "size": "1024x1024",
}
PROJECT_BINDING = re.compile(
    r"(?m)^\s*ASSETCATALOG_COMPILER_APPICON_NAME:\s*[\"']?AppIcon[\"']?\s*$"
)
EMPTY_PROJECT_BINDING = re.compile(
    r"(?m)^\s*ASSETCATALOG_COMPILER_APPICON_NAME:\s*(?:[\"']{2})?\s*$"
)


class AppIconError(ValueError):
    """Raised when the shipping AppIcon contract is absent or malformed."""


def _load_json(path: Path) -> Any:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise AppIconError(f"missing required asset metadata: {path}") from exc
    if len(raw) > 64 * 1024:
        raise AppIconError(f"asset metadata exceeds the bounded size: {path}")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AppIconError(f"asset metadata is not strict UTF-8 JSON: {path}") from exc


def _validate_info(raw: Any, *, label: str) -> None:
    if not isinstance(raw, dict) or raw != {"author": "xcode", "version": 1}:
        raise AppIconError(f"{label} must use the canonical Xcode asset metadata")


def png_metadata(path: Path) -> dict[str, int]:
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise AppIconError(f"missing AppIcon PNG: {path}") from exc
    if not data or len(data) > MAX_ICON_BYTES:
        raise AppIconError("AppIcon PNG is empty or exceeds the bounded size")
    if not data.startswith(PNG_SIGNATURE):
        raise AppIconError("AppIcon source is not a PNG")

    offset = len(PNG_SIGNATURE)
    ihdr: bytes | None = None
    saw_iend = False
    saw_transparency = False
    chunk_index = 0

    while offset < len(data):
        if offset + 12 > len(data):
            raise AppIconError("AppIcon PNG has a truncated chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        kind = data[offset + 4 : offset + 8]
        start = offset + 8
        end = start + length
        crc_end = end + 4
        if crc_end > len(data):
            raise AppIconError("AppIcon PNG chunk exceeds the file boundary")

        payload = data[start:end]
        expected_crc = struct.unpack(">I", data[end:crc_end])[0]
        actual_crc = zlib.crc32(kind)
        actual_crc = zlib.crc32(payload, actual_crc) & 0xFFFFFFFF
        if expected_crc != actual_crc:
            raise AppIconError("AppIcon PNG contains an invalid chunk checksum")

        if chunk_index == 0 and kind != b"IHDR":
            raise AppIconError("AppIcon PNG must start with IHDR")
        if kind == b"IHDR":
            if ihdr is not None or len(payload) != 13:
                raise AppIconError("AppIcon PNG has invalid IHDR metadata")
            ihdr = payload
        elif kind == b"tRNS":
            saw_transparency = True
        elif kind == b"IEND":
            if length != 0:
                raise AppIconError("AppIcon PNG has invalid IEND metadata")
            saw_iend = True
            offset = crc_end
            if offset != len(data):
                raise AppIconError("AppIcon PNG contains data after IEND")
            break

        chunk_index += 1
        offset = crc_end

    if ihdr is None or not saw_iend:
        raise AppIconError("AppIcon PNG is missing required PNG structure")

    width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
        ">IIBBBBB", ihdr
    )
    if (width, height) != (1024, 1024):
        raise AppIconError("AppIcon source must be exactly 1024x1024 pixels")
    if bit_depth != 8 or color_type != 2:
        raise AppIconError("AppIcon source must be opaque 8-bit RGB without an alpha channel")
    if saw_transparency:
        raise AppIconError("AppIcon source must not declare PNG transparency")
    if (compression, filtering, interlace) != (0, 0, 0):
        raise AppIconError("AppIcon PNG uses unsupported encoding metadata")

    return {
        "width": width,
        "height": height,
        "bitDepth": bit_depth,
        "colorType": color_type,
        "bytes": len(data),
    }


def validate(root: Path) -> dict[str, Any]:
    catalog = root / "HalalFoodEU/Assets.xcassets"
    app_icon = catalog / "AppIcon.appiconset"

    catalog_metadata = _load_json(catalog / "Contents.json")
    if not isinstance(catalog_metadata, dict) or set(catalog_metadata) != {"info"}:
        raise AppIconError("asset catalog metadata contains unexpected fields")
    _validate_info(catalog_metadata.get("info"), label="asset catalog")

    icon_metadata = _load_json(app_icon / "Contents.json")
    if not isinstance(icon_metadata, dict) or set(icon_metadata) != {"images", "info"}:
        raise AppIconError("AppIcon metadata contains unexpected fields")
    _validate_info(icon_metadata.get("info"), label="AppIcon")
    if icon_metadata.get("images") != [EXPECTED_IMAGE_ENTRY]:
        raise AppIconError(
            "AppIcon must contain one canonical iOS single-size 1024x1024 source image"
        )

    png = png_metadata(app_icon / EXPECTED_IMAGE_ENTRY["filename"])

    project_path = root / "project.yml"
    try:
        project = project_path.read_text(encoding="utf-8")
    except (FileNotFoundError, UnicodeDecodeError) as exc:
        raise AppIconError("project.yml is missing or not strict UTF-8") from exc
    if len(PROJECT_BINDING.findall(project)) != 1 or EMPTY_PROJECT_BINDING.search(project):
        raise AppIconError("HalalFoodEU must bind exactly one primary AppIcon asset name")

    return {
        "assetCatalog": "HalalFoodEU/Assets.xcassets",
        "appIconSet": "AppIcon",
        "source": EXPECTED_IMAGE_ENTRY["filename"],
        "png": png,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = validate(args.root.resolve())
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
