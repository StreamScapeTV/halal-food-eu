#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
ci_validate_inputs ui-test
ci_require_macos
printf 'ci_operation_unsupported: ui-test has no stable reviewed automated contract\n' >&2
exit 64
