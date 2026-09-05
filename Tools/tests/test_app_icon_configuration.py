from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
import zlib
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import app_icon


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind)
    crc = zlib.crc32(payload, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


def _write_png(path: Path, *, width: int = 1024, height: int = 1024, color_type: int = 2) -> None:
    channels = 3 if color_type == 2 else 4
    pixel = b"\x10\x68\x4e" if channels == 3 else b"\x10\x68\x4e\xff"
    row = b"\x00" + pixel * width
    raw = row * height
    ihdr = struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0)
    path.write_bytes(
        app_icon.PNG_SIGNATURE
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def _write_fixture(root: Path, *, width: int = 1024, color_type: int = 2, binding: str = "AppIcon") -> None:
    catalog = root / "HalalFoodEU/Assets.xcassets"
    icon = catalog / "AppIcon.appiconset"
    icon.mkdir(parents=True)
    (catalog / "Contents.json").write_text(
        json.dumps({"info": {"author": "xcode", "version": 1}}) + "\n",
        encoding="utf-8",
    )
    (icon / "Contents.json").write_text(
        json.dumps(
            {
                "images": [app_icon.EXPECTED_IMAGE_ENTRY],
                "info": {"author": "xcode", "version": 1},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    _write_png(icon / "AppIcon.png", width=width, height=1024, color_type=color_type)
    (root / "project.yml").write_text(
        "targets:\n"
        "  HalalFoodEU:\n"
        "    settings:\n"
        "      base:\n"
        f"        ASSETCATALOG_COMPILER_APPICON_NAME: {binding}\n",
        encoding="utf-8",
    )


class AppIconConfigurationTests(unittest.TestCase):
    def test_committed_primary_app_icon_contract_is_valid(self) -> None:
        report = app_icon.validate(ROOT)
        self.assertEqual(report["appIconSet"], "AppIcon")
        self.assertEqual(report["png"]["width"], 1024)
        self.assertEqual(report["png"]["height"], 1024)
        self.assertEqual(report["png"]["colorType"], 2)

    def test_wrong_dimensions_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root, width=512)
            with self.assertRaisesRegex(app_icon.AppIconError, "1024x1024"):
                app_icon.validate(root)

    def test_alpha_channel_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root, color_type=6)
            with self.assertRaisesRegex(app_icon.AppIconError, "opaque"):
                app_icon.validate(root)

    def test_missing_project_binding_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root, binding='""')
            with self.assertRaisesRegex(app_icon.AppIconError, "bind exactly one"):
                app_icon.validate(root)

    def test_mutated_asset_metadata_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _write_fixture(root)
            metadata = root / "HalalFoodEU/Assets.xcassets/AppIcon.appiconset/Contents.json"
            raw = json.loads(metadata.read_text(encoding="utf-8"))
            raw["images"][0]["size"] = "512x512"
            metadata.write_text(json.dumps(raw) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(app_icon.AppIconError, "single-size"):
                app_icon.validate(root)


if __name__ == "__main__":
    unittest.main()
