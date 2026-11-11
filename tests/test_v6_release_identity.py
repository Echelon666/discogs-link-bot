"""Offline tests for Stage 2 v6 medium, year and country verification."""
import importlib.util
from pathlib import Path
import unittest
import tempfile
import csv

SCRIPT = Path(__file__).resolve().parents[1] / "discogs_bot_verify_v6.py"
SPEC = importlib.util.spec_from_file_location("discogs_bot_verify_v6", SCRIPT)
BOT = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(BOT)


class MediumFormatTests(unittest.TestCase):
    def test_cd_matches_cd(self):
        status, mb, dg, detail = BOT.compare_media_formats(
            {"media": [{"format": "CD"}]},
            {"formats": [{"name": "CD", "qty": "1", "descriptions": ["Album"]}]},
        )
        self.assertEqual(status, "MATCH")
        self.assertEqual(mb, "CD")
        self.assertEqual(dg, "CD x1")
        self.assertEqual(detail, "")

    def test_digital_media_matches_discogs_file(self):
        status, *_ = BOT.compare_media_formats(
            {"media": [{"format": "Digital Media"}]},
            {"formats": [{"name": "File", "qty": "1"}]},
        )
        self.assertEqual(status, "MATCH")

    def test_cd_does_not_match_digital_file(self):
        status, *_ = BOT.compare_media_formats(
            {"media": [{"format": "CD"}]},
            {"formats": [{"name": "File", "qty": "1"}]},
        )
        self.assertEqual(status, "MISMATCH")

    def test_multi_disc_quantity_is_checked(self):
        status, *_ = BOT.compare_media_formats(
            {"media": [{"format": "CD"}, {"format": "CD"}]},
            {"formats": [{"name": "CD", "qty": "2"}]},
        )
        self.assertEqual(status, "MATCH")

        status, *_ = BOT.compare_media_formats(
            {"media": [{"format": "CD"}, {"format": "CD"}]},
            {"formats": [{"name": "CD", "qty": "1"}]},
        )
        self.assertEqual(status, "MISMATCH")

    def test_missing_and_unknown_formats_fail_closed(self):
        self.assertEqual(
            BOT.compare_media_formats({"media": [{"format": "CD"}]}, {"formats": []})[0],
            "MISSING",
        )
        self.assertEqual(
            BOT.compare_media_formats(
                {"media": [{"format": "CD"}]},
                {"formats": [{"name": "Cylinder", "qty": "1"}]},
            )[0],
            "UNMAPPABLE",
        )

    def test_missing_discogs_quantity_is_unmappable(self):
        self.assertEqual(
            BOT.compare_media_formats(
                {"media": [{"format": "CD"}]},
                {"formats": [{"name": "CD"}]},
            )[0],
            "UNMAPPABLE",
        )


class YearTests(unittest.TestCase):
    def test_same_release_year_matches_even_when_full_dates_differ(self):
        status, mb_year, dg_year, _ = BOT.compare_release_years(
            {"date": "2002-10-10"},
            {"year": 2002, "released": "2002-10-15"},
        )
        self.assertEqual(status, "MATCH")
        self.assertEqual(mb_year, "2002")
        self.assertEqual(dg_year, "2002")

    def test_different_year_is_manual_mismatch(self):
        self.assertEqual(
            BOT.compare_release_years({"date": "2002"}, {"year": 2003})[0],
            "MISMATCH",
        )

    def test_missing_year_is_manual(self):
        self.assertEqual(
            BOT.compare_release_years({"date": ""}, {"year": 2003})[0],
            "MISSING",
        )

    def test_conflicting_discogs_year_and_released_is_ambiguous(self):
        self.assertEqual(
            BOT.compare_release_years(
                {"date": "2002"},
                {"year": 2003, "released": "2002-01-01"},
            )[0],
            "AMBIGUOUS",
        )

    def test_multiple_mb_release_event_years_are_ambiguous_if_primary_date_missing(self):
        self.assertEqual(
            BOT.compare_release_years(
                {"date": None, "release-events": [{"date": "2001"}, {"date": "2002"}]},
                {"year": 2002},
            )[0],
            "AMBIGUOUS",
        )


class CountryTests(unittest.TestCase):
    def test_uk_discogs_alias_matches_musicbrainz_gb(self):
        self.assertEqual(
            BOT.compare_release_countries({"country": "GB"}, {"country": "UK"})[0],
            "MATCH",
        )

    def test_europe_special_area_matches(self):
        self.assertEqual(
            BOT.compare_release_countries({"country": "XE"}, {"country": "Europe"})[0],
            "MATCH",
        )

    def test_worldwide_special_area_matches(self):
        self.assertEqual(
            BOT.compare_release_countries({"country": "XW"}, {"country": "Worldwide"})[0],
            "MATCH",
        )

    def test_country_mismatch(self):
        self.assertEqual(
            BOT.compare_release_countries({"country": "GB"}, {"country": "US"})[0],
            "MISMATCH",
        )

    def test_missing_country_is_manual(self):
        self.assertEqual(
            BOT.compare_release_countries({"country": "GB"}, {"country": ""})[0],
            "MISSING",
        )

    def test_compound_unknown_region_is_unmappable(self):
        self.assertEqual(
            BOT.compare_release_countries({"country": "GB"}, {"country": "UK & Europe"})[0],
            "UNMAPPABLE",
        )

    def test_multiple_mb_event_countries_are_ambiguous(self):
        mb = {
            "release-events": [
                {"area": {"iso-3166-1-codes": ["GB"]}},
                {"area": {"iso-3166-1-codes": ["FR"]}},
            ]
        }
        self.assertEqual(
            BOT.compare_release_countries(mb, {"country": "UK"})[0],
            "AMBIGUOUS",
        )


