import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "discogs_bot_verify_v6.py"
SPEC = importlib.util.spec_from_file_location("discogs_bot_verify_v6", SCRIPT)
BOT = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(BOT)


class OptionalIdentityChecksTests(unittest.TestCase):
    def setUp(self):
        self.row = {
            "mbid": "11111111-1111-4111-8111-111111111111",
            "barcode": "012345678905",
            "discogs_url": "https://www.discogs.com/release/12345-test-release",
            "discogs_release_id": "12345",
        }
        self.mb = {
            "id": self.row["mbid"], "title": "Relax: Edition Two", "date": "2002-10-10",
            "country": "GB", "barcode": "012345678905", "media": [{"format": "CD"}],
            "artist-credit": [{"name": "Gilbert O’Sullivan", "artist": {"name": "Gilbert O’Sullivan"}, "joinphrase": ""}],
            "relations": [], "label-info": [{"label": {"name": "Keytone Records"}, "catalog-number": "CDEPC6537"}],
        }
        self.dg = {
            "id": 12345, "title": "Relax (Edition Two)", "year": 2002, "released": "2002-10-15",
            "country": "UK", "formats": [{"name": "CD", "qty": "1"}],
            "artists": [{"name": "Gilbert O'Sullivan", "anv": "", "join": ""}],
            "identifiers": [{"type": "Barcode", "value": "012345678905"}],
            "labels": [{"name": "Keytone Records", "catno": "CDEPC6537"}],
        }

    def evaluate(self):
        return BOT.evaluate(self.row, self.mb, self.dg, set())

    def test_all_three_present_are_compared(self):
        result = self.evaluate()
        self.assertEqual(result["status"], "AUTO_CANDIDATE")
        self.assertEqual(result["media_format_check"], "MATCH")
        self.assertEqual(result["year_check"], "MATCH")
        self.assertEqual(result["country_check"], "MATCH")

    def test_only_year_and_country_present_skips_medium(self):
        self.mb["media"] = []
        result = self.evaluate()
        self.assertEqual(result["status"], "AUTO_CANDIDATE")
        self.assertEqual(result["media_format_check"], "NOT_PRESENT_IN_MUSICBRAINZ")
        self.assertEqual(result["year_check"], "MATCH")
        self.assertEqual(result["country_check"], "MATCH")

    def test_only_year_present_skips_medium_and_country(self):
        self.mb["media"] = []
        self.mb["country"] = ""
        result = self.evaluate()
        self.assertEqual(result["status"], "AUTO_CANDIDATE")
        self.assertEqual(result["media_format_check"], "NOT_PRESENT_IN_MUSICBRAINZ")
        self.assertEqual(result["year_check"], "MATCH")
        self.assertEqual(result["country_check"], "NOT_PRESENT_IN_MUSICBRAINZ")

    def test_only_medium_year_present_skips_country(self):
        self.mb["country"] = ""
        result = self.evaluate()
        self.assertEqual(result["status"], "AUTO_CANDIDATE")
        self.assertEqual(result["media_format_check"], "MATCH")
        self.assertEqual(result["year_check"], "MATCH")
        self.assertEqual(result["country_check"], "NOT_PRESENT_IN_MUSICBRAINZ")

    def test_all_three_missing_in_musicbrainz_are_skipped(self):
        self.mb["media"] = []
        self.mb["date"] = ""
        self.mb["country"] = ""
        result = self.evaluate()
        self.assertEqual(result["status"], "AUTO_CANDIDATE")
        self.assertEqual(result["media_format_check"], "NOT_PRESENT_IN_MUSICBRAINZ")
        self.assertEqual(result["year_check"], "NOT_PRESENT_IN_MUSICBRAINZ")
        self.assertEqual(result["country_check"], "NOT_PRESENT_IN_MUSICBRAINZ")

    def test_missing_discogs_medium_is_manual_when_mb_has_medium(self):
        self.dg["formats"] = []
        result = self.evaluate()
        self.assertEqual(result["media_format_check"], "MISSING")
        self.assertIn("MANUAL_MEDIA_FORMAT_MISSING", result["all_findings"])
        self.assertNotEqual(result["status"], "AUTO_CANDIDATE")

    def test_missing_discogs_year_is_manual_when_mb_has_year(self):
        self.dg["year"] = None
        self.dg["released"] = ""
        result = self.evaluate()
        self.assertEqual(result["year_check"], "MISSING")
        self.assertIn("MANUAL_YEAR_MISSING", result["all_findings"])
        self.assertNotEqual(result["status"], "AUTO_CANDIDATE")

    def test_missing_discogs_country_is_manual_when_mb_has_country(self):
        self.dg["country"] = ""
        result = self.evaluate()
        self.assertEqual(result["country_check"], "MISSING")
        self.assertIn("MANUAL_COUNTRY_MISSING", result["all_findings"])
        self.assertNotEqual(result["status"], "AUTO_CANDIDATE")

    def test_ambiguous_musicbrainz_event_year_is_not_skipped(self):
        self.mb["date"] = ""
        self.mb["release-events"] = [{"date": "2001"}, {"date": "2002"}]
        result = self.evaluate()
        self.assertEqual(result["year_check"], "AMBIGUOUS")
        self.assertIn("MANUAL_YEAR_AMBIGUOUS", result["all_findings"])

    def test_ambiguous_musicbrainz_event_countries_are_not_skipped(self):
        self.mb["country"] = ""
        self.mb["release-events"] = [
            {"area": {"iso-3166-1-codes": ["GB"]}},
            {"area": {"iso-3166-1-codes": ["FR"]}},
        ]
        result = self.evaluate()
        self.assertEqual(result["country_check"], "AMBIGUOUS")
        self.assertIn("MANUAL_COUNTRY_AMBIGUOUS", result["all_findings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
