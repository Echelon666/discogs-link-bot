"""V8.1 tests for the direct Discogs barcode-result limit."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "discogs_bot_verify.py"
SPEC = importlib.util.spec_from_file_location("discogs_bot_verify_v8_1", SCRIPT)
VERIFY = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(VERIFY)


def search_response(total, pages, rows=None):
    if rows is None:
        rows = [
            {
                "id": index, "type": "release", "title": f"Direct release {index}",
                "master_id": 5000, "year": "2003", "country": "UK", "format": ["CD", "Album"],
            }
            for index in range(1, min(total, 100) + 1)
        ]
    return {"pagination": {"items": total, "pages": pages}, "results": rows}


class DirectBarcodeLimitTests(unittest.TestCase):
    def run_search(self, total, pages):
        seen_pages = []
        def fake(url, headers=None, timeout=45):
            page = int(parse_qs(urlparse(url).query)["page"][0])
            seen_pages.append(page)
            return search_response(total, pages), {}
        with tempfile.TemporaryDirectory() as folder:
            cache = VERIFY.JsonCache(Path(folder))
            with patch.object(VERIFY, "http_get_json", side_effect=fake):
                result = None
                error = None
                try:
                    result = VERIFY.fetch_all_discogs_results_v8(
                        "4893391073526", "test-token", "test-agent", 0.0, cache, [0.0]
                    )
                except VERIFY.TooManyDiscogsResults as exc:
                    error = exc
        return result, error, seen_pages

    def test_48_results_are_all_direct_candidates(self):
        result, error, pages = self.run_search(48, 1)
        self.assertIsNone(error)
        self.assertEqual(pages, [1])
        total, page_count, rows = result
        candidates = VERIFY.build_direct_candidates_v8(rows)
        self.assertEqual((total, page_count), (48, 1))
        self.assertEqual(len(candidates), 48)
        self.assertTrue(all(row["candidate_source"] == "BARCODE_SEARCH" for row in candidates))
        self.assertTrue(all(row["master_id"] == "5000" for row in candidates))
        self.assertFalse(any(row["candidate_source"] == "MASTER_VERSION" for row in candidates))

    def test_exactly_100_results_are_allowed(self):
        result, error, pages = self.run_search(100, 1)
        self.assertIsNone(error)
        self.assertEqual(pages, [1])
        self.assertEqual(len(result[2]), 100)

    def test_101_results_skip_without_fetching_page_two(self):
        result, error, pages = self.run_search(101, 2)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(error.total_items, 101)
        self.assertEqual(error.total_pages, 2)
        self.assertEqual(pages, [1])

    def test_2117_results_skip_without_fetching_more_pages(self):
        result, error, pages = self.run_search(2117, 22)
        self.assertIsNone(result)
        self.assertIsNotNone(error)
        self.assertEqual(error.total_items, 2117)
        self.assertEqual(error.total_pages, 22)
        self.assertEqual(pages, [1])

    def test_duplicate_direct_release_ids_are_removed(self):
        rows = [
            {"id": 10, "type": "release", "title": "First"},
            {"id": 10, "type": "release", "title": "Duplicate"},
            {"id": 11, "type": "release", "title": "Second"},
        ]
        candidates = VERIFY.build_direct_candidates_v8(rows)
        self.assertEqual([row["id"] for row in candidates], ["10", "11"])


class ProgressRecoveryTests(unittest.TestCase):
    def test_truncated_final_jsonl_record_is_removed_for_future_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "progress.jsonl"
            path.write_text(
                '{"mbid":"11111111-1111-4111-8111-111111111111","status":"DONE_NO_DISCOGS_RESULTS"}\n'
                '{"mbid":"22222222-2222-4222-8222-',
                encoding="utf-8",
            )
            records = VERIFY.load_stage2_progress(path)
            self.assertEqual(len(records), 1)
            self.assertNotIn("22222222-2222-4222-8222-", path.read_text(encoding="utf-8"))
            with path.open("a", encoding="utf-8", newline="\n") as stream:
                VERIFY.append_stage2_progress(
                    stream, {"mbid": "33333333-3333-4333-8333-333333333333", "status": "DONE_NO_DISCOGS_RESULTS"}, 2
                )
            self.assertEqual(len(VERIFY.load_stage2_progress(path)), 2)

    def test_invalid_middle_jsonl_record_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "progress.jsonl"
            path.write_text('{"mbid":"11111111-1111-4111-8111-111111111111"}\n{bad}\n{}\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid JSON at line 2"):
                VERIFY.load_stage2_progress(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
