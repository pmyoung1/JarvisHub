"""Placeholder for the phase 2 Grok API specialist.

This specialist is registered but disabled. In phase 2 it becomes a
low-priority catch-all: after the rule-based core and media specialists
decline a command, GrokSpecialist would send the text (plus short
conversation context) to the Grok API and return the model's reply
through the same pipeline (wake -> receive -> route -> reply -> display).

Do not call any cloud API in phase 1. Core hub function must work
entirely on the local network with no cloud dependency.
"""

from __future__ import annotations

from typing import Any

from jarvis.specialists.base import Specialist


class GrokSpecialist(Specialist):
    name = "grok"
    priority = 900
    enabled = False  # Phase 2: set True and implement handle() against the Grok API.

    def can_handle(self, text: str, context: dict[str, Any]) -> bool:
        del text, context
        # Phase 2: return True for anything the earlier specialists missed,
        # assuming an API key is configured.
        return False

    def handle(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        # PHASE 2 hook:
        #   1. Read an API key from config (never hard-code it).
        #   2. POST the user text + compact history to the Grok API.
        #   3. Return {"reply": <model text>, "intent": "grok.chat", "data": {...}}.
        # Leave disabled until then so the hub stays local-only.
        del text, context
        return {
            "reply": "The Grok specialist is not enabled in phase 1.",
            "intent": "grok.disabled",
            "data": {"enabled": False},
        }
