#!/usr/bin/env bash
# Shared product-owned Apple CI helpers. This file is sourced by fixed wrappers only.

hfeu_repo_root() {
  cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd
}

hfeu_select_reviewed_xcode() {
  local expected="${HFEU_EXPECTED_XCODE_VERSION:-26.6}"
  if [[ "${GITHUB_ACTIONS:-}" == "true" ]]; then
    local app="/Applications/Xcode_${expected}.app"
    test -d "${app}" || {
      printf 'Required stable Xcode %s is not installed at %s.\n' "${expected}" "${app}" >&2
      return 2
    }
    export DEVELOPER_DIR="${app}/Contents/Developer"
  fi

  local actual
  actual="$(xcodebuild -version | awk 'NR == 1 { print $2 }')"
  test "${actual}" = "${expected}" || {
    printf 'Expected Xcode %s, got %s.\n' "${expected}" "${actual}" >&2
    return 2
  }
}

hfeu_bootstrap_reviewed_xcodegen() {
  local root="$1"
  local manifest="${root}/Data/security/tooling-dependencies-v1.json"
  test -f "${manifest}" || {
    printf 'Reviewed tooling manifest is missing: %s\n' "${manifest}" >&2
    return 2
  }

  local metadata
  metadata="$(
    MANIFEST="${manifest}" python3 - <<'PY'
import json
import os
import re
from pathlib import Path

path = Path(os.environ["MANIFEST"])
data = json.loads(path.read_text(encoding="utf-8"))
entry = data.get("xcodegen")
if not isinstance(entry, dict):
    raise SystemExit("reviewed tooling manifest has no xcodegen entry")
repository = entry.get("repository")
version = entry.get("version")
commit = entry.get("commitSha")
if repository != "yonaskolb/XcodeGen":
    raise SystemExit("reviewed XcodeGen repository is not admitted")
if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
    raise SystemExit("reviewed XcodeGen version is invalid")
if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
    raise SystemExit("reviewed XcodeGen commit is invalid")
print(repository)
print(version)
print(commit)
PY
  )"

  local repository version commit
  repository="$(printf '%s\n' "${metadata}" | sed -n '1p')"
  version="$(printf '%s\n' "${metadata}" | sed -n '2p')"
  commit="$(printf '%s\n' "${metadata}" | sed -n '3p')"

  local temp_base="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
  local checkout="${temp_base}/hfeu-xcodegen-${commit}"
  local bin_dir="${checkout}/bin"
  rm -rf -- "${checkout}"
  mkdir -p "${checkout}/source" "${bin_dir}"

  git -C "${checkout}/source" init -q
  git -C "${checkout}/source" remote add origin "https://github.com/${repository}.git"
  git -C "${checkout}/source" fetch --depth=1 origin "${commit}"
  test "$(git -C "${checkout}/source" rev-parse FETCH_HEAD)" = "${commit}"
  git -C "${checkout}/source" checkout --detach FETCH_HEAD
  test -f "${checkout}/source/Package.resolved"

  (
    cd "${checkout}/source"
    swift build -c release --disable-sandbox
  )
  cp "${checkout}/source/.build/release/xcodegen" "${bin_dir}/xcodegen"
  chmod 755 "${bin_dir}/xcodegen"
  export PATH="${bin_dir}:${PATH}"

  local reported
  reported="$(xcodegen --version)"
  [[ "${reported}" == *"${version}"* ]] || {
    printf 'Reviewed XcodeGen version mismatch: expected %s, got %s\n' "${version}" "${reported}" >&2
    return 2
  }
}

hfeu_sha256() {
  /usr/bin/shasum -a 256 "$1" | awk '{print $1}'
}
