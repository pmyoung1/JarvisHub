"""Interactive-session voice agent (wake word, speaker check, STT, TTS).

This process must run in the logged-on user session, not in the
Session 0 hub service. Heavy libraries are imported lazily.
"""

from jarvis.voice.agent import VoiceAgent, VoicePipeline

__all__ = ["VoiceAgent", "VoicePipeline"]
