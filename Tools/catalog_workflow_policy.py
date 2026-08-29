"""Static trust-boundary checks for repository GitHub Actions workflows."""

from __future__ import annotations

import re
from pathlib import Path

from catalog_workflow_common import ContractError

WRITE_WORKFLOWS = {"catalog-release.yml", "catalog-health.yml", "label-sync.yml"}
TRUSTED_ONLY_WORKFLOWS = {
    "acquire-catalog.yml",
    "scheduled-catalog-refresh.yml",
    "propose-catalog-update.yml",
    "catalog-release.yml",
    "catalog-health.yml",
}
DEFAULT_BRANCH_ONLY_WORKFLOWS = {
    "scheduled-catalog-refresh.yml",
    "catalog-release.yml",
    "catalog-health.yml",
}
PINNED_USES = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}(?:\s+#.*)?$")
RELEASE_BUILDER = "python3 Tools/catalog_builder.py"
RELEASE_VALIDATOR = "python3 Tools/validate_catalog.py"
RELEASE_DATABASE = "HalalFoodEU/Resources/catalog.sqlite3"
RELEASE_MANIFEST = "HalalFoodEU/Resources/catalog-manifest.json"
XCODEGEN_VERSION = "2.46.0"
XCODEGEN_COMMIT = "8445e778451c7e44237b90281bde622d764b0084"
DANGEROUS_SHELL_PATTERNS = (
    (re.compile(r"(?m)^\s*set\s+-[^\n]*x"), "shell tracing is forbidden"),
    (re.compile(r"(?m)^\s*(?:printenv|env)\s*$"), "environment dumps are forbidden"),
    (re.compile(r"(?:curl|wget)[^\n|]*\|\s*(?:ba)?sh\b"), "pipe-to-shell installation is forbidden"),
    (re.compile(r"\bbrew\s+install\s+xcodegen\b"), "XcodeGen must use the reviewed source pin"),
)


def _validate_release_materialization(path: Path, text: str) -> None:
    """Ensure release evidence never assumes ignored generated bundle files exist."""
    if text.count(RELEASE_BUILDER) < 2:
        raise ContractError(
            f"{path.name} must materialize generated catalog subjects in evidence and attestation jobs"
        )
    if text.count(RELEASE_VALIDATOR) < 2:
        raise ContractError(f"{path.name} must validate both generated release subject sets")
    if text.find(RELEASE_BUILDER) > text.find(RELEASE_VALIDATOR):
        raise ContractError(f"{path.name} must materialize the catalog before validating release evidence")
    for required in (RELEASE_DATABASE, RELEASE_MANIFEST, "Data/sample-products.json"):
        if required not in text:
            raise ContractError(f"{path.name} is missing integrated release input/path {required}")
    if text.count("Tools/catalog_security.py bind-manifest") < 2:
        raise ContractError(f"{path.name} must bind each release manifest to reviewed source policy")
    if text.count("Tools/catalog_security.py validate-manifest") < 2:
        raise ContractError(f"{path.name} must validate each release manifest source-policy binding")


def _validate_xcodegen_pin(path: Path, text: str) -> None:
    if path.name != "ios-ci.yml":
        return
    for required in (
        f"XCODEGEN_VERSION: {XCODEGEN_VERSION}",
        f"XCODEGEN_COMMIT: {XCODEGEN_COMMIT}",
        'git -C "$RUNNER_TEMP/XcodeGen" fetch --depth=1 origin "$XCODEGEN_COMMIT"',
        'test "$(git -C "$RUNNER_TEMP/XcodeGen" rev-parse FETCH_HEAD)" = "$XCODEGEN_COMMIT"',
        'swift build -c release --disable-sandbox',
    ):
        if required not in text:
            raise ContractError(f"{path.name} is missing reviewed XcodeGen pin/build evidence")


def validate_workflows(root: Path) -> list[str]:
    if not root.is_dir():
        raise ContractError(f"workflow root is missing: {root}")
    checked: list[str] = []
    for path in sorted(root.glob("*.yml")):
        text = path.read_text(encoding="utf-8")
        checked.append(path.name)
        if "runs-on: self-hosted" in text or "self-hosted" in re.sub(r"#.*", "", text):
            raise ContractError(f"{path.name} must not use self-hosted runners")
        if "pull_request_target:" in text:
            raise ContractError(f"{path.name} must not use pull_request_target")
        if "permissions:" not in text:
            raise ContractError(f"{path.name} must declare permissions explicitly")
        if "schedule:" in text and "workflow_dispatch:" not in text:
            raise ContractError(f"{path.name} schedule must also expose workflow_dispatch")
        if "${{ secrets[" in text:
            raise ContractError(f"{path.name} must not select secret names dynamically")
        for pattern, reason in DANGEROUS_SHELL_PATTERNS:
            if pattern.search(text):
                raise ContractError(f"{path.name}: {reason}")
        if path.name in TRUSTED_ONLY_WORKFLOWS and re.search(r"(?m)^\s{2}pull_request\s*:", text):
            raise ContractError(f"{path.name} trusted workflow must not trigger from pull_request")
        if path.name in DEFAULT_BRANCH_ONLY_WORKFLOWS:
            if "EXPECTED_REF: refs/heads/main" not in text or 'test "$GITHUB_REF" = "$EXPECTED_REF"' not in text:
                raise ContractError(f"{path.name} must fail closed outside the reviewed default branch")
        if path.name == "catalog-release.yml":
            _validate_release_materialization(path, text)
        _validate_xcodegen_pin(path, text)

        if "pull_request:" in text:
            for permission in ("contents: write", "pull-requests: write", "issues: write", "attestations: write", "id-token: write"):
                if permission in text:
                    raise ContractError(f"{path.name} pull-request workflow unexpectedly grants {permission}")

        if path.name not in WRITE_WORKFLOWS:
            for permission in ("contents: write", "pull-requests: write", "issues: write", "attestations: write", "id-token: write"):
                if permission in text:
                    raise ContractError(f"{path.name} unexpectedly grants {permission}")

        checkout_count = text.count("actions/checkout@")
        if checkout_count and text.count("persist-credentials: false") < checkout_count:
            raise ContractError(f"{path.name} must disable persisted checkout credentials for every checkout")

        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("- uses:"):
                target = stripped[len("- uses:"):].strip()
            elif stripped.startswith("uses:"):
                target = stripped[len("uses:"):].strip()
            else:
                continue
            if target.startswith("./"):
                continue
            if not PINNED_USES.fullmatch(target):
                raise ContractError(f"{path.name} has unpinned action reference: {target}")
    return checked
