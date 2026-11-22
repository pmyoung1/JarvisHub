"""Route a normalized command to the first specialist that can handle it."""

from __future__ import annotations

from typing import Any, Iterable

from jarvis.specialists.base import Specialist
from jarvis.specialists.core import CoreSpecialist
from jarvis.specialists.grok import GrokSpecialist
from jarvis.specialists.media import MediaSpecialist


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
                "or play <title> on the tv."
            ),
            "intent": "unknown",
            "data": {"text": text},
        }


def build_default_router(media_index: Any | None = None) -> Router:
    """Phase 1 default set. Future specialists register here.

    Planned (not implemented):
      - Home automation specialist  — do NOT add Home Assistant / MQTT in phase 1
      - Calendar specialist
      - GrokSpecialist (present, disabled)
      - Richer media / TV-root playback specialist
    """
    return Router(
        [
            CoreSpecialist(),
            MediaSpecialist(index=media_index),
            GrokSpecialist(),  # disabled placeholder
        ]
    )
