#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

command -v xcodegen >/dev/null 2>&1 || {
  echo "xcodegen is required. Install it with: brew install xcodegen" >&2
  exit 1
}

xcodebuild -version
swift --version
xcodegen --version

python3 Tools/catalog_builder.py \
  --input Data/sample-products.json \
  --database HalalFoodEU/Resources/catalog.sqlite3 \
  --manifest HalalFoodEU/Resources/catalog-manifest.json

python3 Tools/validate_catalog.py \
  --database HalalFoodEU/Resources/catalog.sqlite3 \
  --manifest HalalFoodEU/Resources/catalog-manifest.json \
  --source Data/sample-products.json

xcodegen generate

SIMULATOR_ID="$({ xcrun simctl list devices available -j; } | python3 -c '
import json
import re
import sys

data = json.load(sys.stdin)
runtimes = []
for runtime, devices in data.get("devices", {}).items():
    if ".iOS-" not in runtime:
        continue
    version = tuple(int(part) for part in re.findall(r"\d+", runtime.split("iOS-")[-1]))
    runtimes.append((version, devices))

for _, devices in sorted(runtimes, reverse=True):
    preferred = sorted(
        (device for device in devices if device.get("isAvailable") and device.get("name", "").startswith("iPhone")),
        key=lambda device: (
            "Pro" not in device.get("name", ""),
            "Plus" in device.get("name", ""),
            device.get("name", ""),
        ),
    )
    if preferred:
        print(preferred[0]["udid"])
        raise SystemExit(0)
raise SystemExit("No available iPhone simulator was found")
')"

xcrun simctl boot "$SIMULATOR_ID" 2>/dev/null || true
xcrun simctl bootstatus "$SIMULATOR_ID" -b

xcodebuild \
  -project HalalFoodEU.xcodeproj \
  -scheme HalalFoodEU \
  -configuration Debug \
  -destination "platform=iOS Simulator,id=${SIMULATOR_ID}" \
  -derivedDataPath .build/DerivedData \
  -enableCodeCoverage YES \
  CODE_SIGNING_ALLOWED=NO \
  test
