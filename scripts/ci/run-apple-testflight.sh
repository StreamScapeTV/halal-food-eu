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

SOURCE_IS_TAG="${CI_APPLE_TESTFLIGHT_SOURCE_IS_TAG:-false}"
RELEASE_VERSION="${CI_APPLE_TESTFLIGHT_RELEASE_VERSION:-}"
case "${SOURCE_IS_TAG}" in
  true|false) ;;
  *)
    printf 'CI_APPLE_TESTFLIGHT_SOURCE_IS_TAG must be true or false.\n' >&2
    exit 2
    ;;
esac

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

PROJECT_IDENTITY="$(
  PROJECT_FILE="${ROOT_DIR}/project.yml" python3 - <<'PY_PROJECT_IDENTITY'
import os
import re
from pathlib import Path

path = Path(os.environ["PROJECT_FILE"])
try:
    text = path.read_text(encoding="utf-8")
except (OSError, UnicodeDecodeError) as exc:
    raise SystemExit(f"failed to read canonical project.yml release identity: {exc}") from exc
patterns = {
    "marketing": re.compile(r'^\s*MARKETING_VERSION:\s*"?([^"\s#]+)"?\s*$', re.MULTILINE),
    "build": re.compile(r'^\s*CURRENT_PROJECT_VERSION:\s*"?([^"\s#]+)"?\s*$', re.MULTILINE),
}
values = {}
for label, pattern in patterns.items():
    matches = pattern.findall(text)
    if len(matches) != 1:
        raise SystemExit(f"project.yml must contain exactly one canonical {label} release value")
    values[label] = matches[0]
print(values["marketing"])
print(values["build"])
PY_PROJECT_IDENTITY
)" || exit 2
PROJECT_MARKETING_VERSION="$(printf '%s\n' "${PROJECT_IDENTITY}" | sed -n '1p')"
PROJECT_BUILD_NUMBER="$(printf '%s\n' "${PROJECT_IDENTITY}" | sed -n '2p')"

if test "${SOURCE_IS_TAG}" = true; then
  [[ "${RELEASE_VERSION}" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || {
    printf 'Tag-driven TestFlight release version must be a three-component numeric marketing version.\n' >&2
    exit 2
  }
  [[ "${BUILD_NUMBER}" =~ ^[1-9][0-9]{0,9}$ ]] || {
    printf 'Tag-driven TestFlight build number must be a positive bounded integer.\n' >&2
    exit 2
  }
  (( 10#${BUILD_NUMBER} < 9999999999 )) || {
    printf 'Tag-driven TestFlight build number must leave room for the next bounded build.\n' >&2
    exit 2
  }
  test "${PROJECT_MARKETING_VERSION}" = "${RELEASE_VERSION}" || {
    printf 'Tag release version %s does not match project.yml MARKETING_VERSION %s.\n' "${RELEASE_VERSION}" "${PROJECT_MARKETING_VERSION}" >&2
    exit 2
  }
  test "${PROJECT_BUILD_NUMBER}" = "${BUILD_NUMBER}" || {
    printf 'Tag release build %s does not match project.yml CURRENT_PROJECT_VERSION %s.\n' "${BUILD_NUMBER}" "${PROJECT_BUILD_NUMBER}" >&2
    exit 2
  }
else
  test -z "${RELEASE_VERSION}" || {
    printf 'Manual TestFlight publication does not accept a release-version override.\n' >&2
    exit 2
  }
fi

RECEIPT="${ROOT_DIR}/Data/catalog/production-catalog-release-input-v1.json"
BUNDLE_SOURCE_MANIFEST="${ROOT_DIR}/Data/catalog/bundled/de/source-manifest-v1.json"
if test -f "${RECEIPT}"; then
  CATALOG_AUTHORITY=receipt
  python3 "${ROOT_DIR}/Tools/production_catalog_release_input.py" validate --input "${RECEIPT}"
elif test -f "${BUNDLE_SOURCE_MANIFEST}"; then
  CATALOG_AUTHORITY=git-bundle
  (
    cd "${ROOT_DIR}"
    PYTHONPATH=Tools python3 Tools/catalog_source_shards.py validate       --manifest Data/catalog/bundled/de/source-manifest-v1.json
  )
else
  printf 'No accepted production catalog authority exists; refusing to package synthetic or unreviewed catalog data for TestFlight.\n' >&2
  exit 2
fi

if [[ "${GITHUB_ACTIONS:-}" == "true" && "${SOURCE_IS_TAG}" != "true" && "${GITHUB_REF:-}" != "refs/heads/main" ]]; then
  printf 'Manual TestFlight publication is allowed only from protected main.\n' >&2
  exit 2
fi

SOURCE_SHA="$(git -C "${ROOT_DIR}" rev-parse HEAD)"
[[ "${SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]] || {
  printf 'Unable to resolve an exact source commit for TestFlight.\n' >&2
  exit 2
}

app_store_build_state() {
  python3 "${ROOT_DIR}/Tools/app_store_connect.py" build-state \
    --bundle-id tv.streamscape.halalfoodeu \
    --marketing-version "${RELEASE_VERSION}" \
    --build-number "${BUILD_NUMBER}" \
    --issuer-id "${ISSUER_ID}" \
    --key-id "${KEY_ID}" \
    --key-path "${AUTH_KEY_PATH}"
}

if test "${SOURCE_IS_TAG}" = true; then
  PROVIDER_STATE="$(app_store_build_state)" || {
    printf 'Unable to reconcile the exact TestFlight build in App Store Connect.\n' >&2
    exit 2
  }
  case "${PROVIDER_STATE}" in
    accepted)
      printf 'App Store Connect already accepted Halal Food EU %s (%s); skipping binary upload so Central can reconcile the post-publication build bump.\n' \
        "${RELEASE_VERSION}" "${BUILD_NUMBER}"
      exit 0
      ;;
    processing)
      printf 'The exact TestFlight build %s (%s) is still processing in App Store Connect; retry the same tag after processing completes.\n' \
        "${RELEASE_VERSION}" "${BUILD_NUMBER}" >&2
      exit 2
      ;;
    retryable)
      printf 'A prior TestFlight delivery for %s (%s) failed processing; retrying the same committed source identity.\n' \
        "${RELEASE_VERSION}" "${BUILD_NUMBER}"
      ;;
    absent) ;;
    *)
      printf 'Unexpected App Store Connect build reconciliation state.\n' >&2
      exit 2
      ;;
  esac
