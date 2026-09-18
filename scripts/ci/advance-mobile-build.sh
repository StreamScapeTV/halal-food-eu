#!/usr/bin/env bash
set -Eeuo pipefail

required_context=(
  CI_MOBILE_RELEASE_VERSION
  CI_MOBILE_RELEASE_BUILD_NUMBER
  CI_MOBILE_BUMP_WORKTREE
)
for name in "${required_context[@]}"; do
  test -n "${!name:-}" || {
    printf 'Missing required Central mobile build-bump context: %s\n' "${name}" >&2
    exit 2
  }
done

RELEASE_VERSION="${CI_MOBILE_RELEASE_VERSION}"
RELEASE_BUILD_NUMBER="${CI_MOBILE_RELEASE_BUILD_NUMBER}"
WORKTREE="${CI_MOBILE_BUMP_WORKTREE}"

[[ "${RELEASE_VERSION}" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] || {
  printf 'Halal Food EU mobile release version must be a three-component numeric marketing version.\n' >&2
  exit 2
}
[[ "${RELEASE_BUILD_NUMBER}" =~ ^[1-9][0-9]{0,9}$ ]] || {
  printf 'Halal Food EU mobile release build must be a positive bounded integer.\n' >&2
  exit 2
}
(( 10#${RELEASE_BUILD_NUMBER} < 9999999999 )) || {
  printf 'Halal Food EU mobile release build must leave room for the next bounded build number.\n' >&2
  exit 2
}

test -d "${WORKTREE}" || {
  printf 'Central mobile build-bump worktree is missing.\n' >&2
  exit 2
}
test -z "$(find "${WORKTREE}" -name .git -print -quit)" || {
  printf 'Halal Food EU build-bump worktree must not contain Git metadata.\n' >&2
  exit 2
}
PROJECT_FILE="${WORKTREE}/project.yml"
test -f "${PROJECT_FILE}" && test ! -L "${PROJECT_FILE}" || {
  printf 'Halal Food EU build-bump project.yml must be one regular file.\n' >&2
  exit 2
}

PROJECT_FILE="${PROJECT_FILE}" RELEASE_VERSION="${RELEASE_VERSION}" RELEASE_BUILD_NUMBER="${RELEASE_BUILD_NUMBER}" python3 - <<'PY'
import os
import re
from pathlib import Path

path = Path(os.environ["PROJECT_FILE"])
release_version = os.environ["RELEASE_VERSION"]
release_build = int(os.environ["RELEASE_BUILD_NUMBER"])
text = path.read_text(encoding="utf-8")

marketing_pattern = re.compile(r"^(?P<indent>\s*)MARKETING_VERSION:\s*\"?(?P<value>[^\"\s#]+)\"?\s*$", re.MULTILINE)
build_pattern = re.compile(r"^(?P<indent>\s*)CURRENT_PROJECT_VERSION:\s*\"?(?P<value>[^\"\s#]+)\"?\s*$", re.MULTILINE)
marketing = list(marketing_pattern.finditer(text))
builds = list(build_pattern.finditer(text))
if len(marketing) != 1 or len(builds) != 1:
    raise SystemExit("project.yml must contain exactly one canonical marketing version and build number")
if marketing[0].group("value") != release_version:
    raise SystemExit("project.yml MARKETING_VERSION does not match the accepted release version")
current = builds[0].group("value")
if not re.fullmatch(r"[1-9][0-9]{0,9}", current):
    raise SystemExit("project.yml CURRENT_PROJECT_VERSION is not a positive bounded integer")
current_build = int(current)
next_build = release_build + 1
if current_build == next_build:
    # Idempotent same-tag repair: Central may rerun after a prior successful bump.
    raise SystemExit(0)
if current_build != release_build:
    raise SystemExit("project.yml CURRENT_PROJECT_VERSION is neither the accepted build nor its exact next build")

replacement = f'{builds[0].group("indent")}CURRENT_PROJECT_VERSION: {next_build}'
updated = text[: builds[0].start()] + replacement + text[builds[0].end() :]
if marketing_pattern.search(updated).group("value") != release_version:
    raise SystemExit("mobile build bump unexpectedly changed MARKETING_VERSION")
path.write_text(updated, encoding="utf-8")
PY
