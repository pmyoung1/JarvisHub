"""Voice/text commands for TV power and input (root + samsungtvws backends)."""

from __future__ import annotations

import re
from typing import Any

from jarvis.specialists.base import Specialist

_TV_ON = re.compile(
    r"^\s*((turn|power|switch)\s+)?(the\s+)?tv\s+(on|wake)|^\s*(wake|power\s+on)\s+(the\s+)?tv\b",
    re.IGNORECASE,
)
_TV_OFF = re.compile(
    r"^\s*((turn|power|switch)\s+)?(the\s+)?tv\s+(off|standby)|^\s*(power\s+off)\s+(the\s+)?tv\b",
    re.IGNORECASE,
)
_TV_INPUT = re.compile(
    r"^\s*((switch|change|set)\s+)?(the\s+)?(tv\s+)?(input|hdmi|source)\b(.*)$|"
    r"^\s*(hdmi\s*[1-4]|next\s+input|source\s+menu)\s*$",
    re.IGNORECASE,
)


class TvSpecialist(Specialist):
    name = "tv"
    priority = 15  # after core, before media

    def __init__(self, tv: Any | None = None) -> None:
        self.tv = tv

    def can_handle(self, text: str, context: dict[str, Any]) -> bool:
        del context
        return bool(_TV_ON.search(text) or _TV_OFF.search(text) or _TV_INPUT.search(text))

    def handle(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        del context
        tv = self.tv
        if tv is None:
            return {
                "reply": "No TV controller is configured. Set tv.controller to 'root' or 'samsungtvws' in config.json.",
                "intent": "tv.unconfigured",
                "data": {"ok": False},
            }
        try:
            if _TV_ON.search(text):
                return self._power(tv, "on")
            if _TV_OFF.search(text):
                return self._power(tv, "off")
            match = _TV_INPUT.search(text)
            if match:
                return self._input(tv, text, match)
        except Exception as exc:  # noqa: BLE001
            return {
                "reply": f"TV command failed: {exc}",
                "intent": "tv.error",
                "data": {"ok": False, "error": str(exc)},
            }
        return {"reply": "I didn't catch a TV command.", "intent": "tv.unknown", "data": {}}

    def _power(self, tv: Any, action: str) -> dict[str, Any]:
        handler = getattr(tv, "power", None)
        if not callable(handler):
            # Fall back to power_on / power_off methods.
            method = getattr(tv, "power_on" if action == "on" else "power_off", None)
            if not callable(method):
                return {
                    "reply": f"This TV controller ({getattr(tv, 'name', 'unknown')}) cannot power the TV {action}.",
                    "intent": f"tv.power_{action}",
                    "data": {"ok": False, "action": f"power_{action}"},
                }
            result = method()
        else:
            result = handler(action)
        if result.get("ok"):
            if action == "on":
                reply = "Sending Wake-on-LAN to turn the TV on."
            else:
                reply = "Turning the TV off."
        else:
            reply = f"Could not turn the TV {action}: {result.get('error') or 'unknown error'}"
        return {"reply": reply, "intent": f"tv.power_{action}", "data": result}

    def _input(self, tv: Any, text: str, match: re.Match) -> dict[str, Any]:
        handler = getattr(tv, "set_input", None)
        if not callable(handler):
            return {
                "reply": f"This TV controller ({getattr(tv, 'name', 'unknown')}) cannot switch inputs.",
                "intent": "tv.input",
                "data": {"ok": False},
            }
        target = _parse_input(text, match)
        result = handler(target)
        if result.get("ok"):
            reply = f"Switching the TV input ({target})."
        else:
            reply = f"Could not switch the TV input: {result.get('error') or 'unknown error'}"
        return {"reply": reply, "intent": "tv.input", "data": result}


def _parse_input(text: str, match: re.Match) -> str:
    raw = (match.group(0) or text).lower()
    for n in ("1", "2", "3", "4"):
        if re.search(rf"hdmi\s*{n}", raw):
            return f"hdmi{n}"
    if "source" in raw:
        return "source"
    return "next"
