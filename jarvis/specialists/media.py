"""Media specialist: find / list / resolve a title for a future TV play action."""

from __future__ import annotations

import re
from typing import Any, Callable

from jarvis.specialists.base import Specialist

_LIST = re.compile(
    r"^\s*(list\s+(movies?|films?|titles?|library|media)|"
    r"(what('?s|\s+is)|show)\s+(in\s+)?(the\s+)?(movie\s+)?library|"
    r"how\s+many\s+(movies?|films?|titles?)|"
    r"what\s+movies\s+(do\s+you\s+have|are\s+there))\b",
    re.IGNORECASE,
)
_FIND = re.compile(
    r"^\s*(find|search(\s+for)?|look\s+up|locate|do\s+you\s+have)\s+(.+)$",
    re.IGNORECASE,
)
_PLAY = re.compile(
    r"^\s*(play|watch|put\s+on|start(\s+playing)?)\s+(.+)$",
    re.IGNORECASE,
)
_ON_TV = re.compile(
    r"\s+on\s+(the\s+)?(tv|television|samsung|screen)\s*$",
    re.IGNORECASE,
)


class MediaSpecialist(Specialist):
    name = "media"
    priority = 20

    def __init__(
        self,
        index: Any | None = None,
        index_factory: Callable[[], Any] | None = None,
        tv: Any | None = None,
        stream_url_factory: Callable[..., str | None] | None = None,
    ) -> None:
        self._index = index
        self._index_factory = index_factory
        self.tv = tv
        self.stream_url_factory = stream_url_factory

    @property
    def index(self) -> Any:
        if self._index is None and self._index_factory is not None:
            self._index = self._index_factory()
        return self._index

    def can_handle(self, text: str, context: dict[str, Any]) -> bool:
        del context
        return bool(_LIST.search(text) or _FIND.search(text) or _PLAY.search(text))

    def handle(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        context = context or {}
        if _LIST.search(text):
            return self._list()
        play = _PLAY.search(text)
        if play:
            return self._play(play.group(3).strip(), context)
        find = _FIND.search(text)
        if find:
            return self._find(find.group(3).strip())
        return {"reply": "I didn't catch a media command.", "intent": "media.unknown", "data": {}}

    def _list(self) -> dict[str, Any]:
        idx = self.index
        if idx is None:
            return {
                "reply": "The media index is not available yet.",
                "intent": "media.list",
                "data": {"items": [], "total": 0},
            }
        total = idx.count()
        items = idx.search(query="", limit=12, offset=0)
        if total == 0:
            reply = "The library is empty. Add folders to media_paths in config.json and rescan."
        else:
            preview = ", ".join(_label(it) for it in items[:8])
            extra = f" Showing the first {len(items)}." if total > len(items) else ""
            reply = f"I have {total} title{'s' if total != 1 else ''} indexed. {preview}.{extra}"
        return {
            "reply": reply,
            "intent": "media.list",
            "data": {"items": items, "total": total},
        }

    def _find(self, query: str) -> dict[str, Any]:
        query = _clean_query(query)
        if not query:
            return {"reply": "Find what?", "intent": "media.find", "data": {"items": [], "query": ""}}
        idx = self.index
        if idx is None:
            return {
                "reply": "The media index is not available yet.",
                "intent": "media.find",
                "data": {"items": [], "query": query},
            }
        items = idx.search(query=query, limit=10, offset=0)
        if not items:
            # Fall back to fuzzy title match (better for spoken titles).
            fuzzy = idx.fuzzy_match(query, limit=5)
            items = fuzzy
        if not items:
            return {
                "reply": f'I couldn\'t find anything matching "{query}".',
                "intent": "media.find",
                "data": {"items": [], "query": query},
            }
        if len(items) == 1:
            reply = f"I found {_label(items[0])}."
        else:
            labels = ", ".join(_label(it) for it in items[:6])
            reply = f"I found {len(items)} matches for \"{query}\": {labels}."
        return {
            "reply": reply,
            "intent": "media.find",
            "data": {"items": items, "query": query},
        }

    def _play(self, rest: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        context = context or {}
        on_tv = bool(_ON_TV.search(rest))
        title = _ON_TV.sub("", rest).strip()
        title = _clean_query(title)
        # Phase 1 only has a TV playback stub; "on the tv" is optional wording.
        target = "tv"
        if not title:
            return {
                "reply": "Play which title?",
                "intent": "media.play",
                "data": {"action": "would_play", "target": target, "on_tv": on_tv, "media": None},
            }
        idx = self.index
        if idx is None:
            return {
                "reply": "The media index is not available yet, so I can't resolve that title.",
                "intent": "media.play",
                "data": {"action": "would_play", "target": target, "on_tv": on_tv, "media": None, "query": title},
            }
        match = idx.resolve_title(title)
        if match is None:
            return {
                "reply": f'I couldn\'t find "{title}" in the library, so I won\'t try to play it.',
                "intent": "media.play",
                "data": {"action": "would_play", "target": target, "on_tv": on_tv, "media": None, "query": title},
            }

        stream_url = None
        if self.stream_url_factory is not None:
            try:
                stream_url = self.stream_url_factory(match, context)
            except Exception as exc:  # noqa: BLE001
                stream_url = None
                context = dict(context)
                context["stream_url_error"] = str(exc)

        tv = self.tv
        if tv is None:
            result = {
                "ok": True,
                "action": "would_play",
                "url": stream_url,
                "path": match.get("path"),
                "title": match.get("title"),
                "message": "No TV controller is configured.",
            }
        else:
            try:
                prefers_path = bool(getattr(tv, "prefers_path", False))
                path = str(match.get("path") or "")
                if prefers_path and hasattr(tv, "play_path") and path:
                    result = tv.play_path(path, title=_label(match))
                elif stream_url and hasattr(tv, "play_url"):
                    result = tv.play_url(stream_url, title=_label(match))
                elif hasattr(tv, "play_path") and path:
                    result = tv.play_path(path, title=_label(match))
                else:
                    result = {"ok": False, "action": "would_play", "error": "TV controller is missing play methods"}
            except Exception as exc:  # noqa: BLE001 — never crash the request thread
                result = {
                    "ok": False,
                    "action": "would_play",
                    "controller": getattr(tv, "name", None),
                    "error": f"TV play failed: {exc}",
                }

        action = str((result or {}).get("action") or "would_play")
        err = (result or {}).get("error")
        if action == "playing" and (result or {}).get("ok", True):
            reply = f"Playing {_label(match)} on the TV."
            if (result or {}).get("remote"):
                reply = f"{reply} File on TV: {result['remote']}."
        elif err:
            reply = f'I found {_label(match)} but could not start it on the TV: {err}'
        else:
            extra = ""
            if stream_url:
                extra = " A stream URL is ready for the TV."
            msg = (result or {}).get("message") or ""
            reply = (
                f"I would play {_label(match)} on the TV "
                f"(file: {match.get('path')}).{extra}"
            )
            if msg and "No TV controller" not in str(msg):
                reply = f"{reply} {msg}"

        data = {
            "action": action,
            "target": target,
            "on_tv": on_tv,
            "media": match,
            "query": title,
            "stream_url": stream_url,
            "controller": (result or {}).get("controller"),
            "ok": (result or {}).get("ok"),
        }
        if result:
            for key in ("error", "message", "remote", "play_command", "todo"):
                if result.get(key):
                    data[key] = result[key]
        return {"reply": reply, "intent": "media.play", "data": data}


def _label(item: dict[str, Any]) -> str:
    title = item.get("title") or "Untitled"
    year = item.get("year")
    return f"{title} ({year})" if year else str(title)


def _clean_query(query: str) -> str:
    query = query.strip().strip(" .?!")
    query = re.sub(r"^(the\s+movie\s+|a\s+movie\s+|the\s+film\s+)", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+please\s*$", "", query, flags=re.IGNORECASE)
    return query.strip()