class InputValidationTests(unittest.TestCase):
    def _write_tsv(self, rows):
        temp = tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", suffix=".tsv", delete=False)
        self.addCleanup(Path(temp.name).unlink, missing_ok=True)
        fields = list(rows[0].keys())
        writer = csv.DictWriter(temp, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)
        temp.close()
        return Path(temp.name)

    def test_stage2_input_must_contain_only_auto_candidates(self):
        base = {
            "mbid": "11111111-1111-4111-8111-111111111111",
            "barcode": "012345678905",
            "discogs_url": "https://www.discogs.com/release/12345-test",
            "discogs_release_id": "12345",
        }
        good = {**base, "status": "AUTO_CANDIDATE"}
        self.assertEqual(len(BOT.load_input(self._write_tsv([good]))), 1)
        bad = {**base, "status": "MANUAL_YEAR_MISMATCH"}
        with self.assertRaisesRegex(RuntimeError, "not AUTO_CANDIDATE"):
            BOT.load_input(self._write_tsv([bad]))

    def test_stage1_input_without_status_column_remains_supported(self):
        row = {
            "mbid": "11111111-1111-4111-8111-111111111111",
            "barcode": "012345678905",
            "discogs_url": "https://www.discogs.com/release/12345-test",
            "discogs_release_id": "12345",
        }
        self.assertEqual(len(BOT.load_input(self._write_tsv([row]))), 1)


class FullEvaluationTests(unittest.TestCase):
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
            "date": "2002-10-10",
            "country": "GB",
            "barcode": "012345678905",
            "media": [{"format": "CD"}],
            "artist-credit": [{"name": "Gilbert O’Sullivan", "artist": {"name": "Gilbert O’Sullivan"}, "joinphrase": ""}],
            "relations": [],
            "label-info": [{"label": {"name": "Keytone Records"}, "catalog-number": "CDEPC6537"}],
        }
        self.dg = {
            "id": 12345,
            "title": "Relax (Edition Two)",
            "year": 2002,
            "released": "2002-10-15",
            "country": "UK",
            "formats": [{"name": "CD", "qty": "1"}],
            "artists": [{"name": "Gilbert O'Sullivan", "anv": "", "join": ""}],
            "identifiers": [{"type": "Barcode", "value": "012345678905"}],
            "labels": [{"name": "Keytone", "catno": "CDEPC 6537"}],
        }

    def evaluate(self):
        return BOT.evaluate(self.row, self.mb, self.dg, set())

    def test_all_v6_required_fields_matching_can_be_auto_candidate(self):
        result = self.evaluate()
        self.assertEqual(result["status"], "AUTO_CANDIDATE")
        self.assertEqual(result["media_format_check"], "MATCH")
        self.assertEqual(result["year_check"], "MATCH")
        self.assertEqual(result["country_check"], "MATCH")
        for token in ["MEDIA_FORMAT_MATCH", "YEAR_MATCH", "COUNTRY_MATCH"]:
            self.assertIn(token, result["reason"])

    def test_cd_vs_digital_prevents_auto_candidate(self):
        self.dg["formats"] = [{"name": "File", "qty": "1"}]
        result = self.evaluate()
        self.assertNotEqual(result["status"], "AUTO_CANDIDATE")
        self.assertIn("MANUAL_MEDIA_FORMAT_MISMATCH", result["all_findings"])

    def test_year_difference_prevents_auto_candidate(self):
        self.dg["year"] = 2003
        self.dg["released"] = "2003-10-15"
        result = self.evaluate()
        self.assertIn("MANUAL_YEAR_MISMATCH", result["all_findings"])

    def test_country_difference_prevents_auto_candidate(self):
        self.dg["country"] = "US"
        result = self.evaluate()
        self.assertIn("MANUAL_COUNTRY_MISMATCH", result["all_findings"])

    def test_all_checks_continue_after_a_media_mismatch(self):
        self.dg["formats"] = [{"name": "File", "qty": "1"}]
        self.dg["year"] = 2003
        self.dg["released"] = "2003-10-15"
        self.dg["country"] = "US"
        result = self.evaluate()
        self.assertIn("MANUAL_MEDIA_FORMAT_MISMATCH", result["all_findings"])
        self.assertIn("MANUAL_YEAR_MISMATCH", result["all_findings"])
        self.assertIn("MANUAL_COUNTRY_MISMATCH", result["all_findings"])

    def test_missing_v6_field_prevents_auto_candidate(self):
        self.mb["media"] = []
        result = self.evaluate()
        self.assertIn("MANUAL_MEDIA_FORMAT_MISSING", result["all_findings"])
        self.assertNotEqual(result["status"], "AUTO_CANDIDATE")


if __name__ == "__main__":
    unittest.main(verbosity=2)