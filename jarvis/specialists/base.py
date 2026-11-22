"""Specialist plug-in interface.

Future Grok Bot specialists (home automation, calendar, a richer
media controller, the live Grok API) should subclass ``Specialist``,
implement ``can_handle`` / ``handle``, and register with the Router.
Do not wire Home Assistant, MQTT, or device control in phase 1.
"""

from __future__ import annotations

from typing import Any


class Specialist:
    """One routeable skill for the command pipeline."""

    name: str = "base"
    # Lower numbers run first. Keep catch-alls (e.g. Grok) high.
    priority: int = 100
    enabled: bool = True

    def can_handle(self, text: str, context: dict[str, Any]) -> bool:
        raise NotImplementedError

    def handle(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        """Return a dict with at least ``reply`` and ``intent``.

        Optional ``data`` carries structured payload for the dashboard
        (media hits, status blocks, a future play action, ...).
        """
        raise NotImplementedError

    def describe(self) -> str:
        return self.name
