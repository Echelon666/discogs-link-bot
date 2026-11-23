"""Offline tests for V8.1 direct Discogs barcode search and progress recovery."""
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


def fake_search_response(total_items, total_pages=1):
    return {
        "pagination": {"items": total_items, "pages": total_pages},
        "results": [
            {
                "id": index,
                "type": "release",
                "title": f"Direct result {index}",
                "master_id": 9999,
                "year": "2003",
                "country": "UK",
                "format": ["CD", "Album"],
            }
            for index in range(1, min(total_items, 100) + 1)
        ],
    }


class BarcodeSearchLimitTests(unittest.TestCase):
    def test_exactly_100_results_are_allowed(self):
        self.assertFalse(VERIFY.exceeds_barcode_search_limit_v8(100))
        self.assertTrue(VERIFY.exceeds_barcode_search_limit_v8(101))

    def test_48_results_are_all_direct_candidates_without_master_expansion(self):
        seen_pages = []

        def fake(url, headers=None, timeout=45):
            page = int(parse_qs(urlparse(url).query)["page"][0])
            seen_pages.append(page)
            return fake_search_response(48), {}

        with tempfile.TemporaryDirectory() as folder:
            cache = VERIFY.JsonCache(Path(folder))
            with patch.object(VERIFY, "http_get_json", side_effect=fake):
                total, pages, rows = VERIFY.fetch_discogs_barcode_search_v8(
                    "4893391073526", "token", "test-agent", 0.0, cache, [0.0]
                )

        candidates = VERIFY.build_direct_candidates_v8(rows)
        self.assertEqual((total, pages), (48, 1))
        self.assertEqual(len(candidates), 48)
        self.assertEqual(seen_pages, [1])
        self.assertTrue(all(row["candidate_source"] == "BARCODE_SEARCH" for row in candidates))
        self.assertTrue(all(row["master_id"] == "9999" for row in candidates))
        self.assertFalse(any(row["candidate_source"] == "MASTER_VERSION" for row in candidates))

    def test_more_than_100_results_fetches_only_first_page_and_is_skipped(self):
        seen_pages = []

        def fake(url, headers=None, timeout=45):
            page = int(parse_qs(urlparse(url).query)["page"][0])
            seen_pages.append(page)
            return fake_search_response(2117, 22), {}

        with tempfile.TemporaryDirectory() as folder:
            cache = VERIFY.JsonCache(Path(folder))
            with patch.object(VERIFY, "http_get_json", side_effect=fake):
                total, pages, rows = VERIFY.fetch_discogs_barcode_search_v8(
                    "4893391073526", "token", "test-agent", 0.0, cache, [0.0]
                )

        self.assertEqual((total, pages), (2117, 22))
        self.assertEqual(len(rows), 100)
        self.assertTrue(VERIFY.exceeds_barcode_search_limit_v8(total))
        self.assertEqual(seen_pages, [1])

    def test_direct_candidate_deduplication_does_not_expand_master(self):
        rows = [
            {"id": 10, "type": "release", "title": "First", "master_id": 50},
            {"id": 10, "type": "release", "title": "Duplicate", "master_id": 50},
            {"id": 11, "type": "release", "title": "Second", "master_id": 50},
        ]
        candidates = VERIFY.build_direct_candidates_v8(rows)
        self.assertEqual([row["id"] for row in candidates], ["10", "11"])
        self.assertEqual([row["candidate_source"] for row in candidates], ["BARCODE_SEARCH", "BARCODE_SEARCH"])


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
                    stream,
                    {"mbid": "33333333-3333-4333-8333-333333333333", "status": "DONE_NO_DISCOGS_RESULTS"},
                    2,
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
