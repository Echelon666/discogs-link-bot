"""Offline tests for v5 conservative normalization and full-field evaluation."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "discogs_bot_verify.py"
SPEC = importlib.util.spec_from_file_location("discogs_bot_verify", SCRIPT)
BOT = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(BOT)


class NormalizationTests(unittest.TestCase):
    def test_typographic_apostrophes_normalize(self):
        self.assertEqual(BOT.norm_text("O’Sullivan"), BOT.norm_text("O'Sullivan"))

    def test_accent_is_preserved(self):
        self.assertNotEqual(BOT.norm_text("Beyoncé"), BOT.norm_text("Beyonce"))

    def test_title_punctuation_only_keeps_all_words_and_order(self):
        self.assertEqual(BOT.compare_titles("Relax: Edition Two", "Relax (Edition Two)"), "PUNCTUATION_EQUIVALENT")
        self.assertEqual(BOT.compare_titles("Relax Edition Two", "Relax Edition Three"), "MISMATCH")
        self.assertEqual(BOT.compare_titles("Don't Stop", "Dont Stop"), "MISMATCH")

    def test_catalog_number_whitespace_normalization_preserves_dashes(self):
        self.assertEqual(BOT.compare_catalog_numbers("CDEPC6537", "CDEPC 6537"), "WHITESPACE_NORMALIZATION")
        self.assertEqual(BOT.compare_catalog_numbers("EX-123", "EX123"), "MISMATCH")
        self.assertEqual(BOT.compare_catalog_numbers("EX–123", "EX-123"), "SAFE_NORMALIZATION")

    def test_label_suffix_requires_catalog_number_corroboration(self):
        self.assertTrue(BOT.strip_records_suffix("Keytone Records") == BOT.strip_records_suffix("Keytone"))


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "mbid": "11111111-1111-4111-8111-111111111111",
            "barcode": "012345678905",
            "discogs_url": "https://www.discogs.com/release/12345-test-release",
            "discogs_release_id": "12345",
        }
        self.mb = {
            "id": self.row["mbid"],
            "title": "Relax: Edition Two",
            "barcode": "012345678905",
            "artist-credit": [{"name": "Gilbert O’Sullivan", "artist": {"name": "Gilbert O’Sullivan"}, "joinphrase": ""}],
            "relations": [],
            "label-info": [{"label": {"name": "Keytone Records"}, "catalog-number": "CDEPC6537"}],
        }
        self.dg = {
            "id": 12345,
            "title": "Relax (Edition Two)",
            "artists": [{"name": "Gilbert O'Sullivan", "anv": "", "join": ""}],
            "identifiers": [{"type": "Barcode", "value": "012345678905"}],
            "labels": [{"name": "Keytone", "catno": "CDEPC 6537"}],
        }

    def evaluate(self):
        return BOT.evaluate(self.row, self.mb, self.dg, set())

    def test_title_punctuation_artist_typography_label_suffix_and_catno_whitespace_can_be_verified(self):
        result = self.evaluate()
        self.assertEqual(result["status"], "AUTO_CANDIDATE")
        self.assertEqual(result["title_match_type"], "PUNCTUATION_EQUIVALENT")
        self.assertEqual(result["label_check"], "MATCH_RECORDS_SUFFIX_CONFIRMED_BY_CATALOG_NUMBER")
        self.assertEqual(result["catalog_number_check"], "MATCH_AFTER_SAFE_NORMALIZATION")
        self.assertIn("TITLE_PUNCTUATION_ONLY_NORMALIZATION", result["normalization_actions"])

    def test_word_mismatch_remains_manual_even_if_other_fields_match(self):
        self.dg["title"] = "Relax (Edition One)"
        result = self.evaluate()
        self.assertEqual(result["status"], "MANUAL_TITLE_MISMATCH")

    def test_catalog_hyphen_absence_does_not_auto_match(self):
        self.dg["labels"] = [{"name": "Keytone Records", "catno": "CDEPC6537"}]
        self.mb["label-info"] = [{"label": {"name": "Keytone Records"}, "catalog-number": "CDEPC-6537"}]
        result = self.evaluate()
        self.assertNotEqual(result["status"], "AUTO_CANDIDATE")
        self.assertIn("MANUAL_CATALOG_NUMBER_MISMATCH", result["all_findings"])

    def test_records_suffix_without_matching_catalog_number_does_not_auto_match(self):
        self.dg["labels"] = [{"name": "Keytone", "catno": "OTHER-999"}]
        result = self.evaluate()
        self.assertNotEqual(result["status"], "AUTO_CANDIDATE")
        self.assertIn("MANUAL_LABEL_MISMATCH", result["all_findings"])
        self.assertIn("MANUAL_CATALOG_NUMBER_MISMATCH", result["all_findings"])

    def test_full_checks_run_even_when_title_mismatches(self):
        self.dg["title"] = "A Completely Different Title"
        self.dg["labels"] = [{"name": "Wrong Label", "catno": "WRONG-999"}]
        result = self.evaluate()
        self.assertEqual(result["status"], "MANUAL_TITLE_MISMATCH")
        self.assertIn("MANUAL_LABEL_MISMATCH", result["all_findings"])
        self.assertIn("MANUAL_CATALOG_NUMBER_MISMATCH", result["all_findings"])
        self.assertIn("MANUAL_LABEL_CATALOG_PAIR_MISMATCH", result["all_findings"])

    def test_accent_mismatch_remains_manual(self):
        self.mb["artist-credit"][0]["name"] = "Beyoncé"
        self.mb["artist-credit"][0]["artist"]["name"] = "Beyoncé"
        self.dg["artists"] = [{"name": "Beyonce", "anv": "", "join": ""}]
        result = self.evaluate()
        self.assertIn("MANUAL_ARTIST_MISMATCH", result["all_findings"])

    def test_upc_ean_equivalence_is_preserved(self):
        self.row["barcode"] = "0077779032921"
        self.mb["barcode"] = "0077779032921"
        self.dg["identifiers"] = [{"type": "Barcode", "value": "077779032921"}]
        result = self.evaluate()
        self.assertEqual(result["discogs_barcode_check"], "MATCH_UPC_A_EAN_13_EQUIVALENT")


if __name__ == "__main__":
    unittest.main(verbosity=2)
