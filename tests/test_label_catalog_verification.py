import importlib.util
import unittest
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "discogs_bot_verify.py"
SPEC = importlib.util.spec_from_file_location("discogs_bot_verify", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError("Could not load discogs_bot_verify.py")
bot = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bot)


class LabelCatalogVerificationTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "mbid": "11111111-1111-4111-8111-111111111111",
            "barcode": "012345678905",
            "discogs_url": "https://www.discogs.com/release/12345-test-release",
            "discogs_release_id": "12345",
        }
        self.mb = {
            "id": self.row["mbid"],
            "title": "Test Release",
            "barcode": "012345678905",
            "artist-credit": [{"name": "Test Artist", "artist": {"name": "Test Artist"}, "joinphrase": ""}],
            "relations": [],
            "label-info": [{"label": {"name": "Example Records"}, "catalog-number": "EX-123"}],
        }
        self.dg = {
            "id": 12345,
            "title": "Test Release",
            "artists": [{"name": "Test Artist", "anv": "", "join": ""}],
            "identifiers": [{"type": "Barcode", "value": "012345678905"}],
            "labels": [{"name": "Example Records", "catno": "EX-123"}],
        }

    def evaluate(self):
        return bot.evaluate(self.row, self.mb, self.dg, set())

    def test_exact_label_and_catalog_number_pair_is_auto_candidate(self):
        result = self.evaluate()
        self.assertEqual(result["status"], "AUTO_CANDIDATE")
        self.assertEqual(result["label_check"], "MATCH")
        self.assertEqual(result["catalog_number_check"], "MATCH")
        self.assertEqual(result["label_catalog_pair_check"], "MATCH")

    def test_label_mismatch_fails_closed_to_manual(self):
        self.dg["labels"] = [{"name": "Other Records", "catno": "EX-123"}]
        result = self.evaluate()
        self.assertEqual(result["status"], "MANUAL_LABEL_MISMATCH")

    def test_catalog_number_mismatch_fails_closed_to_manual(self):
        self.dg["labels"] = [{"name": "Example Records", "catno": "EX-999"}]
        result = self.evaluate()
        self.assertEqual(result["status"], "MANUAL_CATALOG_NUMBER_MISMATCH")

    def test_missing_discogs_label_fails_closed_to_manual(self):
        self.dg["labels"] = []
        result = self.evaluate()
        self.assertEqual(result["status"], "MANUAL_DISCOGS_LABEL_MISSING")

    def test_missing_discogs_catalog_number_fails_closed_to_manual(self):
        self.dg["labels"] = [{"name": "Example Records", "catno": ""}]
        result = self.evaluate()
        self.assertEqual(result["status"], "MANUAL_DISCOGS_CATALOG_NUMBER_MISSING")

    def test_label_catalog_pair_must_match_on_same_discogs_entry(self):
        self.mb["label-info"] = [
            {"label": {"name": "Label A"}, "catalog-number": "CAT-A"},
            {"label": {"name": "Label B"}, "catalog-number": "CAT-B"},
        ]
        self.dg["labels"] = [
            {"name": "Label A", "catno": "CAT-B"},
            {"name": "Label B", "catno": "CAT-A"},
        ]
        result = self.evaluate()
        self.assertEqual(result["status"], "MANUAL_LABEL_CATALOG_PAIR_MISMATCH")

    def test_missing_musicbrainz_label_or_catalog_number_is_not_a_mismatch(self):
        self.mb["label-info"] = []
        result = self.evaluate()
        self.assertEqual(result["status"], "AUTO_CANDIDATE")
        self.assertEqual(result["label_check"], "NOT_PRESENT_IN_MUSICBRAINZ")
        self.assertEqual(result["catalog_number_check"], "NOT_PRESENT_IN_MUSICBRAINZ")

    def test_label_name_and_catalog_number_ignore_case_and_whitespace_only(self):
        self.dg["labels"] = [{"name": " example   records ", "catno": " ex-123 "}]
        result = self.evaluate()
        self.assertEqual(result["status"], "AUTO_CANDIDATE")

    def test_catalog_punctuation_is_not_discarded(self):
        self.dg["labels"] = [{"name": "Example Records", "catno": "EX123"}]
        result = self.evaluate()
        self.assertEqual(result["status"], "MANUAL_CATALOG_NUMBER_MISMATCH")

    def test_musicbrainz_none_marker_is_not_treated_as_a_catalog_number(self):
        self.mb["label-info"] = [{"label": {"name": "Example Records"}, "catalog-number": "[none]"}]
        self.dg["labels"] = [{"name": "Example Records", "catno": "EX-123"}]
        result = self.evaluate()
        self.assertEqual(result["status"], "AUTO_CANDIDATE")
        self.assertEqual(result["mb_catalog_numbers"], "")
        self.assertEqual(result["label_check"], "MATCH")
        self.assertEqual(result["catalog_number_check"], "NOT_PRESENT_IN_MUSICBRAINZ")


if __name__ == "__main__":
    unittest.main(verbosity=2)
