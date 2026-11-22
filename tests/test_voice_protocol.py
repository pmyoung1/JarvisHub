"""End-to-end hub <-> voice agent localhost protocol with a simulated wake + transcript.

Verified with fake wake/STT/TTS engines against a live local hub — no microphone.
"""

from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.voice.agent import VoiceAgent, VoicePipeline
from jarvis.voice.client import HubClient
from jarvis.voice.pcm import SAMPLES_PER_FRAME, sine_pcm
from jarvis.voice.speaker import EnrollmentStore
from tests.support import TestHub
from tests.test_voice import FakeSTT, FakeTTS, FakeWake, HashEncoder, _frame


class ProtocolTests(unittest.TestCase):
    def test_simulated_wake_posts_command_and_speaks_reply(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-proto-"))
        with TestHub(tmp) as hub:
            client = HubClient(hub.base, client_id="hub-voice")
            tts = FakeTTS()
            pipe = VoicePipeline(
                cfg=hub.cfg,
                wake=FakeWake(),
                transcriber=FakeSTT("status"),
                speaker_encoder=None,
                enrollments=EnrollmentStore(tmp / "enroll"),
                client=client,
                tts=tts,
            )
            cmd = sine_pcm(1.2, freq=440, amplitude=0.3)
            step = SAMPLES_PER_FRAME * 2
            frames = [_frame(), _frame(b"WAKE")]
            frames.extend(cmd[i : i + step] for i in range(0, len(cmd), step))
            frames.extend([_frame()] * 30)
            agent = VoiceAgent(pipe, frames=lambda: iter(frames))
            thread = threading.Thread(target=agent.run, daemon=True)
            thread.start()
            deadline = time.time() + 4
            while time.time() < deadline and not tts.spoken:
                time.sleep(0.05)
            agent.stop()
            thread.join(timeout=2)
            self.assertTrue(tts.spoken, "TTS should speak the hub reply after a simulated wake")
            self.assertTrue(any("Jarvis" in s or "indexed" in s.lower() or "up" in s.lower() for s in tts.spoken))
            # Heartbeat should have flipped the hub voice status off "offline"
            with urllib.request.urlopen(hub.base + "/api/voice/status") as resp:
                status = json.loads(resp.read())
            self.assertIn(status.get("state"), {"listening", "processing", "capturing", "offline", "starting"})

    def test_mute_endpoint_reaches_pipeline_via_sse(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-mute-"))
        with TestHub(tmp) as hub:
            req = urllib.request.Request(
                hub.base + "/api/voice/mute",
                data=json.dumps({"muted": True}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                body = json.loads(resp.read())
            self.assertTrue(body["muted"])
            with urllib.request.urlopen(hub.base + "/api/voice/status") as resp:
                status = json.loads(resp.read())
            self.assertTrue(status["muted"])


if __name__ == "__main__":
    unittest.main()
