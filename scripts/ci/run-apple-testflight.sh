#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=apple-common.sh
source "${ROOT_DIR}/scripts/ci/apple-common.sh"

required_context=(
  CI_APPLE_TESTFLIGHT_BUILD_NUMBER
  CI_APPLE_TESTFLIGHT_AUTH_KEY_PATH
  CI_APPLE_TESTFLIGHT_TEMP_DIR
  CI_APPLE_TESTFLIGHT_TEAM_ID
  CI_APPLE_TESTFLIGHT_KEY_ID
  CI_APPLE_TESTFLIGHT_ISSUER_ID
)
for name in "${required_context[@]}"; do
  test -n "${!name:-}" || {
    printf 'Missing required fixed Central TestFlight context: %s\n' "${name}" >&2
    exit 2
  }
done

BUILD_NUMBER="${CI_APPLE_TESTFLIGHT_BUILD_NUMBER}"
if [[ ! "${BUILD_NUMBER}" =~ ^[1-9][0-9]{0,17}([.][0-9]{1,18}){0,2}$ ]]; then
  printf 'Halal Food EU TestFlight build number must be a positive numeric CFBundleVersion with at most three components.\n' >&2
  exit 2
fi

AUTH_KEY_PATH="${CI_APPLE_TESTFLIGHT_AUTH_KEY_PATH}"
RELEASE_ROOT="${CI_APPLE_TESTFLIGHT_TEMP_DIR}"
TEAM_ID="${CI_APPLE_TESTFLIGHT_TEAM_ID}"
KEY_ID="${CI_APPLE_TESTFLIGHT_KEY_ID}"
ISSUER_ID="${CI_APPLE_TESTFLIGHT_ISSUER_ID}"

test -f "${AUTH_KEY_PATH}" || {
  printf 'Central TestFlight authentication key file is missing.\n' >&2
  exit 2
}
test -d "${RELEASE_ROOT}" || {
  printf 'Central TestFlight temporary release directory is missing.\n' >&2
  exit 2
}
if [[ ! "${TEAM_ID}" =~ ^[A-Za-z0-9]{5,64}$ ]] || [[ ! "${KEY_ID}" =~ ^[A-Za-z0-9]{5,64}$ ]]; then
  printf 'Central TestFlight team/key identifiers are invalid.\n' >&2
  exit 2
