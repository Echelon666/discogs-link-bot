"""Offline smoke tests for the V8 Phase 1 JSONL -> Phase 2 handoff."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


BUILDER = load_module("discogs_barcode_builder_v8", "discogs_barcode_builder.py")
VERIFY = load_module("discogs_bot_verify_v8", "discogs_bot_verify.py")


class PipelineHandoffTests(unittest.TestCase):
    def test_stage1_jsonl_snapshot_is_accepted_by_stage2(self):
        mbid = "11111111-1111-4111-8111-111111111111"
        mb_release = {
            "id": mbid, "title": "Example Album", "barcode": "012345678905",
            "artist-credit": [], "media": [{"format": "CD", "track-count": 10, "tracks": []}],
            "release-events": [{"date": "2002-01-01", "area": {"iso-3166-1-codes": ["GB"]}}],
            "relations": [], "label-info": [],
        }
        group = {
            "mbid": mbid, "report_position": 1, "status": "READY_FOR_DISCOGS",
            "barcode": "012345678905", "mb_artist": "Example Artist", "mb_title": "Example Album",
            "mb_release": mb_release,
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            report = root / "report.txt"
            report.write_text("synthetic test report\n", encoding="utf-8")
            tsv, snapshot, summary = BUILDER.write_outputs(root / "phase1", {mbid: group}, report, 1, 0)
            rows = VERIFY.load_stage1_snapshot(snapshot)
            self.assertTrue(tsv.is_file())
            self.assertTrue(summary.is_file())
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["mbid"], mbid)
            self.assertEqual(rows[0]["barcode"], "012345678905")
            self.assertEqual(rows[0]["mb_release"]["id"], mbid)

    def test_results_txt_contains_only_unambiguous_mbid_discogs_pair(self):
        first = "11111111-1111-4111-8111-111111111111"
        ambiguous = "22222222-2222-4222-8222-222222222222"
        progress = {
            first: {
                "mbid": first, "report_position": 1, "status": "DONE_WITH_RESULTS",
                "results": [{"status": "AUTO_CANDIDATE", "discogs_url": "https://www.discogs.com/release/123456"}],
            },
            ambiguous: {
                "mbid": ambiguous, "report_position": 2, "status": "DONE_WITH_RESULTS",
                "results": [
                    {"status": "AUTO_CANDIDATE", "discogs_url": "https://www.discogs.com/release/111111"},
                    {"status": "AUTO_CANDIDATE", "discogs_url": "https://www.discogs.com/release/222222"},
                ],
            },
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "stage1.jsonl"
            source.write_text("{}\n", encoding="utf-8")
            report, summary, results = VERIFY.write_stage2_outputs(root / "phase2", progress, source, 2, 0)
            lines = results.read_text(encoding="utf-8").splitlines()
            self.assertTrue(report.is_file())
            self.assertTrue(summary.is_file())
            self.assertEqual(lines, [f"{first}\thttps://www.discogs.com/release/123456"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
