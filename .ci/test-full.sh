#!/usr/bin/env bash
set -Eeuo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/common.sh"
ci_validate_inputs full
ci_require_macos
ci_progress "full:contract"
python3 .ci/contract_test.py
ci_progress "full:catalog-validate"
make catalog-validate
ci_progress "full:ios-validation"
CI_APPLE_HOSTED_PROFILE=test exec bash scripts/ci/run-apple-hosted-validation.sh