fi
if (( ${#ISSUER_ID} > 128 )) || [[ "${ISSUER_ID}" =~ [[:space:]] ]]; then
  printf 'Central TestFlight issuer identifier is invalid.\n' >&2
  exit 2
fi

RECEIPT="${ROOT_DIR}/Data/catalog/production-catalog-release-input-v1.json"
test -f "${RECEIPT}" || {
  printf 'No accepted production catalog release receipt exists; refusing to package a synthetic catalog for TestFlight.\n' >&2
  exit 2
}
python3 "${ROOT_DIR}/Tools/production_catalog_release_input.py" validate --input "${RECEIPT}"

if [[ "${GITHUB_ACTIONS:-}" == "true" && "${GITHUB_REF:-}" != "refs/heads/main" ]]; then
  printf 'TestFlight publication is allowed only from protected main.\n' >&2
  exit 2
fi

SOURCE_SHA="$(git -C "${ROOT_DIR}" rev-parse HEAD)"
[[ "${SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]] || {
  printf 'Unable to resolve an exact source commit for TestFlight.\n' >&2
  exit 2
}

command -v gh >/dev/null 2>&1 || {
  printf 'GitHub CLI is required to resolve exact catalog release evidence.\n' >&2
  exit 2
}
test -n "${GH_TOKEN:-}" || {
  printf 'A GitHub token with read access to release evidence is required.\n' >&2
  exit 2
}

PRODUCT_REPOSITORY="StreamScapeTV/halal-food-eu"
CATALOG_RUN_ID="$(
  SOURCE_SHA="${SOURCE_SHA}" gh api \
    -H 'Accept: application/vnd.github+json' \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    "repos/${PRODUCT_REPOSITORY}/actions/workflows/catalog-release.yml/runs?event=push&branch=main&status=success&per_page=100" \
  | SOURCE_SHA="${SOURCE_SHA}" python3 -c '
import json
import os
import sys

payload = json.load(sys.stdin)
source_sha = os.environ["SOURCE_SHA"]
matches = [
    run
    for run in payload.get("workflow_runs", [])
    if run.get("head_sha") == source_sha
    and run.get("status") == "completed"
    and run.get("conclusion") == "success"
]
if not matches:
    raise SystemExit(2)
matches.sort(key=lambda run: (run.get("run_attempt", 0), run.get("id", 0)), reverse=True)
print(matches[0]["id"])
'
)" || {
  printf 'No successful exact-main catalog release-evidence run exists for %s. Dispatch catalog-release.yml first.\n' "${SOURCE_SHA}" >&2
  exit 2
}

CATALOG_ROOT="${RELEASE_ROOT}/catalog-release-evidence"
rm -rf -- "${CATALOG_ROOT}"
mkdir -p "${CATALOG_ROOT}"
ARTIFACT_NAME="release-evidence-${SOURCE_SHA}"
gh run download "${CATALOG_RUN_ID}" \
  --repo "${PRODUCT_REPOSITORY}" \
  --name "${ARTIFACT_NAME}" \
  --dir "${CATALOG_ROOT}"

DATABASE="${CATALOG_ROOT}/database/payload/catalog.sqlite3"
MANIFEST="${CATALOG_ROOT}/manifest/payload/catalog-manifest.json"
REPORT="${CATALOG_ROOT}/release/payload/release-report.json"
test -f "${DATABASE}" && test -f "${MANIFEST}" && test -f "${REPORT}" || {
  printf 'Exact catalog release-evidence artifact is incomplete.\n' >&2
  exit 2
}

python3 "${ROOT_DIR}/Tools/production_catalog.py" validate \
  --database "${DATABASE}" \
  --manifest "${MANIFEST}"

SOURCE_SHA="${SOURCE_SHA}" DATABASE="${DATABASE}" MANIFEST="${MANIFEST}" REPORT="${REPORT}" RECEIPT="${RECEIPT}" python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

source_sha = os.environ["SOURCE_SHA"]
database = Path(os.environ["DATABASE"])
manifest_path = Path(os.environ["MANIFEST"])
report_path = Path(os.environ["REPORT"])
receipt_path = Path(os.environ["RECEIPT"])

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
report = json.loads(report_path.read_text(encoding="utf-8"))
receipt = json.loads(receipt_path.read_text(encoding="utf-8"))

if report.get("commitSha") != source_sha or report.get("releaseMode") != "production":
    raise SystemExit("release evidence does not belong to this exact production source commit")
if manifest.get("sourceCommit") != source_sha:
    raise SystemExit("catalog manifest sourceCommit does not match this exact application source")
if manifest.get("catalogVersion") != receipt.get("catalogVersion"):
    raise SystemExit("catalog release evidence does not match the accepted release receipt")
database_sha = hashlib.sha256(database.read_bytes()).hexdigest()
manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
if report.get("databaseSha256") != database_sha or report.get("manifestSha256") != manifest_sha:
    raise SystemExit("catalog release evidence digest mismatch")
PY

cp "${DATABASE}" "${ROOT_DIR}/HalalFoodEU/Resources/catalog.sqlite3"
cp "${MANIFEST}" "${ROOT_DIR}/HalalFoodEU/Resources/catalog-manifest.json"
python3 "${ROOT_DIR}/Tools/production_catalog.py" validate \
  --database "${ROOT_DIR}/HalalFoodEU/Resources/catalog.sqlite3" \
  --manifest "${ROOT_DIR}/HalalFoodEU/Resources/catalog-manifest.json"

hfeu_select_reviewed_xcode
hfeu_bootstrap_reviewed_xcodegen "${ROOT_DIR}"
(
  cd "${ROOT_DIR}"
  xcodegen generate
)

ARCHIVE_PATH="${RELEASE_ROOT}/HalalFoodEU.xcarchive"
EXPORT_PATH="${RELEASE_ROOT}/export"
EXPORT_OPTIONS="${RELEASE_ROOT}/ExportOptions.plist"
rm -rf -- "${ARCHIVE_PATH}" "${EXPORT_PATH}"
mkdir -p "${EXPORT_PATH}"

xcodebuild archive \
  -project "${ROOT_DIR}/HalalFoodEU.xcodeproj" \
  -scheme HalalFoodEU \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath "${ARCHIVE_PATH}" \
  DEVELOPMENT_TEAM="${TEAM_ID}" \
  CODE_SIGN_STYLE=Automatic \
  CURRENT_PROJECT_VERSION="${BUILD_NUMBER}" \
  -allowProvisioningUpdates \
  -authenticationKeyPath "${AUTH_KEY_PATH}" \
  -authenticationKeyID "${KEY_ID}" \
  -authenticationKeyIssuerID "${ISSUER_ID}"

ARCHIVED_APP="${ARCHIVE_PATH}/Products/Applications/HalalFoodEU.app"
test -d "${ARCHIVED_APP}"
test -f "${ARCHIVED_APP}/catalog.sqlite3"
test -f "${ARCHIVED_APP}/catalog-manifest.json"
test "$(hfeu_sha256 "${ARCHIVED_APP}/catalog.sqlite3")" = "$(hfeu_sha256 "${DATABASE}")"
test "$(hfeu_sha256 "${ARCHIVED_APP}/catalog-manifest.json")" = "$(hfeu_sha256 "${MANIFEST}")"
test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "${ARCHIVED_APP}/Info.plist")" = "${BUILD_NUMBER}"

cat > "${EXPORT_OPTIONS}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>destination</key>
  <string>export</string>
  <key>manageAppVersionAndBuildNumber</key>
  <false/>
  <key>method</key>
  <string>app-store-connect</string>
  <key>signingStyle</key>
  <string>automatic</string>
  <key>teamID</key>
  <string>${TEAM_ID}</string>
</dict>
</plist>
EOF

xcodebuild -exportArchive \
  -archivePath "${ARCHIVE_PATH}" \
  -exportPath "${EXPORT_PATH}" \
  -exportOptionsPlist "${EXPORT_OPTIONS}" \
  -allowProvisioningUpdates \
  -authenticationKeyPath "${AUTH_KEY_PATH}" \
  -authenticationKeyID "${KEY_ID}" \
  -authenticationKeyIssuerID "${ISSUER_ID}"

IPA_COUNT="$(find "${EXPORT_PATH}" -maxdepth 1 -type f -name '*.ipa' | wc -l | tr -d '[:space:]')"
test "${IPA_COUNT}" = "1" || {
  printf 'Expected exactly one exported IPA, found %s.\n' "${IPA_COUNT}" >&2
  exit 2
}
IPA_PATH="$(find "${EXPORT_PATH}" -maxdepth 1 -type f -name '*.ipa' -print | head -n 1)"

VERIFY_ROOT="${RELEASE_ROOT}/ipa-verify"
rm -rf -- "${VERIFY_ROOT}"
mkdir -p "${VERIFY_ROOT}"
/usr/bin/unzip -q "${IPA_PATH}" -d "${VERIFY_ROOT}"
EXPORTED_APP_COUNT="$(find "${VERIFY_ROOT}/Payload" -maxdepth 1 -type d -name '*.app' | wc -l | tr -d '[:space:]')"
test "${EXPORTED_APP_COUNT}" = "1" || {
  printf 'Expected exactly one application in the exported IPA.\n' >&2
  exit 2
}
EXPORTED_APP="$(find "${VERIFY_ROOT}/Payload" -maxdepth 1 -type d -name '*.app' -print | head -n 1)"
test "$(hfeu_sha256 "${EXPORTED_APP}/catalog.sqlite3")" = "$(hfeu_sha256 "${DATABASE}")"
test "$(hfeu_sha256 "${EXPORTED_APP}/catalog-manifest.json")" = "$(hfeu_sha256 "${MANIFEST}")"
test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleVersion' "${EXPORTED_APP}/Info.plist")" = "${BUILD_NUMBER}"

xcrun altool \
  --upload-app \
  --type ios \
  --file "${IPA_PATH}" \
  --apiKey "${KEY_ID}" \
  --apiIssuer "${ISSUER_ID}" \
  --p8-file-path "${AUTH_KEY_PATH}"

printf 'Uploaded exact Halal Food EU TestFlight build %s from source %s with catalog %s.\n' \
  "${BUILD_NUMBER}" "${SOURCE_SHA}" "$(hfeu_sha256 "${DATABASE}")"
