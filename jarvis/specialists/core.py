"""Rule-based core specialist: greetings, time/date, help, system status."""

from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Any

from jarvis.specialists.base import Specialist

_GREETINGS = re.compile(
    r"^\s*(hi|hello|hey|yo|howdy|good\s+(morning|afternoon|evening)|sup|"
    r"what'?s\s+up|who\s+are\s+you|your\s+name)\b",
    re.IGNORECASE,
)
_TIME = re.compile(
    r"\b(what\s+time|current\s+time|tell\s+me\s+the\s+time|time\s+is\s+it)\b",
    re.IGNORECASE,
)
_DATE = re.compile(
    r"\b(what('?s|\s+is)\s+(the\s+)?date|what\s+day|today'?s\s+date|"
    r"current\s+date)\b",
    re.IGNORECASE,
)
_HELP = re.compile(
    r"^\s*(help|what\s+can\s+you\s+do|commands?|how\s+do\s+(you|i)\s+work|"
    r"what\s+do\s+you\s+do)\b",
    re.IGNORECASE,
)
_STATUS = re.compile(
    r"^\s*(status|health|system\s+status|how\s+are\s+you|are\s+you\s+(up|ok|there)|"
    r"hub\s+status)\b",
    re.IGNORECASE,
)


class CoreSpecialist(Specialist):
    name = "core"
    priority = 10

    def can_handle(self, text: str, context: dict[str, Any]) -> bool:
        del context
        return bool(
            _GREETINGS.search(text)
            or _TIME.search(text)
            or _DATE.search(text)
            or _HELP.search(text)
            or _STATUS.search(text)
        )

    def handle(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        if _HELP.search(text):
            return self._help(context)
        if _STATUS.search(text):
            return self._status(context)
        if _TIME.search(text):
            return self._time()
        if _DATE.search(text):
            return self._date()
        if _GREETINGS.search(text):
            return self._greet(context)
        return {"reply": "I'm here.", "intent": "core.ack", "data": {}}

    def _greet(self, context: dict[str, Any]) -> dict[str, Any]:
        name = (context.get("hub_name") or "Jarvis").strip()
        hour = datetime.now().hour
        if hour < 12:
            part = "Good morning"
        elif hour < 17:
            part = "Good afternoon"
        else:
            part = "Good evening"
        reply = (
            f"{part}. I'm {name}, the household hub. "
            "Ask me to find a movie, list the library, tell the time, "
            "or say help for more."
        )
        return {"reply": reply, "intent": "core.greet", "data": {"hub_name": name}}

    def _time(self) -> dict[str, Any]:
        now = datetime.now()
        stamp = now.strftime("%I:%M %p").lstrip("0")
        return {
            "reply": f"It's {stamp}.",
            "intent": "core.time",
            "data": {"iso": now.isoformat(timespec="seconds")},
        }

    def _date(self) -> dict[str, Any]:
        now = datetime.now()
        stamp = now.strftime("%A, %B %d, %Y")
        return {
            "reply": f"Today is {stamp}.",
            "intent": "core.date",
            "data": {"iso": now.date().isoformat()},
        }

    def _help(self, context: dict[str, Any]) -> dict[str, Any]:
        name = (context.get("hub_name") or "Jarvis").strip()
        wake = (context.get("wake_word") or "jarvis").strip()
        reply = (
            f"I'm {name}. You can type or speak commands"
            f"{' (optionally starting with ' + wake + ',)' if wake else ''}. "
            "Try: hello; what time is it; what's the date; status; "
            "list movies; find inception; play inception on the tv; "
            "tv on; tv off; switch input / next input / source menu."
        )
        return {
            "reply": reply,
            "intent": "core.help",
            "data": {
                "commands": [
                    "hello",
                    "what time is it",
                    "what's the date",
                    "status",
                    "help",
                    "list movies",
                    "find <title>",
                    "play <title> on the tv",
                    "tv on",
                    "tv off",
                    "switch input",
                    "next input",
                    "source menu",
                ]
            },
        }

    def _status(self, context: dict[str, Any]) -> dict[str, Any]:
        started = context.get("started_at")
        uptime = None
        if isinstance(started, (int, float)):
            uptime = max(0, int(time.time() - started))
        media_count = context.get("media_count")
        scanning = bool(context.get("scanning"))
        clients = context.get("sse_clients")
        bits = ["I'm up and running."]
        if uptime is not None:
            bits.append(f"Uptime is {_format_uptime(uptime)}.")
        if media_count is not None:
            bits.append(f"The media index holds {media_count} title{'s' if media_count != 1 else ''}.")
        if scanning:
            bits.append("A library scan is in progress.")
        if clients is not None:
            bits.append(f"{clients} live client{'s' if clients != 1 else ''} connected.")
        return {
            "reply": " ".join(bits),
            "intent": "core.status",
            "data": {
                "uptime_seconds": uptime,
                "media_count": media_count,
                "scanning": scanning,
                "sse_clients": clients,
            },
        }


def _format_uptime(seconds: int) -> str:
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"
