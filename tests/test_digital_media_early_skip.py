import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


STAGE1 = load_module("digital_media_stage1", "discogs_barcode_builder.py")
STAGE2 = load_module("digital_media_stage2", "discogs_bot_verify_v6.py")


class DigitalMediaEarlySkipTests(unittest.TestCase):
    def test_digital_media_is_detected_case_and_whitespace_insensitively(self):
        releases = [
            {"media": [{"format": "Digital Media"}]},
            {"media": [{"format": " digital   media "}]},
            {"media": [{"format": "CD"}, {"format": "Digital Media"}]},
        ]
        for release in releases:
            with self.subTest(release=release):
                self.assertTrue(STAGE1.has_digital_media_format(release))
                self.assertTrue(STAGE2.has_digital_media_format(release))

    def test_physical_media_formats_are_not_skipped(self):
        for media_format in ("CD", "Vinyl", "DVD", "Blu-ray", "Cassette"):
            with self.subTest(media_format=media_format):
                release = {"media": [{"format": media_format}]}
                self.assertFalse(STAGE1.has_digital_media_format(release))
                self.assertFalse(STAGE2.has_digital_media_format(release))

    def test_missing_or_malformed_media_are_not_skipped(self):
        releases = [{}, {"media": None}, {"media": "Digital Media"}, {"media": [None, {}]}]
        for release in releases:
            with self.subTest(release=release):
                self.assertFalse(STAGE1.has_digital_media_format(release))
                self.assertFalse(STAGE2.has_digital_media_format(release))


if __name__ == "__main__":
    unittest.main(verbosity=2)
