"""Voice pipeline unit tests with fake engines (no mic, speaker, or GPU)."""

from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.config import Config
from jarvis.voice.agent import VoiceAgent, VoicePipeline
from jarvis.voice.client import HubClient
from jarvis.voice.pcm import SAMPLE_RATE, SAMPLES_PER_FRAME, sine_pcm, write_wav
from jarvis.voice.speaker import EnrollmentStore, cosine, mean_l2, verify_pcm


def _frame(tag: bytes | None = None) -> bytes:
    # 80 ms of 16-bit silence, optionally stamped so a fake wake engine can see it.
    data = b"\x00\x00" * SAMPLES_PER_FRAME
    if tag:
        data = tag + data[len(tag) :]
    return data


class HashEncoder:
    name = "hash"

    def embed(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> list[float]:
        digest = hashlib.sha256(pcm).digest()
        vec = [((b / 127.5) - 1.0) for b in digest]
        return vec


class FakeWake:
    name = "fake-wake"

    def __init__(self, marker: bytes = b"WAKE") -> None:
        self.marker = marker
        self.calls = 0

    def score(self, pcm_frame: bytes) -> float:
        self.calls += 1
        return 1.0 if pcm_frame.startswith(self.marker) else 0.0


class FakeSTT:
    name = "fake-stt"

    def __init__(self, text: str = "play inception on the tv") -> None:
        self.text = text
        self.calls = 0

    def transcribe(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> str:
        del pcm, sample_rate
        self.calls += 1
        return self.text


class FakeTTS:
    name = "fake-tts"

    def __init__(self) -> None:
        self.spoken: list[str] = []

    def speak(self, text: str) -> None:
        self.spoken.append(text)


class FakeClient:
    def __init__(self, reply: str = "I would play Inception (2010) on the TV.") -> None:
        self.reply = reply
        self.commands: list[dict] = []
        self.heartbeats: list[dict] = []
        self.client_id = "hub-voice"

    def send_command(self, text: str, source: str = "hub-mic") -> dict:
        self.commands.append({"text": text, "source": source})
        return {"ok": True, "reply": self.reply, "intent": "media.play", "data": {"action": "would_play"}}

    def heartbeat(self, state: str, extra=None) -> None:
        payload = {"state": state}
        if extra:
            payload.update(extra)
        self.heartbeats.append(payload)

    def iter_events(self):
        if False:
            yield {}


class SpeakerTests(unittest.TestCase):
    def test_cosine_and_enrollment_roundtrip(self, tmp=None) -> None:
        tmp = Path(self._tmp())
        store = EnrollmentStore(tmp)
        encoder = HashEncoder()
        patrick = sine_pcm(1.0, freq=220)
        other = sine_pcm(1.0, freq=880)
        e1 = encoder.embed(patrick)
        e2 = encoder.embed(patrick + sine_pcm(0.05, freq=220))  # different bytes
        store.save({"name": "patrick", "centroid": mean_l2([e1]), "engine": "hash", "embeddings": [e1]})
        ok = verify_pcm(encoder, store, patrick, threshold=0.99)
        self.assertTrue(ok["ok"])
        self.assertAlmostEqual(ok["score"], 1.0, places=5)
        rejected = verify_pcm(encoder, store, other, threshold=0.99)
        self.assertFalse(rejected["ok"])
        self.assertLess(rejected["score"], 0.99)

    def test_skip_when_not_enrolled(self) -> None:
        store = EnrollmentStore(Path(self._tmp()))
        result = verify_pcm(HashEncoder(), store, sine_pcm(0.2), threshold=0.65)
        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])

    def _tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="jarvis-enroll-")


