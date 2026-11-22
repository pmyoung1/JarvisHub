"""Media indexer: nfo, posters, incremental mtime scan, fuzzy resolve."""

from __future__ import annotations

import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.media.index import MediaIndex
from jarvis.media.nfo import find_poster, read_nfo

NFO = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<movie>
  <title>Inception</title>
  <year>2010</year>
  <plot>A thief who steals corporate secrets through dream-sharing.</plot>
  <genre>Action</genre>
  <genre>Sci-Fi</genre>
</movie>
"""

# 1x1 PNG (dark pixel).
TINY_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d4944415478da63aaf75700000280015dd4bbfb0000000049454e44"
    "ae426082"
)


def _touch_video(path: Path, size: int = 64) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"0" * size)


class IndexingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lib = self.root / "library"
        self.lib.mkdir()

        inception = self.lib / "Inception (2010)"
        _touch_video(inception / "Inception.2010.1080p.BluRay.x264.mkv")
        (inception / "movie.nfo").write_text(NFO, encoding="utf-8")
        (inception / "poster.jpg").write_bytes(TINY_PNG)

        matrix = self.lib / "The.Matrix.1999.1080p.BluRay.x264"
        _touch_video(matrix / "The.Matrix.1999.1080p.BluRay.x264.mkv")
        (matrix / "folder.jpg").write_bytes(TINY_PNG)

        _touch_video(self.lib / "Some Movie (2020).mp4")

        self.db = self.root / "media.sqlite3"
        self.index = MediaIndex(self.db, [self.lib])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_scan_parses_nfo_and_filenames(self) -> None:
        result = self.index.scan()
        self.assertTrue(result["ok"])
        self.assertEqual(result["added"], 3)
        self.assertEqual(self.index.count(), 3)

        inception = self.index.resolve_title("inception")
        self.assertIsNotNone(inception)
        assert inception is not None
        self.assertEqual(inception["title"], "Inception")
        self.assertEqual(inception["year"], 2010)
        self.assertIn("dream-sharing", inception["plot"] or "")
        self.assertEqual(inception["genres"], ["Action", "Sci-Fi"])
        self.assertTrue(inception["has_poster"])
        poster = self.index.poster_path_for(inception["id"])
        self.assertIsNotNone(poster)
        assert poster is not None
        self.assertTrue(poster.is_file())

        matrix = self.index.resolve_title("the matrix")
        self.assertIsNotNone(matrix)
        assert matrix is not None
        self.assertEqual(matrix["title"], "The Matrix")
        self.assertEqual(matrix["year"], 1999)
        self.assertTrue(matrix["has_poster"])

        indie = self.index.resolve_title("some movie")
        self.assertIsNotNone(indie)
        assert indie is not None
        self.assertEqual(indie["title"], "Some Movie")
        self.assertEqual(indie["year"], 2020)

    def test_search_and_fuzzy(self) -> None:
        self.index.scan()
        hits = self.index.search("incep")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["title"], "Inception")
        fuzzy = self.index.fuzzy_match("incepton")  # typo
        self.assertGreaterEqual(len(fuzzy), 1)
        self.assertEqual(fuzzy[0]["title"], "Inception")

    def test_incremental_skips_unchanged(self) -> None:
        first = self.index.scan()
        self.assertEqual(first["added"], 3)
        second = self.index.scan()
        self.assertEqual(second["added"], 0)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(second["unchanged"], 3)
        self.assertEqual(second["removed"], 0)

    def test_incremental_picks_up_new_and_gone_files(self) -> None:
        self.index.scan()
        new = self.lib / "Arrival (2016).mkv"
        _touch_video(new)
        again = self.index.scan()
        self.assertEqual(again["added"], 1)
        self.assertEqual(self.index.count(), 4)
        new.unlink()
        pruned = self.index.scan()
        self.assertEqual(pruned["removed"], 1)
        self.assertEqual(self.index.count(), 3)

    def test_sidecar_mtime_triggers_update(self) -> None:
        self.index.scan()
        nfo = self.lib / "Inception (2010)" / "movie.nfo"
        time.sleep(0.05)
        nfo.write_text(
            NFO.replace("dream-sharing", "dream-sharing technology"),
            encoding="utf-8",
        )
        result = self.index.scan()
        self.assertEqual(result["updated"], 1)
        item = self.index.resolve_title("inception")
        assert item is not None
        self.assertIn("technology", item["plot"] or "")

    def test_read_nfo_and_find_poster(self) -> None:
        nfo_path = self.lib / "Inception (2010)" / "movie.nfo"
        meta = read_nfo(nfo_path)
        self.assertEqual(meta["title"], "Inception")
        self.assertEqual(meta["year"], 2010)
        video = self.lib / "Inception (2010)" / "Inception.2010.1080p.BluRay.x264.mkv"
        poster = find_poster(video)
        self.assertIsNotNone(poster)
        assert poster is not None
        self.assertEqual(poster.name, "poster.jpg")

    def test_poster_restricted_to_indexed_ids(self) -> None:
        self.index.scan()
        self.assertIsNone(self.index.poster_path_for(99999))
        self.assertIsNone(self.index.get(99999))


if __name__ == "__main__":
    unittest.main()
