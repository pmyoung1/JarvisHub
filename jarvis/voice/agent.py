"""Voice agent process: listen for the wake word, verify speaker, STT, post to hub, speak replies."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Iterator

from jarvis.config import Config, load_config
from jarvis.logging_setup import setup_logging
from jarvis.voice.client import HubClient
from jarvis.voice.pcm import BYTES_PER_FRAME, SAMPLE_RATE, rms
from jarvis.voice.speaker import EnrollmentStore, SpeakerEncoder, build_speaker_encoder, verify_pcm
from jarvis.voice.stt import Transcriber, build_transcriber
from jarvis.voice.tts import Speaker, build_speaker
from jarvis.voice.wake import WakeEngine, build_wake_engine

LOG = logging.getLogger("jarvis.voice")

# Simple energy VAD thresholds for 16-bit audio normalized 0..1.
VAD_START = 0.018
VAD_CONTINUE = 0.010


class VoicePipeline:
    """Testable core: feed PCM, get a command result. Engines are injectable."""

    def __init__(
        self,
        cfg: Config,
        wake: WakeEngine,
        transcriber: Transcriber,
        speaker_encoder: SpeakerEncoder | None,
        enrollments: EnrollmentStore,
        client: HubClient,
        tts: Speaker,
        speak_sources: list[str] | None = None,
    ) -> None:
        self.cfg = cfg
        self.wake = wake
        self.transcriber = transcriber
        self.speaker_encoder = speaker_encoder
        self.enrollments = enrollments
        self.client = client
        self.tts = tts
        self.speak_sources = list(speak_sources or cfg.voice.get("speak_sources") or ["hub-mic"])
        self.muted = bool(cfg.voice.get("mute"))
        self.speaking = threading.Event()
        self.state = "idle"
        self.last_error = ""
        self.threshold = float(cfg.voice.get("wake_threshold") or 0.5)
        self.speaker_threshold = float(cfg.voice.get("speaker_threshold") or 0.65)
        self.command_seconds = float(cfg.voice.get("command_seconds") or 6.0)
        self.min_command_seconds = float(cfg.voice.get("min_command_seconds") or 1.0)

    def handle_utterance(self, pcm: bytes, source: str = "hub-mic") -> dict[str, Any]:
        """Verify (if enrolled), transcribe, POST /api/command, optionally speak the reply."""
        result: dict[str, Any] = {"ok": False, "transcript": "", "verification": None, "command": None}
        if self.muted:
            result["error"] = "muted"
            return result
        if self.speaker_encoder is not None:
            verification = verify_pcm(
                self.speaker_encoder,
                self.enrollments,
                pcm,
                threshold=self.speaker_threshold,
            )
            result["verification"] = verification
            if not verification.get("ok"):
                result["error"] = "speaker not recognized"
                LOG.info("Rejected utterance (score=%s)", verification.get("score"))
                return result
        else:
            result["verification"] = {"ok": True, "skipped": True, "reason": "no speaker encoder"}

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

        try:
            command = self.client.send_command(transcript, source=source)
        except Exception as exc:  # noqa: BLE001
            result["error"] = f"hub command failed: {exc}"
            LOG.warning("Hub command failed: %s", exc)
            return result
        result["command"] = command
        result["ok"] = bool(command.get("ok", True)) and "error" not in command
        reply = ""
        if isinstance(command, dict):
            reply = str(command.get("reply") or "")
        if reply and source in self.speak_sources:
            self.speak(reply)
        return result

    def speak(self, text: str) -> None:
        text = (text or "").strip()
        if not text or self.muted:
            return
        self.speaking.set()
        try:
            self.tts.speak(text)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("TTS failed: %s", exc)
        finally:
            self.speaking.clear()

    def maybe_wake(self, frame: bytes) -> bool:
        if self.muted or self.speaking.is_set():
            return False
        try:
            score = float(self.wake.score(frame))
        except Exception as exc:  # noqa: BLE001
            LOG.debug("wake score failed: %s", exc)
            return False
        return score >= self.threshold


class VoiceAgent:
    def __init__(self, pipeline: VoicePipeline, frames: Callable[[], Iterator[bytes]] | None = None) -> None:
        self.pipeline = pipeline
        self._frames_factory = frames
        self._stop = threading.Event()
        self._sse_thread: threading.Thread | None = None

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> int:
        p = self.pipeline
        p.client.heartbeat("starting", {"wake": getattr(p.wake, "name", ""), "stt": getattr(p.transcriber, "name", ""), "tts": getattr(p.tts, "name", "")})
        if p.enrollments.centroid is None:
            LOG.warning("No enrollment profile yet — speaker verification will be skipped until you enroll.")
        self._sse_thread = threading.Thread(target=self._sse_loop, name="jarvis-voice-sse", daemon=True)
        self._sse_thread.start()
        try:
            self._listen_loop()
        finally:
            p.client.heartbeat("offline")
        return 0

    def _listen_loop(self) -> None:
        p = self.pipeline
        if self._frames_factory is None:
            from jarvis.voice.audio import iter_mic_frames

            frames = iter_mic_frames(str(p.cfg.voice.get("input_device") or ""))
        else:
            frames = self._frames_factory()
        p.state = "listening"
        p.client.heartbeat("listening")
        trailing: list[bytes] = []
        last_beat = time.time()
        for frame in frames:
            if self._stop.is_set():
                return
            now = time.time()
            if now - last_beat >= 15:
                p.client.heartbeat(p.state if not p.muted else "muted")
                last_beat = now
            if p.muted or p.speaking.is_set():
                continue
            trailing.append(frame)
            if len(trailing) > 8:
                trailing.pop(0)
            if not p.maybe_wake(frame):
                continue
            LOG.info("Wake word detected")
            p.state = "capturing"
            p.client.heartbeat("capturing")
            utterance = b"".join(trailing) + self._capture_command(frames)
            trailing = []
            p.state = "processing"
            p.client.heartbeat("processing")
            result = p.handle_utterance(utterance, source="hub-mic")
            if result.get("error"):
                LOG.info("Utterance dropped: %s", result["error"])
            p.state = "listening"
            p.client.heartbeat("listening", {"last_transcript": result.get("transcript") or ""})

    def _capture_command(self, frames: Iterator[bytes]) -> bytes:
        p = self.pipeline
        max_frames = max(1, int(p.command_seconds * SAMPLE_RATE * 2 / BYTES_PER_FRAME))
        min_frames = max(1, int(p.min_command_seconds * SAMPLE_RATE * 2 / BYTES_PER_FRAME))
        collected: list[bytes] = []
        silent = 0
        started = False
        for i, frame in enumerate(frames):
            if self._stop.is_set() or i >= max_frames:
                break
            collected.append(frame)
            level = rms(frame)
            if level >= VAD_START:
                started = True
                silent = 0
            elif started and level < VAD_CONTINUE:
                silent += 1
                if len(collected) >= min_frames and silent >= 15:  # ~1.2 s of silence
                    break
            if p.speaking.is_set():
                break
        return b"".join(collected)

    def _sse_loop(self) -> None:
        p = self.pipeline
        while not self._stop.is_set():
            try:
                for event in p.client.iter_events():
                    if self._stop.is_set():
                        return
                    self._on_event(event)
            except Exception as exc:  # noqa: BLE001
                LOG.debug("SSE disconnected: %s", exc)
                time.sleep(2)

    def _on_event(self, event: dict[str, Any]) -> None:
        p = self.pipeline
        typ = event.get("type")
        data = event.get("data") or {}
        if typ == "voice" and isinstance(data, dict):
            if "muted" in data:
                p.muted = bool(data["muted"])
                p.state = "muted" if p.muted else "listening"
            return
        if typ != "reply" or not isinstance(data, dict):
            return
        source = str(data.get("source") or "")
        if source not in p.speak_sources:
            return
        # The pipeline already speaks after send_command; this covers replies
        # that originated from the same source on another path.
        if data.get("client_id") == p.client.client_id:
            return
        reply = str(data.get("reply") or "")
        if reply:
            p.speak(reply)


def build_pipeline(cfg: Config, client: HubClient | None = None) -> VoicePipeline:
    voice = cfg.voice
    token = (cfg.auth_tokens.get("voice") or cfg.auth_token or "").strip()
    hub_url = str(voice.get("hub_url") or f"http://127.0.0.1:{cfg.port}")
    if client is None:
        client = HubClient(hub_url, token=token, client_id=str(voice.get("client_id") or "hub-voice"))
    wake = build_wake_engine(str(voice.get("wake_engine") or "openwakeword"), str(voice.get("wake_model") or "hey jarvis"))
    transcriber = build_transcriber(
        str(voice.get("stt_engine") or "faster-whisper"),
        str(voice.get("stt_model") or "small"),
        str(voice.get("stt_device") or "cuda"),
        str(voice.get("stt_compute_type") or "int8_float16"),
    )
    savedir = cfg.data_dir / "voice" / "models"
    encoder = build_speaker_encoder(str(voice.get("speaker_engine") or "ecapa"), savedir=savedir)
    enrollments = EnrollmentStore(Path(voice["enrollments_dir"]))
    tts = build_speaker(
        str(voice.get("tts_engine") or "piper"),
        str(voice.get("tts_voice") or "en_US-lessac-medium"),
        Path(voice["tts_voices_dir"]),
        str(voice.get("tts_fallback") or "sapi"),
        str(voice.get("output_device") or ""),
    )
    return VoicePipeline(
        cfg=cfg,
        wake=wake,
        transcriber=transcriber,
        speaker_encoder=encoder,
        enrollments=enrollments,
        client=client,
        tts=tts,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Jarvis Hub interactive voice agent")
    parser.add_argument("--config", help="Path to config.json (or set JARVIS_CONFIG)")
    parser.add_argument("--list-devices", action="store_true", help="Print audio devices and exit")
    args = parser.parse_args(argv)
    if args.config:
        os.environ["JARVIS_CONFIG"] = args.config
    cfg = load_config()
    setup_logging(cfg, name="jarvis.voice")
    if args.list_devices:
        from jarvis.voice.audio import list_devices

        for dev in list_devices():
            print(f"{dev['index']}: {dev['name']}  in={dev['max_input_channels']} out={dev['max_output_channels']}")
        return 0
    if not cfg.voice.get("enabled", True):
        LOG.info("voice.enabled is false; exiting.")
        return 0
    pipeline = build_pipeline(cfg)
    agent = VoiceAgent(pipeline)

    def _stop(*_args: object) -> None:
        agent.stop()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    LOG.info(
        "Voice agent starting (wake=%s stt=%s tts=%s encoder=%s)",
        getattr(pipeline.wake, "name", "?"),
        getattr(pipeline.transcriber, "name", "?"),
        getattr(pipeline.tts, "name", "?"),
        getattr(pipeline.speaker_encoder, "name", "none"),
    )
    return agent.run()


if __name__ == "__main__":
    sys.exit(main())
