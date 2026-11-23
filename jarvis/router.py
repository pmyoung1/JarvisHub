"""Route a normalized command to the first specialist that can handle it."""

from __future__ import annotations

from typing import Any, Iterable

from jarvis.specialists.base import Specialist
from jarvis.specialists.core import CoreSpecialist
from jarvis.specialists.grok import GrokSpecialist
from jarvis.specialists.media import MediaSpecialist
from jarvis.specialists.tv import TvSpecialist


class Router:
    def __init__(self, specialists: Iterable[Specialist] | None = None) -> None:
        self._specialists: list[Specialist] = list(specialists or [])
        self._specialists.sort(key=lambda s: s.priority)

    def register(self, specialist: Specialist) -> None:
        self._specialists.append(specialist)
        self._specialists.sort(key=lambda s: s.priority)

    @property
    def specialists(self) -> list[Specialist]:
        return list(self._specialists)

    def route(self, text: str, context: dict[str, Any]) -> tuple[Specialist | None, dict[str, Any]]:
        """Return (specialist, handle-result) or (None, fallback dict)."""
        for spec in self._specialists:
            if not getattr(spec, "enabled", True):
                continue
            try:
                if not spec.can_handle(text, context):
                    continue
            except Exception as exc:  # noqa: BLE001 — isolate plug-in failures
                return spec, {
                    "reply": f"The {spec.name} specialist failed while deciding: {exc}",
                    "intent": "error",
                    "data": {"specialist": spec.name, "error": str(exc)},
                }
            try:
                result = spec.handle(text, context)
            except Exception as exc:  # noqa: BLE001
                return spec, {
                    "reply": f"The {spec.name} specialist failed: {exc}",
                    "intent": "error",
                    "data": {"specialist": spec.name, "error": str(exc)},
                }
            if not isinstance(result, dict):
                result = {"reply": str(result), "intent": f"{spec.name}.reply", "data": {}}
            result.setdefault("intent", f"{spec.name}.reply")
            result.setdefault("data", {})
            result.setdefault("reply", "")
            return spec, result
        return None, {
            "reply": (
                "I don't have a specialist for that yet. "
                "Try hello, help, status, list movies, find <title>, "
                "play <title> on the tv, tv on, tv off, or switch input."
            ),
            "intent": "unknown",
            "data": {"text": text},
        }


def build_default_router(
    media_index: Any | None = None,
    *,
    grok: Any | None = None,
    tv: Any | None = None,
    stream_url_factory: Any | None = None,
    grok_kwargs: dict[str, Any] | None = None,
) -> Router:
    """Default specialist set. Grok stays disabled when no API key is set."""
    media = MediaSpecialist(index=media_index, tv=tv, stream_url_factory=stream_url_factory)
    grok_specialist = grok if grok is not None else GrokSpecialist.from_config(
        (grok_kwargs or {}).get("config") if grok_kwargs and "config" in grok_kwargs else None,
        media_index=media_index,
        **{k: v for k, v in (grok_kwargs or {}).items() if k != "config"},
    )
    return Router(
        [
            CoreSpecialist(),
            TvSpecialist(tv=tv),
            media,
            grok_specialist,
        ]
    )
