import copy
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "evidence_model", ROOT / "Tools" / "evidence_model.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

FIXTURE = ROOT / "Data" / "evidence" / "sample-evidence-v1.json"


class SourceMarketTests(unittest.TestCase):
    def test_source_must_declare_evidence_market(self) -> None:
        data = MODULE.load_json(FIXTURE)
        identity = copy.deepcopy(data["identities"][0])
        identity["market"] = "FR"
        identity["id"] = MODULE.derive_id("identities", identity)
        data["identities"][0] = identity

        with self.assertRaisesRegex(
            MODULE.EvidenceValidationError,
            "does not declare market FR",
        ):
            MODULE.validate_envelope(data)


if __name__ == "__main__":
    unittest.main()
