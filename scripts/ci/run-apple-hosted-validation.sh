#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=apple-common.sh
source "${ROOT_DIR}/scripts/ci/apple-common.sh"

PROFILE="${CI_APPLE_HOSTED_PROFILE:-}"
case "${PROFILE}" in
  build|test|simulator) ;;
  "")
    printf 'CI_APPLE_HOSTED_PROFILE is required.\n' >&2
    exit 2
    ;;
  *)
    printf 'Unsupported CI_APPLE_HOSTED_PROFILE: %s\n' "${PROFILE}" >&2
    exit 2
    ;;
esac

hfeu_select_reviewed_xcode
hfeu_bootstrap_reviewed_xcodegen "${ROOT_DIR}"

export HFEU_IOS_VALIDATION_PROFILE="${PROFILE}"
exec bash "${ROOT_DIR}/Scripts/ci-ios.sh"
