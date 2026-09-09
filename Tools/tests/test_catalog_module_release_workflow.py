from pathlib import Path
import json
import unittest

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / ".github/workflows/catalog-module-ci.yml"
RELEASE = ROOT / ".github/workflows/catalog-module-release.yml"
POLICY = ROOT / "Data/catalog/catalog-module-trust-policy-v1.json"
RFC_PUBLIC = "11qYAYKxCrfVS/7TyWQHOg7hcvPapiMlrwIaaPcHURo="


class CatalogModuleWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = CONTRACT.read_text(encoding="utf-8")
        self.release = RELEASE.read_text(encoding="utf-8")

    def test_contract_lane_is_secretless_and_proves_production_shaped_fixture(self) -> None:
        self.assertIn("workflow_dispatch:", self.contract)
        self.assertIn("pull_request:", self.contract)
        self.assertIn("Tools/build_production_fixture.py", self.contract)
        self.assertIn("Tools/catalog_module_release.py verify-artifacts", self.contract)
        self.assertIn("rfc8032-ci-only", self.contract)
        self.assertNotIn("secrets.CATALOG_SIGNING_PRIVATE_KEY", self.contract)

    def test_release_is_manual_protected_main_and_exact_catalog_release_run(self) -> None:
        self.assertIn("workflow_dispatch:", self.release)
        self.assertNotIn("pull_request:", self.release)
        self.assertIn("if: github.ref == 'refs/heads/main'", self.release)
        self.assertIn("environment: catalog-module-release", self.release)
        self.assertIn(".github/workflows/catalog-release.yml", self.release)
        self.assertIn("'head_sha': os.environ['GITHUB_SHA']", self.release)
        self.assertIn("release-evidence-${{ github.sha }}", self.release)
        self.assertIn("releaseMode') != 'production'", self.release)

    def test_secret_must_match_bundled_active_key_before_signing(self) -> None:
        verify_key = self.release.index("Tools/catalog_module_release.py verify-key")
        sign = self.release.index("Tools/catalog_module_release.py sign")
        self.assertLess(verify_key, sign)
        self.assertIn("secrets.CATALOG_SIGNING_PRIVATE_KEY", self.release)
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        self.assertNotIn(RFC_PUBLIC, {key["publicKeyBase64"] for key in policy["keys"]})

    def test_release_is_prerelease_until_unauthenticated_public_redownload_passes(self) -> None:
        create = self.release.index('gh release create "$tag"')
        public = self.release.index('BASE="https://github.com/$GITHUB_REPOSITORY/releases/download/$tag"')
        verify = self.release.index("Tools/catalog_module_release.py verify-artifacts", public)
        stable = self.release.index('gh release edit "$tag"', verify)
        self.assertLess(create, public)
        self.assertLess(public, verify)
        self.assertLess(verify, stable)
        self.assertIn("--prerelease", self.release[create:public])
        self.assertIn("curl --proto '=https'", self.release[public:verify])

    def test_release_refuses_existing_identity_and_publishes_durable_report(self) -> None:
        self.assertIn('gh release view "$tag"', self.release)
        self.assertIn('git ls-remote --exit-code --tags', self.release)
        self.assertIn("catalog-module-release-report.json", self.release)
        self.assertIn("publicRedownloadVerified", self.release)


if __name__ == "__main__":
    unittest.main()
