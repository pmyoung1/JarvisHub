"""Command intake pipeline: wake -> receive -> route -> reply -> display."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from jarvis.router import Router


@dataclass
class CommandRequest:
    text: str
    source: str = "unknown"
    client_id: str = ""


@dataclass
class CommandResponse:
    reply: str
    intent: str
    data: dict[str, Any] = field(default_factory=dict)
    stages: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "intent": self.intent,
            "data": self.data,
            "stages": self.stages,
        }


class Pipeline:
    """Explicit five-stage plumbing for every inbound command."""

    def __init__(
        self,
        router: Router,
        *,
        wake_word: str = "jarvis",
        display: Callable[[dict[str, Any]], None] | None = None,
        context_factory: Callable[[], dict[str, Any]] | None = None,
    ) -> None:
        self.router = router
        self.wake_word = (wake_word or "").strip().lower()
        self._display = display
        self._context_factory = context_factory or (lambda: {})

    def process(self, request: CommandRequest) -> CommandResponse:
        # 1. RECEIVE — accept and normalize the inbound payload.
        received_text = (request.text or "").strip()
        source = (request.source or "unknown").strip() or "unknown"
        client_id = (request.client_id or "").strip()
        receive_stage = {
            "source": source,
            "client_id": client_id,
            "raw_text": received_text,
        }

        # 2. WAKE — optional "jarvis, ..." prefix; always accept typed input.
        awake, text = self._wake(received_text)
        wake_stage = {"awake": awake, "text": text, "wake_word": self.wake_word}

        context = self._context_factory()
        context.update(
            {
                "source": source,
                "client_id": client_id,
                "awake": awake,
                "raw_text": received_text,
            }
        )

        if not text:
            result = {
                "reply": "I didn't catch a command. Try saying help.",
                "intent": "empty",
                "data": {},
            }
            specialist_name = None
        else:
            # 3. ROUTE — first enabled specialist that can_handle() wins.
            specialist, result = self.router.route(text, context)
            specialist_name = specialist.name if specialist else None

        # 4. REPLY — specialist (or fallback) already produced the body.
        reply = str(result.get("reply") or "")
        intent = str(result.get("intent") or "unknown")
        data = result.get("data") if isinstance(result.get("data"), dict) else {}
        route_stage = {"specialist": specialist_name, "intent": intent}
        reply_stage = {"intent": intent, "has_data": bool(data)}

        payload = {
            "type": "reply",
            "reply": reply,
            "intent": intent,
            "data": data,
            "source": source,
            "client_id": client_id,
            "text": text,
            "specialist": specialist_name,
        }

        # 5. DISPLAY — fan out to every connected client (phone + TV) via SSE.
        broadcast = False
        if self._display is not None:
            try:
                self._display(payload)
                broadcast = True
            except Exception:  # noqa: BLE001
                broadcast = False
        display_stage = {"broadcast": broadcast}

        return CommandResponse(
            reply=reply,
            intent=intent,
            data=data,
            stages={
                "wake": wake_stage,
                "receive": receive_stage,
                "route": route_stage,
                "reply": reply_stage,
                "display": display_stage,
            },
        )

    def _wake(self, text: str) -> tuple[bool, str]:
        """Strip a leading wake-word prefix if present.

        Typed and spoken commands are accepted with or without the prefix.
        A bare wake word ("jarvis" / "jarvis,") is treated as a greeting cue
        and rewritten to "hello" so the core specialist can answer.
        """
        stripped = text.strip()
        if not self.wake_word:
            return True, _normalize(stripped)
        pattern = re.compile(
            rf"^\s*{re.escape(self.wake_word)}\s*([,.:;]+)?\s*",
            re.IGNORECASE,
        )
        match = pattern.match(stripped)
        if not match:
            return True, _normalize(stripped)
        rest = stripped[match.end() :].strip()
        if not rest:
            return True, "hello"
        return True, _normalize(rest)


def _normalize(text: str) -> str:
    text = text.strip()
    text = re.sub(r"\s+", " ", text)
    return text


def utc_now() -> float:
    return time.time()
