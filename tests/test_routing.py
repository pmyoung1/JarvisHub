"""Command pipeline and specialist routing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.pipeline import CommandRequest, Pipeline
from jarvis.router import Router, build_default_router
from jarvis.specialists.core import CoreSpecialist
from jarvis.specialists.grok import GrokSpecialist
from jarvis.specialists.media import MediaSpecialist


class FakeIndex:
    def __init__(self) -> None:
        self._items = [
            {
                "id": 1,
                "title": "Inception",
                "year": 2010,
                "path": r"D:\Movies\Inception (2010)\Inception.2010.mkv",
                "plot": "A thief enters dreams.",
                "genres": ["Action", "Sci-Fi"],
                "has_poster": True,
            },
            {
                "id": 2,
                "title": "The Matrix",
                "year": 1999,
                "path": r"D:\Movies\The.Matrix.1999.mkv",
                "plot": None,
                "genres": [],
                "has_poster": False,
            },
        ]

    def count(self) -> int:
        return len(self._items)

    def search(self, query: str = "", limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        q = (query or "").lower()
        items = [it for it in self._items if not q or q in it["title"].lower()]
        return items[offset : offset + limit]

    def fuzzy_match(self, query: str, limit: int = 5, cutoff: float = 0.52) -> list[dict[str, Any]]:
        del cutoff
        q = query.lower()
        hits = [it for it in self._items if q in it["title"].lower() or it["title"].lower() in q]
        return hits[:limit]

    def resolve_title(self, query: str) -> dict[str, Any] | None:
        hits = self.fuzzy_match(query)
        return hits[0] if hits else None


def _pipeline() -> tuple[Pipeline, list[dict[str, Any]]]:
    displayed: list[dict[str, Any]] = []
    router = Router(
        [
            CoreSpecialist(),
            MediaSpecialist(index=FakeIndex()),
            GrokSpecialist(),
        ]
    )
    pipe = Pipeline(
        router,
        wake_word="jarvis",
        display=displayed.append,
        context_factory=lambda: {
            "hub_name": "Jarvis",
            "wake_word": "jarvis",
            "started_at": 0,
            "media_count": 2,
            "scanning": False,
            "sse_clients": 1,
        },
    )
    return pipe, displayed


class RoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.pipe, self.displayed = _pipeline()

    def _run(self, text: str) -> dict[str, Any]:
        return self.pipe.process(CommandRequest(text=text, source="test", client_id="t1")).to_dict()

    def test_greeting(self) -> None:
        out = self._run("hello")
        self.assertEqual(out["intent"], "core.greet")
        self.assertIn("Jarvis", out["reply"])
        self.assertEqual(out["stages"]["route"]["specialist"], "core")

    def test_wake_word_stripped(self) -> None:
        out = self._run("Jarvis, help")
        self.assertEqual(out["intent"], "core.help")
        self.assertEqual(out["stages"]["wake"]["text"], "help")

    def test_bare_wake_word_greets(self) -> None:
        out = self._run("jarvis")
        self.assertEqual(out["intent"], "core.greet")

    def test_time(self) -> None:
        out = self._run("what time is it")
        self.assertEqual(out["intent"], "core.time")
        self.assertTrue(out["reply"])

    def test_date(self) -> None:
        out = self._run("what's the date")
        self.assertEqual(out["intent"], "core.date")

    def test_status(self) -> None:
        out = self._run("status")
        self.assertEqual(out["intent"], "core.status")
        self.assertIn("2", out["reply"])

    def test_list_movies(self) -> None:
        out = self._run("list movies")
        self.assertEqual(out["intent"], "media.list")
        self.assertEqual(out["data"]["total"], 2)
        self.assertEqual(out["stages"]["route"]["specialist"], "media")

    def test_find(self) -> None:
        out = self._run("find inception")
        self.assertEqual(out["intent"], "media.find")
        self.assertEqual(out["data"]["items"][0]["title"], "Inception")

    def test_play_inception_on_the_tv(self) -> None:
        out = self._run("play inception on the tv")
        self.assertEqual(out["intent"], "media.play")
        self.assertEqual(out["data"]["action"], "would_play")
        self.assertEqual(out["data"]["target"], "tv")
        self.assertEqual(out["data"]["media"]["title"], "Inception")
        self.assertIn("would play", out["reply"].lower())
        self.assertIn("Inception", out["reply"])

    def test_play_unknown(self) -> None:
        out = self._run("play missing movie on the tv")
        self.assertEqual(out["intent"], "media.play")
        self.assertIsNone(out["data"]["media"])
        self.assertIn("couldn't find", out["reply"].lower())

    def test_unknown_falls_back(self) -> None:
        out = self._run("dim the kitchen lights")
        self.assertEqual(out["intent"], "unknown")
        self.assertIsNone(out["stages"]["route"]["specialist"])

    def test_grok_is_disabled(self) -> None:
        router = build_default_router(media_index=FakeIndex())
        names = [s.name for s in router.specialists]
        self.assertIn("grok", names)
        grok = next(s for s in router.specialists if s.name == "grok")
        self.assertFalse(grok.enabled)
        self.assertFalse(grok.can_handle("tell me a joke", {}))

    def test_display_stage_broadcasts(self) -> None:
        out = self._run("hello")
        self.assertTrue(out["stages"]["display"]["broadcast"])
        self.assertEqual(len(self.displayed), 1)
        self.assertEqual(self.displayed[0]["intent"], "core.greet")

    def test_empty_command(self) -> None:
        out = self._run("   ")
        self.assertEqual(out["intent"], "empty")


if __name__ == "__main__":
    unittest.main()
