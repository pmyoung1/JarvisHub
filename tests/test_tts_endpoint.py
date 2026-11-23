"""Talkback synthesis: backend order, fallback, and the /api/tts endpoint."""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import jarvis.server as server
from jarvis.config import Config
from jarvis.tts_service import TtsService, TtsUnavailable
from tests.support import TestHub


def make_wav(seconds: float = 0.1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x01\x00" * int(16000 * seconds))
    return buf.getvalue()


class FakeBackend:
    def __init__(self, name: str, fail: bool = False, data: bytes | None = None) -> None:
        self.name = name
        self.fail = fail
        self.data = data if data is not None else make_wav()
        self.calls: list[str] = []

    def synthesize_wav_bytes(self, text: str) -> bytes:
        self.calls.append(text)
        if self.fail:
            raise RuntimeError(f"{self.name} exploded")
        return self.data


def _cfg(tmp: Path, **voice) -> Config:
    raw = {"data_dir": str(tmp / "data"), "voice": voice}
    cfg = Config(raw)
    return cfg


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class TtsServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-tts-"))

    def test_piper_first_then_sapi_fallback_then_pyttsx3(self) -> None:
        piper = FakeBackend("piper:test", fail=True)
        sapi = FakeBackend("sapi:powershell")
        pytt = FakeBackend("sapi:pyttsx3")
        svc = TtsService(_cfg(self.tmp), piper_factory=lambda: piper, sapi_factory=lambda: sapi, pyttsx3_factory=lambda: pytt)
        data, engine = svc.synthesize("Hello there")
        self.assertEqual(engine, "sapi:powershell")
        self.assertEqual(piper.calls, ["Hello there"])
        self.assertEqual(pytt.calls, [])
        self.assertTrue(data.startswith(b"RIFF"))

        sapi.fail = True
        _data, engine = svc.synthesize("Another line")
        self.assertEqual(engine, "sapi:pyttsx3")

    def test_piper_wins_when_available_and_results_are_cached(self) -> None:
        piper = FakeBackend("piper:en_US-lessac-medium")
        svc = TtsService(_cfg(self.tmp), piper_factory=lambda: piper, sapi_factory=lambda: FakeBackend("sapi"), pyttsx3_factory=lambda: FakeBackend("p"))
        svc.synthesize("same text")
        svc.synthesize("same   text")  # whitespace-normalized => cache hit
        self.assertEqual(len(piper.calls), 1)
        self.assertEqual(svc.last_engine, "piper:en_US-lessac-medium")

    def test_unavailable_backends_are_skipped_and_all_failing_raises(self) -> None:
        def boom():
            raise FileNotFoundError("Piper voice not found")

        svc = TtsService(_cfg(self.tmp), piper_factory=boom, sapi_factory=boom, pyttsx3_factory=boom)
        with self.assertRaises(TtsUnavailable) as ctx:
            svc.synthesize("hi")
        self.assertIn("Piper voice not found", str(ctx.exception))
        self.assertIn("requirements-wakeword.txt", str(ctx.exception))

    def test_long_text_is_truncated_and_empty_rejected(self) -> None:
        piper = FakeBackend("piper")
        svc = TtsService(_cfg(self.tmp), piper_factory=lambda: piper)
        svc.synthesize("word " * 500)
        self.assertLessEqual(len(piper.calls[0]), svc.max_chars)
        with self.assertRaises(ValueError):
            svc.synthesize("   ")

    def test_explicit_model_path_config_is_resolved(self) -> None:
        cfg = _cfg(self.tmp, tts_model_path=str(self.tmp / "voices" / "x.onnx"))
        self.assertTrue(cfg.voice["tts_model_path"].endswith("x.onnx"))
        status = TtsService(cfg).status()
        self.assertEqual(status["piper_voice"], "x.onnx")
        self.assertFalse(status["piper_voice_present"])


class TtsEndpointTests(unittest.TestCase):
    def _hub(self, **kw):
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-ttsapi-"))
        hub = TestHub(tmp, **kw)
        return hub

    def test_get_and_post_return_wav_and_engine_header(self) -> None:
        with self._hub() as hub:
            server.TTS = TtsService(
                hub.cfg,
                piper_factory=lambda: FakeBackend("piper:fake"),
                sapi_factory=lambda: FakeBackend("sapi"),
                pyttsx3_factory=lambda: FakeBackend("p"),
            )
            status, headers, body = _get(hub.base + "/api/tts?text=" + urllib.parse.quote("Hello Pat"))
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "audio/wav")
            self.assertEqual(headers["X-Jarvis-TTS-Engine"], "piper:fake")
            self.assertTrue(body.startswith(b"RIFF"))
            req = urllib.request.Request(
                hub.base + "/api/tts",
                data=json.dumps({"text": "From POST"}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                self.assertEqual(resp.status, 200)
                self.assertTrue(resp.read().startswith(b"RIFF"))

    def test_fallback_to_sapi_when_piper_is_missing(self) -> None:
        with self._hub() as hub:
            def no_piper():
                raise FileNotFoundError("no voice")

            server.TTS = TtsService(hub.cfg, piper_factory=no_piper, sapi_factory=lambda: FakeBackend("sapi:powershell"), pyttsx3_factory=no_piper)
            status, headers, _ = _get(hub.base + "/api/tts?text=hi")
            self.assertEqual(status, 200)
            self.assertEqual(headers["X-Jarvis-TTS-Engine"], "sapi:powershell")

    def test_503_with_clear_message_when_nothing_is_installed(self) -> None:
        with self._hub() as hub:
            def nope():
                raise RuntimeError("not installed")

            server.TTS = TtsService(hub.cfg, piper_factory=nope, sapi_factory=nope, pyttsx3_factory=nope)
            status, _h, body = _get(hub.base + "/api/tts?text=hi")
            self.assertEqual(status, 503)
            err = json.loads(body)
            self.assertFalse(err["ok"])
            self.assertIn("not installed", err["error"])
            self.assertIn("INSTALL_WINDOWS", err["error"])

    def test_missing_text_is_400_and_token_is_enforced(self) -> None:
        with self._hub(auth_token="tok") as hub:
            server.TTS = TtsService(hub.cfg, piper_factory=lambda: FakeBackend("piper"))
            self.assertEqual(_get(hub.base + "/api/tts?text=hi")[0], 401)
            self.assertEqual(_get(hub.base + "/api/tts?text=&token=tok")[0], 400)
            self.assertEqual(_get(hub.base + "/api/tts?text=hi&token=tok")[0], 200)

    def test_status_reports_tts_without_loading_a_model(self) -> None:
        with self._hub() as hub:
            status, _h, body = _get(hub.base + "/api/status")
            tts = json.loads(body)["tts"]
            self.assertIn("piper_voice_present", tts)
            self.assertTrue(tts["enabled"])


@unittest.skipUnless(os.environ.get("JARVIS_TEST_PIPER_MODEL"), "set JARVIS_TEST_PIPER_MODEL to a Piper .onnx to run the real-engine test")
class RealPiperTests(unittest.TestCase):
    def test_real_piper_makes_a_valid_wav(self) -> None:
        from jarvis.voice.tts import PiperSpeaker

        sp = PiperSpeaker("x", Path("."), model_path=os.environ["JARVIS_TEST_PIPER_MODEL"])
        data = sp.synthesize_wav_bytes("Hello from Piper.")
        with wave.open(io.BytesIO(data)) as wf:
            self.assertGreater(wf.getnframes() / wf.getframerate(), 0.5)


if __name__ == "__main__":
    unittest.main()
