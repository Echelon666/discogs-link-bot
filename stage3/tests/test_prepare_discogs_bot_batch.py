from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "prepare_discogs_bot_batch.py"


class PrepareBatchTests(unittest.TestCase):
    def make_report(self, folder: Path, rows: list[dict[str, str]]) -> Path:
        fields = [
            "line_no", "mbid", "barcode", "discogs_url", "discogs_release_id",
            "status", "reason", "normalization_actions", "mb_title", "mb_artist",
            "mb_barcode", "discogs_api_title", "discogs_api_artist",
            "mb_labels", "mb_catalog_numbers", "discogs_api_labels",
            "discogs_api_catalog_numbers", "title_match_type", "artist_match_type",
        ]
        path = folder / "report.tsv"
        with path.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def candidate(self, n: int, *, status: str = "AUTO_CANDIDATE", discogs_id: int | None = None) -> dict[str, str]:
        mbid = f"00000000-0000-4000-8000-{n:012d}"
        did = discogs_id if discogs_id is not None else 900000 + n
        return {
            "line_no": str(n),
            "mbid": mbid,
            "barcode": f"000000000{n:03d}",
            "discogs_url": f"https://www.discogs.com/release/{did}-Artist-Title",
            "discogs_release_id": str(did),
            "status": status,
            "reason": "ARTIST_MATCH;TITLE_MATCH;BARCODE_MATCH;NO_EXISTING_DISCOGS_LINK;NOT_BLACKLISTED;LABEL_CHECK_MATCH;CATALOG_NUMBER_CHECK_MATCH;LABEL_CATALOG_PAIR_CHECK_MATCH",
            "normalization_actions": "",
            "mb_title": f"Title {n}",
            "mb_artist": f"Artist {n}",
            "mb_barcode": f"000000000{n:03d}",
            "discogs_api_title": f"Title {n}",
            "discogs_api_artist": f"Artist {n}",
            "mb_labels": "Example Label",
            "mb_catalog_numbers": f"CAT-{n}",
            "discogs_api_labels": "Example Label",
            "discogs_api_catalog_numbers": f"CAT-{n}",
            "title_match_type": "EXACT",
            "artist_match_type": "EXACT",
        }

    def run_generator(self, report: Path, out_dir: Path, *, target: str = "musicbrainz.org", limit: int = 2) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable, str(SCRIPT), "--input", str(report),
                "--limit", str(limit), "--target-server", target,
                "--out-dir", str(out_dir),
            ],
            text=True, capture_output=True, check=False,
        )

    def test_only_auto_candidates_and_target_server_are_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            rows = [self.candidate(1), self.candidate(2), self.candidate(3, status="MANUAL_TITLE_MISMATCH")]
            report = self.make_report(folder, rows)
            out_dir = folder / "out"
            result = self.run_generator(report, out_dir, target="test.musicbrainz.org")
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads((out_dir / "submission_batch.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["target_server"], "test.musicbrainz.org")
            self.assertEqual(payload["source_engine"], "DiscogsBot Stage 2 v5")
            self.assertEqual(payload["eligible_auto_candidates"], 2)
            self.assertEqual(payload["selected_count"], 2)
            self.assertEqual(len(payload["items"]), 2)
            self.assertTrue(all(item["status"] == "AUTO_CANDIDATE" for item in payload["items"]))
            self.assertEqual(len({item["mbid"] for item in payload["items"]}), 2)
            self.assertEqual(len({item["discogs_release_id"] for item in payload["items"]}), 2)
            self.assertTrue((out_dir / "submission_batch_preview.tsv").exists())

    def test_duplicate_discogs_release_id_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            rows = [self.candidate(1, discogs_id=777), self.candidate(2, discogs_id=777)]
            report = self.make_report(folder, rows)
            result = self.run_generator(report, folder / "out")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Duplicate AUTO_CANDIDATE", result.stderr)

    def test_invalid_limit_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            report = self.make_report(folder, [self.candidate(1)])
            result = self.run_generator(report, folder / "out", limit=151)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("--limit must be 1..150", result.stderr)


if __name__ == "__main__":
    unittest.main()