class PipelineTests(unittest.TestCase):
    def _pipe(self, **kwargs):
        cfg = Config({})
        enroll = EnrollmentStore(Path(self._tmp()))
        client = kwargs.pop("client", FakeClient())
        tts = kwargs.pop("tts", FakeTTS())
        encoder = kwargs.pop("encoder", None)
        pipe = VoicePipeline(
            cfg=cfg,
            wake=kwargs.pop("wake", FakeWake()),
            transcriber=kwargs.pop("transcriber", FakeSTT()),
            speaker_encoder=encoder,
            enrollments=enroll,
            client=client,
            tts=tts,
        )
        return pipe, client, tts, enroll

    def _tmp(self) -> str:
        import tempfile

        return tempfile.mkdtemp(prefix="jarvis-voice-")

    def test_handle_utterance_posts_and_speaks(self) -> None:
        pipe, client, tts, _ = self._pipe()
        out = pipe.handle_utterance(sine_pcm(0.4), source="hub-mic")
        self.assertTrue(out["ok"])
        self.assertEqual(out["transcript"], "play inception on the tv")
        self.assertEqual(client.commands[0]["source"], "hub-mic")
        self.assertEqual(tts.spoken[0], client.reply)

    def test_muted_skips(self) -> None:
        pipe, client, tts, _ = self._pipe()
        pipe.muted = True
        out = pipe.handle_utterance(sine_pcm(0.2))
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"], "muted")
        self.assertEqual(client.commands, [])
        self.assertEqual(tts.spoken, [])

    def test_speaker_reject(self) -> None:
        pipe, client, tts, store = self._pipe(encoder=HashEncoder())
        store.save({"name": "patrick", "centroid": HashEncoder().embed(sine_pcm(0.5, freq=100)), "embeddings": []})
        out = pipe.handle_utterance(sine_pcm(0.5, freq=2000))
        self.assertEqual(out["error"], "speaker not recognized")
        self.assertFalse(out["ok"])
        self.assertEqual(client.commands, [])

    def test_does_not_speak_other_sources(self) -> None:
        pipe, client, tts, _ = self._pipe()
        out = pipe.handle_utterance(sine_pcm(0.2), source="phone")
        self.assertTrue(out["ok"])
        self.assertEqual(tts.spoken, [])

    def test_agent_wake_then_capture(self) -> None:
        pipe, client, tts, _ = self._pipe()
        wake_frame = _frame(b"WAKE")
        silence = _frame()
        # loud-ish sine split into 80ms frames after the wake
        cmd = sine_pcm(1.6, freq=440, amplitude=0.3)
        frames = [silence, silence, wake_frame]
        step = SAMPLES_PER_FRAME * 2
        frames.extend(cmd[i : i + step] for i in range(0, len(cmd), step))
        frames.append(b"\x00\x00" * SAMPLES_PER_FRAME * 2)  # will be consumed during capture
        it = iter(frames + [silence] * 40)

        def factory():
            return it

        agent = VoiceAgent(pipe, frames=factory)
        # Run a short listen in a thread and stop after a command is posted.
        import threading
        import time

        t = threading.Thread(target=agent.run, daemon=True)
        t.start()
        deadline = time.time() + 2
        while time.time() < deadline and not client.commands:
            time.sleep(0.02)
        agent.stop()
        t.join(timeout=2)
        self.assertTrue(client.commands)
        self.assertEqual(client.commands[0]["text"], "play inception on the tv")
        self.assertTrue(tts.spoken)

    def test_wav_roundtrip(self) -> None:
        import tempfile

        pcm = sine_pcm(0.25, freq=330)
        path = Path(tempfile.mkdtemp()) / "clip.wav"
        write_wav(path, pcm)
        self.assertTrue(path.is_file())
        self.assertGreater(path.stat().st_size, 44)


class ClientUnitTests(unittest.TestCase):
    def test_headers_include_token(self) -> None:
        client = HubClient("http://127.0.0.1:9", token="secret")
        headers = client._headers()
        self.assertEqual(headers["X-Hub-Token"], "secret")


if __name__ == "__main__":
    unittest.main()