fi

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

SOURCE_SHA="${SOURCE_SHA}" DATABASE="${DATABASE}" MANIFEST="${MANIFEST}" REPORT="${REPORT}"   RECEIPT="${RECEIPT}" BUNDLE_SOURCE_MANIFEST="${BUNDLE_SOURCE_MANIFEST}" CATALOG_AUTHORITY="${CATALOG_AUTHORITY}" python3 - <<'PY'
import hashlib
import json
import os
from pathlib import Path

source_sha = os.environ["SOURCE_SHA"]
database = Path(os.environ["DATABASE"])
manifest_path = Path(os.environ["MANIFEST"])
report_path = Path(os.environ["REPORT"])
receipt_path = Path(os.environ["RECEIPT"])
bundle_source_path = Path(os.environ["BUNDLE_SOURCE_MANIFEST"])
authority = os.environ["CATALOG_AUTHORITY"]

manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
report = json.loads(report_path.read_text(encoding="utf-8"))

if report.get("commitSha") != source_sha or report.get("releaseMode") != "production":
    raise SystemExit("release evidence does not belong to this exact production source commit")
if report.get("productionAuthority") != authority:
    raise SystemExit("release evidence production authority does not match the accepted source authority")
if manifest.get("sourceCommit") != source_sha:
    raise SystemExit("catalog manifest sourceCommit does not match this exact application source")
if report.get("catalogVersion") != manifest.get("catalogVersion"):
    raise SystemExit("release report catalogVersion does not match the packaged catalog manifest")

database_sha = hashlib.sha256(database.read_bytes()).hexdigest()
manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
if report.get("databaseSha256") != database_sha or report.get("manifestSha256") != manifest_sha:
    raise SystemExit("catalog release evidence digest mismatch")

if authority == "receipt":
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    if manifest.get("catalogVersion") != receipt.get("catalogVersion"):
        raise SystemExit("catalog release evidence does not match the accepted release receipt")
