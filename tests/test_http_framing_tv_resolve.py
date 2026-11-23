"""Keep-alive body drain + TV controller auto-resolve."""

from __future__ import annotations

import http.client
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.tv.controller import resolve_tv_controller_name
from tests.support import TestHub


class ResolveTvControllerTests(unittest.TestCase):
    def test_nullish_stays_null_without_exe(self) -> None:
        for raw in (None, "null", "NULL", "none", "off", "", "  "):
            self.assertEqual(resolve_tv_controller_name({"controller": raw, "root_exe_path": ""}), "null")
            self.assertEqual(
                resolve_tv_controller_name({"controller": raw, "root_exe_path": "C:/no/such/samsung-tv-root.exe"}),
                "null",
            )

    def test_auto_selects_root_when_exe_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "samsung-tv-root.exe"
            exe.write_bytes(b"MZ")
            name = resolve_tv_controller_name({"controller": "null", "root_exe_path": str(exe)})
            self.assertEqual(name, "root")
            name = resolve_tv_controller_name({"controller": None, "root_exe_path": str(exe)})
            self.assertEqual(name, "root")

    def test_explicit_controller_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "samsung-tv-root.exe"
            exe.write_bytes(b"MZ")
            self.assertEqual(
                resolve_tv_controller_name({"controller": "samsungtvws", "root_exe_path": str(exe)}),
                "samsungtvws",
            )


class KeepAliveFramingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-frame-"))

    def test_unread_post_body_does_not_corrupt_next_request(self) -> None:
        """POST to a missing route with a JSON body used to leak into the next request line."""
        with TestHub(self.tmp) as hub:
            conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=10)
            body = json.dumps({"text": "Talkback is on."}).encode("utf-8")
            conn.request(
                "POST",
                "/api/does-not-exist",
                body=body,
                headers={"Content-Type": "application/json", "Content-Length": str(len(body)), "Connection": "keep-alive"},
            )
            resp = conn.getresponse()
            raw = resp.read()
            self.assertEqual(resp.status, 404, raw)
            # Same keep-alive socket: a real route must still parse cleanly.
            conn.request("GET", "/api/health", headers={"Connection": "keep-alive"})
            resp2 = conn.getresponse()
            raw2 = resp2.read()
            self.assertEqual(resp2.status, 200, raw2)
            self.assertTrue(json.loads(raw2).get("ok"))
            conn.close()

    def test_unauthorized_post_with_body_then_health(self) -> None:
        with TestHub(self.tmp, auth_token="secret") as hub:
            conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=10)
            body = json.dumps({"text": "Talkback is on."}).encode("utf-8")
            conn.request(
                "POST",
                "/api/tts",
                body=body,
                headers={"Content-Type": "application/json", "Content-Length": str(len(body)), "Connection": "keep-alive"},
            )
            resp = conn.getresponse()
            raw = resp.read()
            self.assertEqual(resp.status, 401, raw)
            conn.request("GET", "/api/health", headers={"Connection": "close"})
            resp2 = conn.getresponse()
            raw2 = resp2.read()
            self.assertEqual(resp2.status, 200, raw2)
            conn.close()

    def test_voice_and_transfer_routes_exist(self) -> None:
        with TestHub(self.tmp) as hub:
            conn = http.client.HTTPConnection("127.0.0.1", hub.port, timeout=10)
            for path in ("/api/voice/status", "/api/transfers"):
                conn.request("GET", path)
                resp = conn.getresponse()
                raw = resp.read()
                self.assertEqual(resp.status, 200, f"{path}: {raw}")
            conn.request(
                "POST",
                "/api/voice/mute",
                body=b'{"muted": false}',
                headers={"Content-Type": "application/json", "Content-Length": "16"},
            )
            resp = conn.getresponse()
            raw = resp.read()
            self.assertEqual(resp.status, 200, raw)
            # Poster route exists (404 for missing id is fine; not a missing route).
            conn.request("GET", "/api/media/1/poster")
            resp = conn.getresponse()
            raw = resp.read()
            self.assertIn(resp.status, {200, 404}, raw)
            conn.close()


if __name__ == "__main__":
    unittest.main()
