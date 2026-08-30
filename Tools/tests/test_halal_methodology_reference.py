import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("halal_methodology_reference_test", ROOT / "Tools" / "halal_methodology_reference.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
DATA = json.loads((ROOT / "Data" / "methodology" / "additive-identities-v1.json").read_text(encoding="utf-8"))


class AdditiveReferenceTests(unittest.TestCase):
    def test_committed_identity_reference_validates(self):
        MODULE.validate_additive_identities(DATA)
        lookup = MODULE.additive_lookup(DATA)
        self.assertEqual(set(lookup), {"E422", "E471"})
        self.assertIsNone(lookup["E471"]["halalConclusion"])
        self.assertEqual(lookup["E471"]["originConclusion"], "unknown-without-evidence")

    def test_halal_conclusion_is_rejected(self):
        broken = copy.deepcopy(DATA)
        broken["entries"][0]["halalConclusion"] = "halal"
        with self.assertRaises(MODULE.AdditiveReferenceError):
            MODULE.validate_additive_identities(broken)

    def test_origin_inference_is_rejected(self):
        broken = copy.deepcopy(DATA)
        broken["entries"][0]["originConclusion"] = "plant"
        with self.assertRaises(MODULE.AdditiveReferenceError):
            MODULE.validate_additive_identities(broken)

    def test_duplicate_additive_identity_is_rejected(self):
        broken = copy.deepcopy(DATA)
        broken["entries"].append(copy.deepcopy(broken["entries"][0]))
        with self.assertRaises(MODULE.AdditiveReferenceError):
            MODULE.validate_additive_identities(broken)


if __name__ == "__main__":
    unittest.main()
