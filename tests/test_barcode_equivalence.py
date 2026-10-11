"""Offline tests for strict barcode comparison; no API requests are made."""
import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "discogs_bot_verify.py"
SPEC = importlib.util.spec_from_file_location("discogs_bot_verify", SCRIPT)
BOT = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(BOT)


class BarcodeEquivalenceTests(unittest.TestCase):
    def test_identical_barcodes(self):
        self.assertTrue(BOT.barcodes_equivalent("0077779032921", "0077779032921"))

    def test_ean13_leading_zero_equals_upca12(self):
        self.assertTrue(BOT.barcodes_equivalent("0077779032921", "077779032921"))
        self.assertTrue(BOT.barcodes_equivalent("077779032921", "0077779032921"))

    def test_nonzero_leading_digit_is_not_equivalent(self):
        self.assertFalse(BOT.barcodes_equivalent("1077779032921", "077779032921"))

    def test_arbitrary_leading_zeros_are_not_removed(self):
        self.assertFalse(BOT.barcodes_equivalent("00123456789012", "123456789012"))

    def test_separators_are_ignored(self):
        self.assertTrue(BOT.barcodes_equivalent("007 77790-32921", "077779032921"))

    def test_missing_barcode_does_not_match(self):
        self.assertFalse(BOT.barcodes_equivalent("", "077779032921"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
