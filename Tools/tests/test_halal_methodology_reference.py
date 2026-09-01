import copy
import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("halal_methodology_reference_test", ROOT / "Tools" / "halal_methodology_reference.py")
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)
DATA = json.loads((ROOT / "Data" / "methodology" / "additive-identities-v1.json").read_text(encoding="utf-8"))
METHODOLOGY = json.loads((ROOT / "Data" / "methodology" / "halal-methodology-v1.json").read_text(encoding="utf-8"))


class AdditiveReferenceTests(unittest.TestCase):
    def test_committed_identity_reference_validates_without_halal_conclusions(self):
        MODULE.validate_additive_identities(DATA)
        lookup = MODULE.additive_lookup(DATA)
        self.assertEqual(set(lookup), {"E120", "E160a(i)", "E322", "E422", "E471", "E472a", "E901", "E904", "E920"})
        self.assertTrue(DATA["identityOnly"])
        self.assertNotIn("halal", json.dumps(DATA, ensure_ascii=False).casefold())

    def test_e_number_spacing_suffix_and_roman_subtype_are_canonical(self):
        self.assertEqual(MODULE.canonicalize_additive_id("E 120"), "E120")
        self.assertEqual(MODULE.canonicalize_additive_id("e 472 A"), "E472a")
        self.assertEqual(MODULE.canonicalize_additive_id("E 160 a ( I )"), "E160a(i)")
        text = "Farbstoffe: E 1 2 0, E160 a (i); Emulgator E 472 a"
        matches = MODULE.match_additive_identities(DATA, text, "de-DE")
        self.assertEqual({item["id"] for item in matches}, {"E120", "E160a(i)", "E472a"})

    def test_german_and_english_names_match_inside_function_class_syntax(self):
        german = MODULE.match_additive_identities(DATA, "Emulgator: Lecithine; Überzugsmittel: Schellack", "de")
        english = MODULE.match_additive_identities(DATA, "Emulsifier: lecithins; glazing agent: shellac", "en-GB")
        self.assertEqual({item["id"] for item in german}, {"E322", "E904"})
        self.assertEqual({item["id"] for item in english}, {"E322", "E904"})

    def test_name_boundaries_avoid_ordinary_word_false_positive(self):
        matches = MODULE.match_additive_identities(DATA, "karminrote Dekoration; lecithinarm", "de")
        self.assertEqual(matches, [])

    def test_multiple_official_origin_possibilities_remain_possibilities(self):
        matches = MODULE.match_additive_identities(DATA, "Lecithine", "de")
        match = next(item for item in matches if item["id"] == "E322")
        kinds = {item["kind"] for item in match["originPossibilities"]}
        self.assertEqual(kinds, {"animal-derived", "plant-derived"})
        self.assertNotIn("halalConclusion", match)
        self.assertNotIn("originConclusion", match)

    def test_alias_collision_is_rejected(self):
        broken = copy.deepcopy(DATA)
        e471 = next(item for item in broken["entries"] if item["id"] == "E471")
        e471["aliases"]["en"].append("Lecithins")
        with self.assertRaises(MODULE.AdditiveReferenceError):
            MODULE.validate_additive_identities(broken)

    def test_removed_entry_is_not_matched_but_is_preserved_for_review(self):
        changed = copy.deepcopy(DATA)
        e904 = next(item for item in changed["entries"] if item["id"] == "E904")
        e904["status"] = "removed"
        MODULE.validate_additive_identities(changed)
        self.assertEqual(MODULE.match_additive_identities(changed, "E904 shellac", "en"), [])

    def test_change_report_selectively_links_methodology_e_number_rules(self):
        current = copy.deepcopy(DATA)
        current["datasetVersion"] = "2.0.1"
        current["referenceRevision"] = "eu-additives-test-change"
        e471 = next(item for item in current["entries"] if item["id"] == "E471")
        e471["technologicalFunctions"].append("test-only-function")
        report = MODULE.diff_additive_references(DATA, current, METHODOLOGY)
        self.assertTrue(report["reviewRequired"])
        self.assertEqual(report["affectedAdditiveIDs"], ["E471"])
        self.assertIn("emulsifier-origin", report["affectedMethodologyRuleIDs"])
        self.assertEqual(report["changed"], [{"id": "E471", "fields": ["technologicalFunctions"]}])

    def test_compact_sqlite_is_deterministic_and_indexed(self):
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "first.sqlite3"
            second = Path(directory) / "second.sqlite3"
            MODULE.build_reference_sqlite(DATA, first)
            MODULE.build_reference_sqlite(DATA, second)
            first_db = sqlite3.connect(first)
            second_db = sqlite3.connect(second)
            try:
                self.assertEqual("\n".join(first_db.iterdump()), "\n".join(second_db.iterdump()))
                self.assertEqual(first_db.execute("SELECT COUNT(*) FROM additives").fetchone()[0], 9)
                plan = " ".join(row[3].upper() for row in first_db.execute("EXPLAIN QUERY PLAN SELECT additive_id FROM additive_names WHERE language=? AND normalized_name=?", ("de", "lecithine")))
                self.assertIn("INDEX", plan)
                self.assertEqual(first_db.execute("SELECT additive_id FROM additive_names WHERE language=? AND normalized_name=?", ("de", "lecithine")).fetchall(), [("E322",)])
            finally:
                first_db.close()
                second_db.close()


if __name__ == "__main__":
    unittest.main()
