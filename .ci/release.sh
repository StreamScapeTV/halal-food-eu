#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
ci_validate_inputs release
ci_require_macos
printf 'ci_operation_unsupported: generic repository release is not the accepted TestFlight publication boundary\n' >&2
exit 64
