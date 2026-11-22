import importlib.util
from pathlib import Path
import unittest
import tempfile
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "discogs_barcode_builder.py"
SPEC = importlib.util.spec_from_file_location("discogs_barcode_builder_v6", SCRIPT)
BUILDER = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(BUILDER)


class MasterVersionExpansionTests(unittest.TestCase):
    def test_single_search_result_is_not_expanded(self):
        calls = []
        def load_versions(master_id):
            calls.append(master_id)
            return [{"id": 200, "title": "Other pressing"}]
        candidates, master_ids, version_rows = BUILDER.expand_candidates_for_master_results(
            1, [{"id": 100, "title": "Single result", "master_id": 10}], load_versions
        )
        self.assertEqual([row["id"] for row in candidates], ["100"])
        self.assertEqual(master_ids, [])
        self.assertEqual(version_rows, 0)
        self.assertEqual(calls, [])

    def test_multiple_results_expand_all_distinct_master_versions(self):
        calls = []
        def load_versions(master_id):
            calls.append(master_id)
            if master_id == "10":
                return [
                    {"id": 100, "title": "Direct duplicate"},
                    {"id": 101, "title": "Version B", "released": "2001", "country": "UK", "format": ["CD", "Album"]},
                ]
            if master_id == "20":
                return [{"id": 202, "title": "Version C"}]
            raise AssertionError(master_id)
        search_rows = [
            {"id": 100, "title": "Search A", "master_id": 10},
            {"id": 200, "title": "Search B", "master_id": 10},
            {"id": 201, "title": "Search C", "master_id": 20},
            {"id": 300, "title": "No master"},
        ]
        candidates, master_ids, version_rows = BUILDER.expand_candidates_for_master_results(4, search_rows, load_versions)
        self.assertEqual(calls, ["10", "20"])
        self.assertEqual(master_ids, ["10", "20"])
        self.assertEqual(version_rows, 3)
        self.assertEqual([row["id"] for row in candidates], ["100", "200", "201", "300", "101", "202"])
        self.assertEqual(candidates[0]["candidate_source"], "BARCODE_SEARCH")
        self.assertEqual(candidates[4]["candidate_source"], "MASTER_VERSION")
        self.assertEqual(candidates[4]["version_year"], "2001")
        self.assertEqual(candidates[4]["version_country"], "UK")
        self.assertEqual(candidates[4]["version_format"], "CD; Album")

    def test_duplicate_master_ids_are_fetched_once_and_duplicate_release_ids_removed(self):
        calls = []
        def load_versions(master_id):
            calls.append(master_id)
            return [{"id": 999, "title": "Version"}]
        rows = [{"id": 1, "master_id": "55"}, {"id": 2, "master_id": "55"}, {"id": 3, "master_id": "0"}]
        candidates, master_ids, _ = BUILDER.expand_candidates_for_master_results(3, rows, load_versions)
        self.assertEqual(calls, ["55"])
        self.assertEqual(master_ids, ["55"])
        self.assertEqual([row["id"] for row in candidates], ["1", "2", "3", "999"])


class DiscogsPaginationTests(unittest.TestCase):
    def test_barcode_search_fetches_every_page(self):
        seen_pages = []
        def fake_http_get_json(url, headers=None, timeout=45, retries=5):
            page = int(parse_qs(urlparse(url).query)["page"][0])
            seen_pages.append(page)
            if page == 1:
                return {"pagination": {"items": 101, "pages": 2}, "results": [
                    {"id": i, "type": "release", "title": f"Release {i}"} for i in range(1, 101)
                ]}
            return {"pagination": {"items": 101, "pages": 2}, "results": [
                {"id": 101, "type": "release", "title": "Release 101"}
            ]}
        with tempfile.TemporaryDirectory() as folder:
            cache = BUILDER.JsonCache(Path(folder))
            with patch.object(BUILDER, "http_get_json", side_effect=fake_http_get_json):
                total, pages, results = BUILDER.fetch_all_discogs_results(
                    "012345678905", "token", "test-agent", cache, [0.0], 0.0
                )
        self.assertEqual((total, pages), (101, 2))
        self.assertEqual(seen_pages, [1, 2])
        self.assertEqual(len(results), 101)
        self.assertEqual(results[-1]["id"], 101)

    def test_master_versions_fetches_every_view_all_page(self):
        seen_pages = []
        def fake_http_get_json(url, headers=None, timeout=45, retries=5):
            page = int(parse_qs(urlparse(url).query)["page"][0])
            seen_pages.append(page)
            if page == 1:
                return {"pagination": {"items": 101, "pages": 2}, "versions": [
                    {"id": i, "title": f"Version {i}"} for i in range(1, 101)
                ]}
            return {"pagination": {"items": 101, "pages": 2}, "versions": [
                {"id": 101, "title": "Version 101"}
            ]}
        with tempfile.TemporaryDirectory() as folder:
            cache = BUILDER.JsonCache(Path(folder))
            with patch.object(BUILDER, "http_get_json", side_effect=fake_http_get_json):
                total, pages, versions = BUILDER.fetch_all_master_versions(
                    "42473", "token", "test-agent", cache, [0.0], 0.0
                )
        self.assertEqual((total, pages), (101, 2))
        self.assertEqual(seen_pages, [1, 2])
        self.assertEqual(len(versions), 101)
        self.assertEqual(versions[-1]["id"], 101)


class ProgressRecoveryTests(unittest.TestCase):
    def test_truncated_final_jsonl_record_is_removed_for_future_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "progress.jsonl"
            path.write_text(
                '{"mbid":"11111111-1111-4111-8111-111111111111","status":"SEARCHED_SINGLE"}\n'
                '{"mbid":"22222222-2222-4222-8222-',
                encoding="utf-8",
            )
            records = BUILDER.load_progress(path)
            self.assertEqual(len(records), 1)
            self.assertNotIn("22222222-2222-4222-8222-", path.read_text(encoding="utf-8"))
            BUILDER.append_progress(path, {"mbid": "33333333-3333-4333-8333-333333333333", "status": "SEARCHED_SINGLE"})
            self.assertEqual(len(BUILDER.load_progress(path)), 2)

    def test_invalid_middle_jsonl_record_still_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "progress.jsonl"
            path.write_text('{"mbid":"11111111-1111-4111-8111-111111111111"}\n{bad}\n{}\n', encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid JSON at line 2"):
                BUILDER.load_progress(path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
