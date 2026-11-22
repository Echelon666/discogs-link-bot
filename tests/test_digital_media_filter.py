import unittest

import discogs_barcode_builder as stage1
import discogs_bot_verify_v6 as stage2


class DigitalMediaFilterTests(unittest.TestCase):
    def test_exact_digital_media_is_detected_case_and_whitespace_insensitively(self):
        releases = [
            {"media": [{"format": "Digital Media"}]},
            {"media": [{"format": " digital   media "}]},
            {"media": [{"format": "CD"}, {"format": "Digital Media"}]},
        ]
        for release in releases:
            with self.subTest(release=release):
                self.assertTrue(stage1.has_digital_media_format(release))
                self.assertTrue(stage2.has_digital_media_format(release))

    def test_physical_formats_are_not_skipped(self):
        for media_format in ("CD", "Vinyl", "DVD", "Blu-ray"):
            release = {"media": [{"format": media_format}]}
            with self.subTest(media_format=media_format):
                self.assertFalse(stage1.has_digital_media_format(release))
                self.assertFalse(stage2.has_digital_media_format(release))

    def test_missing_or_malformed_media_are_not_skipped(self):
        releases = [{}, {"media": None}, {"media": "Digital Media"}, {"media": [None, {}]}]
        for release in releases:
            with self.subTest(release=release):
                self.assertFalse(stage1.has_digital_media_format(release))
                self.assertFalse(stage2.has_digital_media_format(release))


if __name__ == "__main__":
    unittest.main()
