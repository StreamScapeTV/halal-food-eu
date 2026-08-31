"""Static trust-boundary checks for repository GitHub Actions workflows."""

from __future__ import annotations

import re
from pathlib import Path

from catalog_workflow_common import ContractError

ALLOWED_WRITE_PERMISSIONS = {
    "catalog-release.yml": {"attestations", "id-token"},
    "label-sync.yml": {"issues"},
    "propose-catalog-update.yml": {"contents", "pull-requests"},
    "scheduled-catalog-refresh.yml": {"contents", "pull-requests"},
}
TRUSTED_ONLY_WORKFLOWS = {
    "acquire-catalog.yml",
    "scheduled-catalog-refresh.yml",
    "propose-catalog-update.yml",
    "catalog-release.yml",
    "catalog-health.yml",
}
DEFAULT_BRANCH_ONLY_WORKFLOWS = {
    "scheduled-catalog-refresh.yml",
    "propose-catalog-update.yml",
    "catalog-release.yml",
    "catalog-health.yml",
}
PINNED_USES = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}(?:\s+#.*)?$")
USES_LINE = re.compile(r"^-?\s*uses\s*:\s*(?P<target>.+?)\s*$")
PERMISSIONS_HEADER = re.compile(r"^(?P<indent> *)permissions\s*:\s*(?P<value>[^#]*?)(?:\s+#.*)?$")
PERMISSION_ENTRY = re.compile(r"^(?P<indent> +)(?P<scope>[A-Za-z0-9-]+)\s*:\s*(?P<access>read|write|none)\s*(?:#.*)?$")
RELEASE_FIXTURE_BUILDER = "python3 Tools/catalog_builder.py"
RELEASE_FIXTURE_VALIDATOR = "python3 Tools/validate_catalog.py"
RELEASE_PRODUCTION_RECEIPT = "Data/catalog/production-catalog-release-input-v1.json"
RELEASE_PRODUCTION_MATERIALIZER = "python3 Tools/production_catalog_release_input.py materialize-request"
RELEASE_PRODUCTION_BUILDER = "python3 Tools/production_catalog_request.py build"
RELEASE_PRODUCTION_VALIDATOR = "python3 Tools/production_catalog.py validate"
RELEASE_DATABASE = "database/payload/catalog.sqlite3"
RELEASE_MANIFEST = "manifest/payload/catalog-manifest.json"
XCODEGEN_VERSION = "2.46.0"
XCODEGEN_COMMIT = "8445e778451c7e44237b90281bde622d764b0084"
DANGEROUS_SHELL_PATTERNS = (
    (re.compile(r"(?m)^\s*set\s+-[^\n]*x"), "shell tracing is forbidden"),
    (re.compile(r"(?m)^\s*(?:printenv|env)\s*$"), "environment dumps are forbidden"),
    (re.compile(r"(?:curl|wget)[^\n|]*\|\s*(?:ba)?sh\b"), "pipe-to-shell installation is forbidden"),
    (re.compile(r"\bbrew\s+install\s+xcodegen\b"), "XcodeGen must use the reviewed source pin"),
)


def _permission_entries(path: Path, text: str) -> tuple[bool, list[tuple[str, str]]]:
    lines = text.splitlines()
    found = False
    entries: list[tuple[str, str]] = []
    for index, line in enumerate(lines):
        header = PERMISSIONS_HEADER.fullmatch(line)
        if not header:
            continue
        found = True
        inline = header.group("value").strip()
        if inline:
            if inline == "{}":
                continue
            raise ContractError(f"{path.name} permissions must use an explicit scope map")

        base_indent = len(header.group("indent"))
        cursor = index + 1
        while cursor < len(lines):
            candidate = lines[cursor]
            stripped = candidate.strip()
            if not stripped or stripped.startswith("#"):
                cursor += 1
                continue
            indent = len(candidate) - len(candidate.lstrip(" "))
            if indent <= base_indent:
                break
            entry = PERMISSION_ENTRY.fullmatch(candidate)
            if not entry:
                raise ContractError(f"{path.name} has an unsupported permissions entry")
            entries.append((entry.group("scope"), entry.group("access")))
            cursor += 1
    return found, entries


def _validate_permissions(path: Path, text: str) -> None:
    found, entries = _permission_entries(path, text)
    if not found:
        raise ContractError(f"{path.name} must declare permissions explicitly")

    pull_request_workflow = bool(re.search(r"(?m)^\s{2}pull_request\s*:", text))
    allowed_writes = ALLOWED_WRITE_PERMISSIONS.get(path.name, set())
    for scope, access in entries:
        if access != "write":
            continue
        if pull_request_workflow:
            raise ContractError(f"{path.name} pull-request workflow unexpectedly grants {scope}: write")
        if scope not in allowed_writes:
            raise ContractError(f"{path.name} unexpectedly grants {scope}: write")


def _validate_release_materialization(path: Path, text: str) -> None:
    """Ensure release evidence rebuilds from reviewed inputs instead of ignored files."""
    required = (
        RELEASE_PRODUCTION_RECEIPT,
        "python3 Tools/production_catalog_release_input.py validate",
        RELEASE_PRODUCTION_MATERIALIZER,
        '--integrated-source-commit "$GITHUB_SHA"',
        RELEASE_PRODUCTION_BUILDER,
        RELEASE_PRODUCTION_VALIDATOR,
        "run-id: ${{ steps.mode.outputs.source_run_id }}",
        "github-token: ${{ github.token }}",
        RELEASE_DATABASE,
        RELEASE_MANIFEST,
        RELEASE_FIXTURE_BUILDER,
        RELEASE_FIXTURE_VALIDATOR,
        "Data/sample-products.json",
        "release-evidence-${{ github.sha }}",
    )
    for value in required:
        if value not in text:
            raise ContractError(f"{path.name} is missing integrated release input/path {value}")

    if text.find(RELEASE_PRODUCTION_MATERIALIZER) > text.find(RELEASE_PRODUCTION_BUILDER):
        raise ContractError(f"{path.name} must verify reviewed inputs before production rematerialization")
    if text.find(RELEASE_PRODUCTION_BUILDER) > text.find(RELEASE_PRODUCTION_VALIDATOR):
        raise ContractError(f"{path.name} must materialize the production catalog before validation")
    if text.find(RELEASE_FIXTURE_BUILDER) > text.find(RELEASE_FIXTURE_VALIDATOR):
        raise ContractError(f"{path.name} must materialize the fixture catalog before validation")
    if text.count("run-id: ${{ steps.mode.outputs.source_run_id }}") < 3:
        raise ContractError(f"{path.name} must download all reviewed production inputs from the receipt run")
    if text.count("github-token: ${{ github.token }}") < 3:
        raise ContractError(f"{path.name} must authenticate every cross-run production artifact download")
    if text.count(RELEASE_PRODUCTION_VALIDATOR) < 2:
        raise ContractError(f"{path.name} must revalidate production subjects before optional attestation")
    if text.count(RELEASE_FIXTURE_VALIDATOR) < 2:
        raise ContractError(f"{path.name} must revalidate fixture subjects before optional attestation")
    if text.count("Tools/catalog_security.py bind-manifest") < 1:
        raise ContractError(f"{path.name} must bind the synthetic fallback manifest to reviewed source policy")
    if text.count("Tools/catalog_security.py validate-manifest") < 2:
        raise ContractError(f"{path.name} must validate synthetic source-policy binding before evidence and attestation")

    attestation = text.find("  attestation:")
    if attestation < 0:
        raise ContractError(f"{path.name} is missing optional attestation job")
    attestation_text = text[attestation:]
    for forbidden in (RELEASE_FIXTURE_BUILDER, RELEASE_PRODUCTION_BUILDER):
        if forbidden in attestation_text:
            raise ContractError(
                f"{path.name} attestation must reuse exact release-evidence subjects instead of rebuilding them"
            )


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
        _validate_permissions(path, text)
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

        for line in text.splitlines():
            stripped = line.strip()
            uses_match = USES_LINE.fullmatch(stripped)
            if not uses_match:
                continue
            target = uses_match.group("target").strip()
            if target.startswith("./"):
                continue
            if not PINNED_USES.fullmatch(target):
                raise ContractError(f"{path.name} has unpinned action reference: {target}")

        checkout_count = text.count("actions/checkout@")
        if checkout_count and text.count("persist-credentials: false") < checkout_count:
            raise ContractError(f"{path.name} must disable persisted checkout credentials for every checkout")
    return checked
