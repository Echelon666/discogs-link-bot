"""Integration smoke tests for the Stage 1 -> Stage 2 V6 handoff."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load_module(module_name: str, script_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / script_name)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


BUILDER = load_module("discogs_barcode_builder_v6", "discogs_barcode_builder.py")
VERIFY = load_module("discogs_bot_verify_v6", "discogs_bot_verify_v6.py")


class PipelineHandoffTests(unittest.TestCase):
    def test_stage1_candidate_tsv_is_accepted_by_stage2_v6(self):
        mbid = "11111111-1111-4111-8111-111111111111"
        group = {
            "mbid": mbid, "report_position": 1, "status": "SEARCHED_MULTIPLE",
            "barcode": "012345678905", "mb_artist": "Example Artist", "mb_title": "Example Album",
            "search_total_results": 2, "search_pages": 1,
            "candidates": [{
                "id": "123456", "title": "Example Artist - Example Album",
                "candidate_source": "MASTER_VERSION", "master_id": "654321",
                "version_year": "2002", "version_country": "UK", "version_format": "CD; Album",
            }],
        }
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            tsv_path, _, _ = BUILDER.write_outputs(root / "out", {mbid: group}, root / "report.txt", 1, 1)
            rows = VERIFY.load_input(tsv_path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["mbid"], mbid)
        self.assertEqual(rows[0]["discogs_release_id"], "123456")
        self.assertEqual(rows[0]["version_country"], "UK")
        self.assertEqual(rows[0]["version_format"], "CD; Album")


if __name__ == "__main__":
    unittest.main(verbosity=2)
