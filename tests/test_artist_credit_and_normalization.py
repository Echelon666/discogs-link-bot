"""Offline tests for conservative artist-credit/text comparison."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "discogs_bot_verify.py"
SPEC = importlib.util.spec_from_file_location("discogs_bot_verify", SCRIPT)
BOT = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(BOT)


class TextNormalizationTests(unittest.TestCase):
    def test_unicode_hyphen_variants_match_ascii_hyphen(self):
        self.assertEqual(BOT.norm_text("De\u2010Phazz"), BOT.norm_text("De-Phazz"))
        self.assertEqual(BOT.norm_text("De\u2011Phazz"), BOT.norm_text("De-Phazz"))

    def test_accents_are_not_removed(self):
        self.assertNotEqual(BOT.norm_text("Les Négresses Vertes"), BOT.norm_text("Les Negresses Vertes"))

    def test_en_dash_typography_is_normalized(self):
        self.assertEqual(BOT.norm_text("A–B"), BOT.norm_text("A-B"))


class DiscogsArtistCreditTests(unittest.TestCase):
    def test_anv_is_used_as_the_release_credit(self):
        release = {"artists": [{"name": "Kyau & Albert", "anv": "Kyau Vs. Albert", "join": ""}]}
        self.assertEqual(BOT.discogs_artist_credit(release), "Kyau Vs. Albert")
        self.assertEqual(BOT.discogs_artist_credit(release, prefer_anv=False), "Kyau & Albert")

    def test_primary_name_is_used_when_no_anv_exists(self):
        release = {"artists": [{"name": "Muki", "join": ""}]}
        self.assertEqual(BOT.discogs_artist_credit(release), "Muki")

    def test_anv_is_visible_in_audit_report(self):
        release = {"artists": [{"name": "Kyau & Albert", "anv": "Kyau Vs. Albert", "join": ""}]}
        self.assertEqual(BOT.discogs_artist_anv_report(release), "Kyau & Albert => Kyau Vs. Albert")


if __name__ == "__main__":
    unittest.main(verbosity=2)
