"""Standalone wake-word listener: POST payload, auth header, dry-run, mute, lock."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.config import Config
from jarvis.voice.capture import capture_command
from jarvis.voice.client import HubClient
from jarvis.voice.listener import WakewordListener, strip_wake_phrase
from jarvis.voice.lock import ListenerLock
from jarvis.voice.pcm import SAMPLES_PER_FRAME, sine_pcm

SILENCE = b"\x00\x00" * SAMPLES_PER_FRAME


def loud(n: int = 1) -> bytes:
    return sine_pcm(0.08 * n, 440.0, 0.4)[: SAMPLES_PER_FRAME * 2 * n]


class FakeWake:
    name = "fake-wake"

    def __init__(self) -> None:
        self.resets = 0

    def score(self, frame: bytes) -> float:
        return 0.9 if frame.startswith(b"WAKE") else 0.0

    def reset(self) -> None:
        self.resets += 1


class FakeSTT:
    name = "fake-stt"

    def __init__(self, text: str = "what time is it") -> None:
        self.text = text
        self.sizes: list[int] = []

    def transcribe(self, pcm: bytes, sample_rate: int = 16000) -> str:
        self.sizes.append(len(pcm))
        return self.text


class FakeSpeaker:
    name = "fake-tts"

    def __init__(self) -> None:
        self.said: list[str] = []

    def speak(self, text: str) -> None:
        self.said.append(text)


class Capture:
    def __init__(self, voice_muted: bool = False) -> None:
        self.requests: list[dict] = []
        self.voice_muted = voice_muted
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # silence
                pass

            def _send(self, obj):
                data = json.dumps(obj).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append({"path": self.path, "body": body, "token": self.headers.get("X-Hub-Token"), "ctype": self.headers.get("Content-Type")})
                if self.path == "/api/command":
                    self._send({"reply": "It is noon.", "intent": "time", "data": {}, "stages": {}})
                else:
                    self._send({"ok": True})

            def do_GET(self):  # noqa: N802
                outer.requests.append({"path": self.path, "body": None, "token": self.headers.get("X-Hub-Token")})
                self._send({"ok": True, "muted": outer.voice_muted, "state": "listening"})

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self._t = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    def __enter__(self) -> "Capture":
        self._t.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def commands(self) -> list[dict]:
        return [r for r in self.requests if r["path"] == "/api/command"]


def frames_with_wake(speech_frames: int = 6, tail_silence: int = 20):
    wake = b"WAKE" + SILENCE[4:]
    yield SILENCE
    yield wake
    for _ in range(speech_frames):
        yield loud()
    for _ in range(tail_silence):
        yield SILENCE
    for _ in range(3):  # extra frames so the loop can finish naturally
        yield SILENCE


class WakewordListenerTests(unittest.TestCase):
    def test_wake_posts_transcript_to_api_command_with_token(self) -> None:
        with Capture() as hub:
            client = HubClient(hub.url, token="devtoken", client_id="wakeword-testpc")
            stt = FakeSTT("what time is it")
            wake = FakeWake()
            listener = WakewordListener(wake, stt, client, threshold=0.5, min_command_seconds=0.2)
            listener.run(frames_with_wake())
            cmds = hub.commands()
            self.assertEqual(len(cmds), 1)
            self.assertEqual(cmds[0]["body"], {"text": "what time is it", "source": "wakeword", "client_id": "wakeword-testpc"})
            self.assertEqual(cmds[0]["token"], "devtoken")
            self.assertEqual(cmds[0]["ctype"], "application/json")
            self.assertEqual(wake.resets, 1)
            self.assertTrue(listener.last_result["posted"])
            # captured audio includes the wake frame and the speech, not just silence
            self.assertGreater(stt.sizes[0], SAMPLES_PER_FRAME * 2 * 3)
            states = [r["body"]["state"] for r in hub.requests if r["path"] == "/api/voice/status" and r["body"]]
            self.assertEqual(states[0], "listening")
            self.assertIn("capturing", states)
            self.assertIn("processing", states)
            self.assertEqual(states[-1], "offline")


    def test_strips_wake_phrase_before_post(self) -> None:
        with Capture() as hub:
            listener = WakewordListener(
                FakeWake(),
                FakeSTT("hey jarvis play inception on the tv"),
                HubClient(hub.url, token="t", client_id="wakeword-testpc"),
                min_command_seconds=0.2,
            )
            listener.run(frames_with_wake())
            cmds = hub.commands()
            self.assertEqual(len(cmds), 1)
            self.assertEqual(cmds[0]["body"]["text"], "play inception on the tv")
            self.assertEqual(listener.last_result["transcript"], "play inception on the tv")
            self.assertEqual(listener.last_result["raw_transcript"], "hey jarvis play inception on the tv")

    def test_wake_only_transcript_skips_post(self) -> None:
        with Capture() as hub:
            listener = WakewordListener(FakeWake(), FakeSTT("Hey Jarvis"), HubClient(hub.url), min_command_seconds=0.2)
            listener.run(frames_with_wake())
            self.assertEqual(hub.commands(), [])
            self.assertEqual(listener.last_result["error"], "wake-only")
            self.assertTrue(listener.last_result["ok"])
            self.assertFalse(listener.last_result["posted"])

    def test_dry_run_transcribes_but_posts_nothing(self) -> None:
        with Capture() as hub:
            client = HubClient(hub.url, token="t")
            listener = WakewordListener(FakeWake(), FakeSTT("hello"), client, dry_run=True, min_command_seconds=0.2)
            listener.run(frames_with_wake())
            self.assertEqual(hub.requests, [])
            self.assertEqual(listener.last_result["transcript"], "hello")
            self.assertFalse(listener.last_result["posted"])

    def test_below_threshold_never_posts(self) -> None:
        with Capture() as hub:
            listener = WakewordListener(FakeWake(), FakeSTT(), HubClient(hub.url), threshold=0.95, min_command_seconds=0.2)
            listener.run(frames_with_wake())
            self.assertEqual(hub.commands(), [])

    def test_muted_hub_suppresses_commands(self) -> None:
        with Capture(voice_muted=True) as hub:
            listener = WakewordListener(FakeWake(), FakeSTT(), HubClient(hub.url), min_command_seconds=0.2)
            listener.run(frames_with_wake())
            self.assertTrue(listener.muted)
            self.assertEqual(hub.commands(), [])

    def test_empty_transcript_is_dropped_and_hub_down_is_survived(self) -> None:
        with Capture() as hub:
            listener = WakewordListener(FakeWake(), FakeSTT("  "), HubClient(hub.url), min_command_seconds=0.2)
            listener.run(frames_with_wake())
            self.assertEqual(hub.commands(), [])
            self.assertEqual(listener.last_result["error"], "empty transcript")
        dead = WakewordListener(FakeWake(), FakeSTT("hi"), HubClient("http://127.0.0.1:9", timeout=1), min_command_seconds=0.2)
        self.assertEqual(dead.run(frames_with_wake()), 0)
        self.assertIn("hub command failed", dead.last_result["error"])

    def test_speak_option_speaks_the_hub_reply(self) -> None:
        with Capture() as hub:
            tts = FakeSpeaker()
            listener = WakewordListener(FakeWake(), FakeSTT(), HubClient(hub.url), speaker=tts, min_command_seconds=0.2)
            listener.run(frames_with_wake())
            self.assertEqual(tts.said, ["It is noon."])

    def test_capture_command_stops_on_silence(self) -> None:
        frames = iter([loud()] * 5 + [SILENCE] * 40)
        data = capture_command(frames, command_seconds=6, min_command_seconds=0.2)
        self.assertLess(len(data), (5 + 40) * SAMPLES_PER_FRAME * 2)
        self.assertGreaterEqual(len(data), 5 * SAMPLES_PER_FRAME * 2)


class WakewordScriptTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        spec = importlib.util.spec_from_file_location("wakeword_script", ROOT / "scripts" / "wakeword.py")
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)

    def test_flags_exist(self) -> None:
        args = self.mod.parse_args(["--hub-url", "http://h:1", "--token", "x", "--threshold", "0.7", "--dry-run", "--list-devices"])
        self.assertEqual((args.hub_url, args.token, args.threshold, args.dry_run, args.list_devices), ("http://h:1", "x", 0.7, True, True))

    def test_settings_precedence(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        cfg = Config({"data_dir": str(tmp), "auth_token": "household", "auth_tokens": {"voice": "voicetok"}, "voice": {"hub_url": "http://cfg:8765/", "wake_threshold": 0.4}})
        s = self.mod.resolve_settings(self.mod.parse_args([]), cfg)
        self.assertEqual((s["hub_url"], s["token"], s["threshold"]), ("http://cfg:8765", "voicetok", 0.4))
        s = self.mod.resolve_settings(self.mod.parse_args(["--hub-url", "http://cli:1", "--token", "cli", "--threshold", "0.9"]), cfg)
        self.assertEqual((s["hub_url"], s["token"], s["threshold"]), ("http://cli:1", "cli", 0.9))
        self.assertTrue(s["client_id"].startswith("wakeword-"))

    def test_missing_wake_engine_exits_2_with_install_hint(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        cfg_path = tmp / "config.json"
        cfg_path.write_text(json.dumps({"data_dir": str(tmp / "data"), "voice": {"wake_engine": "none"}}))
        self.assertEqual(self.mod.main(["--config", str(cfg_path), "--dry-run", "--allow-multiple"]), 2)

    def test_wav_frames_reader_rejects_wrong_format(self) -> None:
        import wave

        p = Path(tempfile.mkdtemp()) / "x.wav"
        with wave.open(str(p), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(44100)
            wf.writeframes(b"\x00" * 400)
        with self.assertRaises(SystemExit):
            list(self.mod.wav_frames(str(p)))


@unittest.skipIf(sys.platform == "win32", "flock-style semantics are exercised on POSIX; Windows uses msvcrt.locking")
class ListenerLockTests(unittest.TestCase):
    def test_second_holder_is_refused_until_release(self) -> None:
        path = Path(tempfile.mkdtemp()) / "voice" / "listener.lock"
        a, b = ListenerLock(path), ListenerLock(path)
        self.assertTrue(a.acquire())
        self.assertFalse(b.acquire())
        a.release()
        self.assertTrue(b.acquire())
        b.release()



class StripWakePhraseTests(unittest.TestCase):
    def test_common_prefixes(self) -> None:
        self.assertEqual(strip_wake_phrase("hey jarvis play inception on the tv"), "play inception on the tv")
        self.assertEqual(strip_wake_phrase("Hey Jarvis, play inception"), "play inception")
        self.assertEqual(strip_wake_phrase("JARVIS play inception"), "play inception")
        self.assertEqual(strip_wake_phrase("hey jarvis"), "")
        self.assertEqual(strip_wake_phrase("Hey Jarvis!"), "")
        self.assertEqual(strip_wake_phrase("play inception on the tv"), "play inception on the tv")

    def test_custom_phrase_and_mid_sentence_untouched(self) -> None:
        self.assertEqual(strip_wake_phrase("okay google play music", "okay google"), "play music")
        # Do not strip jarvis in the middle of a title-like phrase after a real command starts.
        self.assertEqual(strip_wake_phrase("play jarvis returns on the tv"), "play jarvis returns on the tv")



if __name__ == "__main__":
    unittest.main()
