#!/usr/bin/env bash
set -Eeuo pipefail
CI_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
readonly CI_REPO_ROOT
cd "${CI_REPO_ROOT}"

ci_progress() {
  if test -n "${CI_PROGRESS_FILE:-}"; then
    printf '%s\n' "$1" >> "${CI_PROGRESS_FILE}"
  fi
}

ci_validate_inputs() {
  python3 "${CI_REPO_ROOT}/.ci/validate_inputs.py" "$1" >/dev/null
}

ci_require_macos() {
  if test -n "${CI_HOST_OS:-}" && test "${CI_HOST_OS}" != macos; then
    printf 'ci_host_unsupported: %s\n' "${CI_HOST_OS}" >&2
    exit 64
  fi
}
