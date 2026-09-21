#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
ci_validate_inputs build
ci_require_macos
ci_progress "build:hosted-validation"
CI_APPLE_HOSTED_PROFILE=build exec bash scripts/ci/run-apple-hosted-validation.sh
