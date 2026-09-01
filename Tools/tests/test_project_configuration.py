from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
ROOT = TOOLS.parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import project_configuration as config


class ProjectConfigurationTests(unittest.TestCase):
    def _write_fixture(self, root: Path, *, registry: list[dict], policies: dict[str, dict] | None = None):
        config_dir = root / "Data/config"
        source_root = root / "Data/sources"
        workflow_dir = root / "Data/workflows"
        config_dir.mkdir(parents=True, exist_ok=True)
        source_root.mkdir(parents=True, exist_ok=True)
        workflow_dir.mkdir(parents=True, exist_ok=True)
        public = {
            "schemaVersion": 1,
            "publicValues": {
                "PRODUCT_SUBMISSION_EMAIL": "info@faruqi.dev",
                "OPEN_FOOD_FACTS_CONTACT_EMAIL": "info@faruqi.dev",
                "OPEN_FOOD_FACTS_USER_AGENT": "HalalFoodEU/0.1 (info@faruqi.dev)",
            },
        }
        public_path = config_dir / "public-project-configuration-v1.json"
        public_path.write_text(json.dumps(public) + "\n", encoding="utf-8")
        contract_path = workflow_dir / "catalog-workflow-contract-v1.json"
        contract_path.write_text(json.dumps({"sourceRegistry": registry}) + "\n", encoding="utf-8")
        for source_key, raw in (policies or {}).items():
            path = source_root / source_key / config.CREDENTIAL_POLICY_NAME
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(raw) + "\n", encoding="utf-8")
        return public_path, contract_path, source_root

    def test_committed_public_values_are_exact_and_valid(self):
        values = config.load_public_values(ROOT / "Data/config/public-project-configuration-v1.json")
        self.assertEqual(values["PRODUCT_SUBMISSION_EMAIL"], "info@faruqi.dev")
        self.assertEqual(values["OPEN_FOOD_FACTS_CONTACT_EMAIL"], "info@faruqi.dev")
        self.assertEqual(values["OPEN_FOOD_FACTS_USER_AGENT"], "HalalFoodEU/0.1 (info@faruqi.dev)")

    def test_user_agent_contact_must_match_reviewed_contact(self):
        raw = {
            "schemaVersion": 1,
            "publicValues": {
                "PRODUCT_SUBMISSION_EMAIL": "info@faruqi.dev",
                "OPEN_FOOD_FACTS_CONTACT_EMAIL": "info@faruqi.dev",
                "OPEN_FOOD_FACTS_USER_AGENT": "HalalFoodEU/0.1 (other@example.test)",
            },
        }
        with self.assertRaisesRegex(config.ConfigurationError, "contact must equal"):
            config.validate_public_config(raw)

    def test_free_sources_require_no_credential_policy_or_secret_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public, contract, source_root = self._write_fixture(
                root,
                registry=[
                    {"key": "open-food-facts", "enabled": True, "credentialsRequired": False},
                    {"key": "open-prices", "enabled": True, "credentialsRequired": False},
                ],
            )
            report = config.evaluate_health(
                config_path=public,
                workflow_contract_path=contract,
                source_root=source_root,
            )
        self.assertEqual(report["status"], "healthy")
        self.assertFalse(report["ownerInputRequired"])
        self.assertEqual(report["sources"], [])

    def test_disabled_optional_source_does_not_block_when_secrets_are_absent(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = {
                "schemaVersion": 1,
                "sourceKey": "example-retailer",
                "authentication": {"mode": "api-key", "requiredSecretNames": ["EXAMPLE_API_KEY"]},
            }
            public, contract, source_root = self._write_fixture(
                root,
                registry=[{"key": "example-retailer", "enabled": False, "credentialsRequired": True}],
                policies={"example-retailer": policy},
            )
            report = config.evaluate_health(
                config_path=public,
                workflow_contract_path=contract,
                source_root=source_root,
            )
        self.assertEqual(report["status"], "healthy")
        self.assertEqual(report["sources"][0]["state"], "disabled")
        self.assertFalse(report["sources"][0]["requiredSecrets"][0]["configured"])

    def test_enabled_source_reports_only_required_names_and_boolean_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = {
                "schemaVersion": 1,
                "sourceKey": "example-retailer",
                "authentication": {
                    "mode": "oauth-client",
                    "requiredSecretNames": ["EXAMPLE_CLIENT_ID", "EXAMPLE_CLIENT_SECRET"],
                },
            }
            public, contract, source_root = self._write_fixture(
                root,
                registry=[{"key": "example-retailer", "enabled": True, "credentialsRequired": True}],
                policies={"example-retailer": policy},
            )
            blocked = config.evaluate_health(
                config_path=public,
                workflow_contract_path=contract,
                source_root=source_root,
                configured_secret_names=["EXAMPLE_CLIENT_ID"],
            )
            healthy = config.evaluate_health(
                config_path=public,
                workflow_contract_path=contract,
                source_root=source_root,
                configured_secret_names=["EXAMPLE_CLIENT_ID", "EXAMPLE_CLIENT_SECRET"],
            )
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["blockers"][0]["missingSecretNames"], ["EXAMPLE_CLIENT_SECRET"])
        self.assertNotIn("secretValue", json.dumps(blocked))
        self.assertEqual(healthy["status"], "healthy")

    def test_source_marked_credential_free_rejects_speculative_credential_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            policy = {
                "schemaVersion": 1,
                "sourceKey": "example-retailer",
                "authentication": {"mode": "api-key", "requiredSecretNames": ["EXAMPLE_API_KEY"]},
            }
            public, contract, source_root = self._write_fixture(
                root,
                registry=[{"key": "example-retailer", "enabled": False, "credentialsRequired": False}],
                policies={"example-retailer": policy},
            )
            with self.assertRaisesRegex(config.ConfigurationError, "forbids credentials"):
                config.validate_contracts(
                    config_path=public,
                    workflow_contract_path=contract,
                    source_root=source_root,
                )

    def test_enabled_credential_source_requires_source_specific_policy(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public, contract, source_root = self._write_fixture(
                root,
                registry=[{"key": "example-retailer", "enabled": True, "credentialsRequired": True}],
            )
            with self.assertRaisesRegex(config.ConfigurationError, "no reviewed credential policy"):
                config.validate_contracts(
                    config_path=public,
                    workflow_contract_path=contract,
                    source_root=source_root,
                )

    def test_automatic_github_token_cannot_be_declared_as_source_secret(self):
        raw = {
            "schemaVersion": 1,
            "sourceKey": "example-retailer",
            "authentication": {"mode": "custom", "requiredSecretNames": ["GITHUB_TOKEN"]},
        }
        with self.assertRaisesRegex(config.ConfigurationError, "automatic GitHub token"):
            config.validate_credential_policy(raw, Path("credential-policy-v1.json"))

    def test_mutually_exclusive_authentication_shape_rejects_extra_modes(self):
        raw = {
            "schemaVersion": 1,
            "sourceKey": "example-retailer",
            "authentication": {
                "mode": "api-key",
                "requiredSecretNames": ["EXAMPLE_API_KEY"],
                "oauth": {"requiredSecretNames": ["EXAMPLE_CLIENT_SECRET"]},
            },
        }
        with self.assertRaisesRegex(config.ConfigurationError, "exactly one mode"):
            config.validate_credential_policy(raw, Path("credential-policy-v1.json"))

    def test_unknown_configured_secret_names_fail_closed_without_reporting_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            public, contract, source_root = self._write_fixture(
                root,
                registry=[{"key": "open-food-facts", "enabled": True, "credentialsRequired": False}],
            )
            with self.assertRaisesRegex(config.ConfigurationError, "undeclared names"):
                config.evaluate_health(
                    config_path=public,
                    workflow_contract_path=contract,
                    source_root=source_root,
                    configured_secret_names=["UNDECLARED_SECRET"],
                )


if __name__ == "__main__":
    unittest.main()
