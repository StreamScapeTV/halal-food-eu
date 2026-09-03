#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

command -v xcodegen >/dev/null 2>&1 || {
  echo "xcodegen is required. CI builds the reviewed source pin from Data/security/tooling-dependencies-v1.json." >&2
  exit 1
}

EXPECTED_XCODE_VERSION="${HFEU_EXPECTED_XCODE_VERSION:-26.6}"
if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  EXPECTED_XCODE_APP="/Applications/Xcode_${EXPECTED_XCODE_VERSION}.app"
  if [[ ! -d "$EXPECTED_XCODE_APP" ]]; then
    echo "Required stable Xcode ${EXPECTED_XCODE_VERSION} is not installed at ${EXPECTED_XCODE_APP}." >&2
    exit 1
  fi
  export DEVELOPER_DIR="${EXPECTED_XCODE_APP}/Contents/Developer"
fi

xcodebuild -version
swift --version
xcodegen --version

if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
  ACTUAL_XCODE_VERSION="$(xcodebuild -version | awk 'NR == 1 { print $2 }')"
  if [[ "$ACTUAL_XCODE_VERSION" != "$EXPECTED_XCODE_VERSION" ]]; then
    echo "Expected Xcode ${EXPECTED_XCODE_VERSION}, got ${ACTUAL_XCODE_VERSION}." >&2
    exit 1
  fi
fi

PREBUILT_DATABASE="${HFEU_PREBUILT_CATALOG_DATABASE:-}"
PREBUILT_MANIFEST="${HFEU_PREBUILT_CATALOG_MANIFEST:-}"
PREBUILT_MODE=false
if [[ -n "$PREBUILT_DATABASE" || -n "$PREBUILT_MANIFEST" ]]; then
  if [[ -z "$PREBUILT_DATABASE" || -z "$PREBUILT_MANIFEST" ]]; then
    echo "Both HFEU_PREBUILT_CATALOG_DATABASE and HFEU_PREBUILT_CATALOG_MANIFEST are required." >&2
    exit 1
  fi
  test -f "$PREBUILT_DATABASE"
  test -f "$PREBUILT_MANIFEST"
  cp "$PREBUILT_DATABASE" HalalFoodEU/Resources/catalog.sqlite3
  cp "$PREBUILT_MANIFEST" HalalFoodEU/Resources/catalog-manifest.json
  PREBUILT_MODE=true
else
  python3 Tools/build_production_fixture.py \
    --database HalalFoodEU/Resources/catalog.sqlite3 \
    --manifest HalalFoodEU/Resources/catalog-manifest.json \
    --source-commit "${GITHUB_SHA:-0000000000000000000000000000000000000000}" \
    --workflow-run "${GITHUB_RUN_ID:-local-ios-ci}"
fi

python3 Tools/production_catalog.py validate \
  --database HalalFoodEU/Resources/catalog.sqlite3 \
  --manifest HalalFoodEU/Resources/catalog-manifest.json

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

XCODEBUILD_ARGS=(
  -project HalalFoodEU.xcodeproj
  -scheme HalalFoodEU
  -configuration Debug
  -destination "platform=iOS Simulator,id=${SIMULATOR_ID}"
  -derivedDataPath .build/DerivedData
  -enableCodeCoverage YES
  CODE_SIGNING_ALLOWED=NO
)
if [[ "$PREBUILT_MODE" == "true" ]]; then
  XCODEBUILD_ARGS+=(-only-testing:HalalFoodEUTests/ProductionCatalogArtifactCompatibilityTests)
fi
XCODEBUILD_ARGS+=(test)

xcodebuild "${XCODEBUILD_ARGS[@]}"
