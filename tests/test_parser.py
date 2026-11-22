"""Filename / folder title+year parsing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.media.parser import parse_media_name, title_from_path


class ParseMediaNameTests(unittest.TestCase):
    def test_dotted_scene_name(self) -> None:
        title, year = parse_media_name("Movie.Name.2010.1080p.BluRay.x264")
        self.assertEqual(title, "Movie Name")
        self.assertEqual(year, 2010)

    def test_dotted_with_extension(self) -> None:
        title, year = parse_media_name("The.Matrix.1999.1080p.BluRay.x264.mkv")
        self.assertEqual(title, "The Matrix")
        self.assertEqual(year, 1999)

    def test_paren_year(self) -> None:
        title, year = parse_media_name("Movie Name (2010)")
        self.assertEqual(title, "Movie Name")
        self.assertEqual(year, 2010)

    def test_paren_year_with_extension(self) -> None:
        title, year = parse_media_name("Inception (2010).mkv")
        self.assertEqual(title, "Inception")
        self.assertEqual(year, 2010)

    def test_bracket_year(self) -> None:
        title, year = parse_media_name("Interstellar [2014] 1080p")
        self.assertEqual(title, "Interstellar")
        self.assertEqual(year, 2014)

    def test_year_at_start_of_title(self) -> None:
        title, year = parse_media_name("2001.A.Space.Odyssey.1968.1080p.BluRay")
        self.assertEqual(title, "2001 A Space Odyssey")
        self.assertEqual(year, 1968)

    def test_underscores(self) -> None:
        title, year = parse_media_name("Spirited_Away_2001.mp4")
        self.assertEqual(title, "Spirited Away")
        self.assertEqual(year, 2001)

    def test_no_year(self) -> None:
        title, year = parse_media_name("Some Indie Feature.mkv")
        self.assertEqual(title, "Some Indie Feature")
        self.assertIsNone(year)

    def test_webrip_tags_stripped(self) -> None:
        title, year = parse_media_name(
            "Dune.Part.Two.2024.2160p.WEB-DL.DDP5.1.Atmos.H.265-GROUP.mkv"
        )
        self.assertEqual(title, "Dune Part Two")
        self.assertEqual(year, 2024)

    def test_title_from_folder_preferred(self) -> None:
        path = Path("/movies/Inception (2010)/Inception.2010.1080p.BluRay.x264.mkv")
        title, year = title_from_path(path)
        self.assertEqual(title, "Inception")
        self.assertEqual(year, 2010)

    def test_generic_parent_not_preferred(self) -> None:
        path = Path("/Movies/The.Dark.Knight.2008.1080p.mkv")
        title, year = title_from_path(path)
        self.assertEqual(title, "The Dark Knight")
        self.assertEqual(year, 2008)


if __name__ == "__main__":
    unittest.main()
