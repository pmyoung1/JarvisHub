"""Pluggable command specialists.

Phase 1 ships a rule-based core specialist and a media specialist.
A disabled GrokSpecialist placeholder shows where the Grok API
will plug in during phase 2. Additional specialists (home automation,
calendar, etc.) should implement the Specialist interface and be
registered in ``build_default_router``.
"""

from jarvis.specialists.base import Specialist
from jarvis.specialists.core import CoreSpecialist
from jarvis.specialists.grok import GrokSpecialist
from jarvis.specialists.media import MediaSpecialist

__all__ = [
    "Specialist",
    "CoreSpecialist",
    "MediaSpecialist",
    "GrokSpecialist",
]