elif authority == "git-bundle":
    source = json.loads(bundle_source_path.read_text(encoding="utf-8"))
    expected_shard_set = {
        "schemaVersion": source["schemaVersion"],
        "datasetID": source["datasetID"],
        "manifestPath": "Data/catalog/bundled/de/source-manifest-v1.json",
        "manifestSha256": hashlib.sha256(bundle_source_path.read_bytes()).hexdigest(),
        "logicalSha256": source["logicalSha256"],
        "recordCount": source["recordCount"],
        "shardCount": len(source["shards"]),
    }
    if manifest.get("catalogVersion") != source.get("catalogVersion"):
        raise SystemExit("catalog release evidence does not match the committed Git-backed catalog version")
    if manifest.get("sourceShardSet") != expected_shard_set:
        raise SystemExit("packaged catalog sourceShardSet does not match the committed Git-backed source set")
    if report.get("sourceShardSet") != expected_shard_set:
        raise SystemExit("release report sourceShardSet does not match the committed Git-backed source set")
    if report.get("sourceSetManifestSha256") != expected_shard_set["manifestSha256"]:
        raise SystemExit("release report source-set manifest digest mismatch")
    if report.get("sourceSetLogicalSha256") != expected_shard_set["logicalSha256"]:
        raise SystemExit("release report source-set logical digest mismatch")
    if report.get("sourceSetRecordCount") != expected_shard_set["recordCount"]:
        raise SystemExit("release report source-set record count mismatch")
    if report.get("sourceSetShardCount") != expected_shard_set["shardCount"]:
        raise SystemExit("release report source-set shard count mismatch")
else:
    raise SystemExit("unsupported production catalog authority")
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

ARCHIVE_VERSION_ARGS=()
if test "${SOURCE_IS_TAG}" != true; then
  ARCHIVE_VERSION_ARGS+=(CURRENT_PROJECT_VERSION="${BUILD_NUMBER}")
fi

xcodebuild archive \
  -project "${ROOT_DIR}/HalalFoodEU.xcodeproj" \
  -scheme HalalFoodEU \
  -configuration Release \
  -destination 'generic/platform=iOS' \
  -archivePath "${ARCHIVE_PATH}" \
  DEVELOPMENT_TEAM="${TEAM_ID}" \
  CODE_SIGN_STYLE=Automatic \
  "${ARCHIVE_VERSION_ARGS[@]}" \
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
if test "${SOURCE_IS_TAG}" = true; then
  test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "${ARCHIVED_APP}/Info.plist")" = "${RELEASE_VERSION}"
fi

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
if test "${SOURCE_IS_TAG}" = true; then
  test "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "${EXPORTED_APP}/Info.plist")" = "${RELEASE_VERSION}"
fi

xcrun altool \
  --upload-app \
  --type ios \
  --file "${IPA_PATH}" \
  --apiKey "${KEY_ID}" \
  --apiIssuer "${ISSUER_ID}" \
  --p8-file-path "${AUTH_KEY_PATH}"

if test "${SOURCE_IS_TAG}" = true; then
  POST_UPLOAD_MAX_ATTEMPTS=80
  POST_UPLOAD_SLEEP_SECONDS=15
  POST_UPLOAD_ACCEPTED=false
  for ((attempt = 1; attempt <= POST_UPLOAD_MAX_ATTEMPTS; attempt++)); do
    PROVIDER_STATE="$(app_store_build_state)" || {
      printf 'Unable to reconcile TestFlight processing after upload.\n' >&2
      exit 2
    }
    case "${PROVIDER_STATE}" in
      accepted)
        POST_UPLOAD_ACCEPTED=true
        break
        ;;
      processing|absent)
        if (( attempt < POST_UPLOAD_MAX_ATTEMPTS )); then
          sleep "${POST_UPLOAD_SLEEP_SECONDS}"
        fi
        ;;
      retryable)
        printf 'App Store Connect rejected TestFlight version %s build %s during processing; source build number remains unchanged.\n' \
          "${RELEASE_VERSION}" "${BUILD_NUMBER}" >&2
        exit 2
        ;;
      *)
        printf 'Unexpected App Store Connect post-upload reconciliation state.\n' >&2
        exit 2
        ;;
    esac
  done
  test "${POST_UPLOAD_ACCEPTED}" = true || {
    printf 'TestFlight version %s build %s did not reach an accepted provider state within the bounded processing window; source build number remains unchanged.\n' \
      "${RELEASE_VERSION}" "${BUILD_NUMBER}" >&2
    exit 2
  }
  printf 'Uploaded and provider-accepted exact Halal Food EU TestFlight version %s build %s from source %s with catalog %s.\n' \
    "${RELEASE_VERSION}" "${BUILD_NUMBER}" "${SOURCE_SHA}" "$(hfeu_sha256 "${DATABASE}")"
else
  printf 'Uploaded exact Halal Food EU TestFlight build %s from source %s with catalog %s.\n' \
    "${BUILD_NUMBER}" "${SOURCE_SHA}" "$(hfeu_sha256 "${DATABASE}")"
fi
