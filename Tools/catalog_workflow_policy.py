"""Static trust-boundary checks for repository GitHub Actions workflows."""

from __future__ import annotations

import re
from pathlib import Path

from catalog_workflow_common import ContractError

WRITE_WORKFLOWS = {"propose-catalog-update.yml", "catalog-release.yml", "catalog-health.yml", "label-sync.yml"}
TRUSTED_ONLY_WORKFLOWS = {
    "acquire-catalog.yml",
    "scheduled-catalog-refresh.yml",
    "propose-catalog-update.yml",
    "catalog-release.yml",
    "catalog-health.yml",
}
PINNED_USES = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}(?:\s+#.*)?$")


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
        if path.name in TRUSTED_ONLY_WORKFLOWS and re.search(r"(?m)^\s{2}pull_request\s*:", text):
            raise ContractError(f"{path.name} trusted workflow must not trigger from pull_request")
        if path.name not in WRITE_WORKFLOWS:
            for permission in ("contents: write", "pull-requests: write", "issues: write", "attestations: write", "id-token: write"):
                if permission in text:
                    raise ContractError(f"{path.name} unexpectedly grants {permission}")
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
