"""Standalone wake-word listener: wake -> capture -> STT -> POST /api/command.

This is the testable core behind scripts/wakeword.py. It reuses the same
engines as the full voice agent (jarvis.voice.wake / stt / client / capture)
but has no speaker verification and no always-on talkback; replies show up on
the dashboards (and can be spoken by the phone/TV pages via /api/tts).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable, Iterator

from jarvis.voice.capture import capture_command
from jarvis.voice.client import HubClient

LOG = logging.getLogger("jarvis.wakeword")

MUTE_POLL_SECONDS = 5.0
HEARTBEAT_SECONDS = 15.0
COOLDOWN_SECONDS = 1.5


class WakewordListener:
    def __init__(
        self,
        wake: Any,
        transcriber: Any,
        client: HubClient | None,
        *,
        threshold: float = 0.5,
        source: str = "wakeword",
        dry_run: bool = False,
        command_seconds: float = 6.0,
        min_command_seconds: float = 1.0,
        speaker: Any = None,
        trailing_frames: int = 8,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.wake = wake
        self.transcriber = transcriber
        self.client = client
        self.threshold = float(threshold)
        self.source = source
        self.dry_run = dry_run
        self.command_seconds = command_seconds
        self.min_command_seconds = min_command_seconds
        self.speaker = speaker
        self.trailing_frames = max(0, int(trailing_frames))
        self._clock = clock
        self.stop_event = threading.Event()
        self.muted = False
        self.state = "idle"
        self.last_result: dict[str, Any] | None = None

    # ---- hub side-channel (heartbeats / mute) -----------------------------

    def _beat(self, state: str, extra: dict[str, Any] | None = None) -> None:
        self.state = state
        if self.client is not None and not self.dry_run:
            self.client.heartbeat(state, extra)

    def _poll_mute(self) -> None:
        if self.client is None or self.dry_run:
            return
        try:
            status = self.client.get_voice_status()
        except Exception as exc:  # noqa: BLE001
            LOG.debug("mute poll failed: %s", exc)
            return
        self.muted = bool(status.get("muted"))

    # ---- one utterance ----------------------------------------------------

    def handle_utterance(self, pcm: bytes) -> dict[str, Any]:
        """Transcribe and (unless dry-run) POST to the hub. Returns a result dict."""
        result: dict[str, Any] = {"ok": False, "transcript": "", "posted": False}
        try:
            transcript = (self.transcriber.transcribe(pcm) or "").strip()
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"stt failed: {exc}"
            LOG.warning("STT failed: %s", exc)
            return result
        result["transcript"] = transcript
        if not transcript:
            result["error"] = "empty transcript"
            return result
        if self.dry_run or self.client is None:
            LOG.info("[dry-run] would POST /api/command text=%r source=%s", transcript, self.source)
            result["ok"] = True
            return result
        try:
            command = self.client.send_command(transcript, source=self.source)
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"hub command failed: {exc}"
            LOG.warning("Hub command failed: %s", exc)
            return result
        result["command"] = command
        result["posted"] = True
        result["ok"] = bool(command.get("ok", True)) and "error" not in command
        reply = str(command.get("reply") or "")
        LOG.info("Heard %r -> %s", transcript, reply or "(no reply)")
        if reply and self.speaker is not None:
            try:
                self.speaker.speak(reply)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("TTS failed: %s", exc)
        return result

    # ---- main loop --------------------------------------------------------

    def run(self, frames: Iterator[bytes]) -> int:
        self._poll_mute()
        self._beat("listening", {"wake": getattr(self.wake, "name", ""), "stt": getattr(self.transcriber, "name", "")})
        trailing: list[bytes] = []
        last_beat = last_mute = self._clock()
        cooldown_until = 0.0
        try:
            for frame in frames:
                if self.stop_event.is_set():
                    break
                now = self._clock()
                if now - last_mute >= MUTE_POLL_SECONDS:
                    self._poll_mute()
                    last_mute = now
                if now - last_beat >= HEARTBEAT_SECONDS:
                    self._beat("muted" if self.muted else "listening")
                    last_beat = now
                if self.muted or now < cooldown_until:
                    continue
                if self.trailing_frames:
                    trailing.append(frame)
                    if len(trailing) > self.trailing_frames:
                        trailing.pop(0)
                try:
                    score = float(self.wake.score(frame))
                except Exception as exc:  # noqa: BLE001
                    LOG.debug("wake score failed: %s", exc)
                    continue
                if score < self.threshold:
                    continue
                LOG.info("Wake word detected (score %.2f)", score)
                self._beat("capturing")
                utterance = b"".join(trailing) + capture_command(
                    frames, self.command_seconds, self.min_command_seconds, self.stop_event.is_set
                )
                trailing = []
                self._beat("processing")
                self.last_result = self.handle_utterance(utterance)
                if self.last_result.get("error"):
                    LOG.info("Utterance dropped: %s", self.last_result["error"])
                reset = getattr(self.wake, "reset", None)
                if callable(reset):
                    reset()
                cooldown_until = self._clock() + COOLDOWN_SECONDS
                self._beat("listening", {"last_transcript": self.last_result.get("transcript") or ""})
                last_beat = self._clock()
        finally:
            self._beat("offline")
        return 0
