"""Pluggable command specialists."""

from jarvis.specialists.base import Specialist
from jarvis.specialists.core import CoreSpecialist
from jarvis.specialists.grok import GrokSpecialist
from jarvis.specialists.media import MediaSpecialist
from jarvis.specialists.tv import TvSpecialist

__all__ = [
    "Specialist",
    "CoreSpecialist",
    "MediaSpecialist",
    "TvSpecialist",
    "GrokSpecialist",
]
