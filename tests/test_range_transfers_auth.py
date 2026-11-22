"""HTTP Range streaming, phone-to-hub uploads, and per-device tokens.

Uses a live local hub. Magic/extension checks and Range headers are real;
no TV hardware is involved (the TV controller is NullController / hook).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.support import TestHub, fake_mp4, fake_png


def _request(url, method="GET", headers=None, data=None, range=None):
    hdrs = dict(headers or {})
    if range:
        hdrs["Range"] = range
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class RangeAndTransferTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-range-"))
        self.lib = self.tmp / "movies" / "Inception (2010)"
        self.lib.mkdir(parents=True)
        self.video = self.lib / "Inception.2010.mp4"
        self.payload = fake_mp4(48 * 1024)
        self.video.write_bytes(self.payload)

    def test_range_stream_and_signed_url(self) -> None:
        with TestHub(self.tmp, media_paths=[str(self.tmp / "movies")]) as hub:
            result = hub.index.scan(full=True)
            self.assertGreaterEqual(result.get("added") or result.get("total") or hub.index.count(), 1)
            items = hub.index.search("inception")
            self.assertTrue(items)
            media_id = items[0]["id"]
            # Play command should include a stream URL (still would_play under NullController).
            body = json.dumps({"text": "play inception on the tv", "source": "test", "client_id": "t"}).encode()
            status, _h, raw = _request(hub.base + "/api/command", method="POST", headers={"Content-Type": "application/json"}, data=body)
            self.assertEqual(status, 200)
            reply = json.loads(raw)
            self.assertEqual(reply["data"]["action"], "would_play")
            stream_url = reply["data"]["stream_url"]
            self.assertIn("/api/media/", stream_url)
            self.assertIn("sig=", stream_url)

            status, headers, data = _request(stream_url, range="bytes=0-99")
            self.assertEqual(status, 206)
            self.assertEqual(headers.get("Accept-Ranges") or headers.get("accept-ranges"), "bytes")
            self.assertTrue((headers.get("Content-Range") or headers.get("content-range") or "").startswith("bytes 0-99/"))
            self.assertEqual(data, self.payload[:100])

            status, headers, data = _request(stream_url)
            self.assertEqual(status, 200)
            self.assertEqual(len(data), len(self.payload))

            status, _h, _d = _request(stream_url, range="bytes=999999-9999999")
            self.assertEqual(status, 416)

            # Unsigned stream is allowed on an open LAN (no auth_token).
            # With a token, the bare URL should 401.
        with TestHub(self.tmp, media_paths=[str(self.tmp / "movies")], auth_token="secret") as hub:
            hub.index.scan(full=True)
            media_id = hub.index.search("inception")[0]["id"]
            bare = f"{hub.base}/api/media/{media_id}/stream"
            status, _h, _d = _request(bare)
            self.assertEqual(status, 401)
            status, _h, data = _request(bare, headers={"X-Hub-Token": "secret"}, range="bytes=0-9")
            self.assertEqual(status, 206)
            self.assertEqual(data, self.payload[:10])

    def test_upload_allowlist_and_transfer_stream(self) -> None:
        with TestHub(self.tmp) as hub:
            png = fake_png()
            # Raw body with X-Filename
            status, _h, raw = _request(
                hub.base + "/api/transfers",
                method="POST",
                headers={"Content-Type": "image/png", "X-Filename": "shot.png"},
                data=png,
            )
            self.assertEqual(status, 201, raw)
            item = json.loads(raw)["item"]
            self.assertEqual(item["filename"], "shot.png")
            self.assertIn("stream_url", item)
            status, headers, data = _request(item["stream_url"], range="bytes=0-7")
            self.assertEqual(status, 206)
            self.assertEqual(data, png[:8])

            # Rejected extension
            status, _h, raw = _request(
                hub.base + "/api/transfers",
                method="POST",
                headers={"Content-Type": "application/octet-stream", "X-Filename": "payload.exe"},
                data=b"MZ" + b"\x00" * 20,
            )
            self.assertEqual(status, 400)
            self.assertIn("not allowed", json.loads(raw)["error"])

            # Bad magic for png extension
            status, _h, raw = _request(
                hub.base + "/api/transfers",
                method="POST",
                headers={"Content-Type": "image/png", "X-Filename": "fake.png"},
                data=b"not a png at all........",
            )
            self.assertEqual(status, 400)

            # List + delete
            status, _h, raw = _request(hub.base + "/api/transfers")
            self.assertEqual(status, 200)
            items = json.loads(raw)["items"]
            self.assertEqual(len(items), 1)
            ident = items[0]["public_id"]
            status, _h, raw = _request(hub.base + f"/api/transfers/{ident}", method="DELETE")
            self.assertEqual(status, 200)
            status, _h, raw = _request(hub.base + "/api/transfers")
            self.assertEqual(json.loads(raw)["items"], [])

    def test_oversize_content_length_rejected_without_reading_forever(self) -> None:
        with TestHub(self.tmp, transfers={"dir": str(self.tmp / "data" / "transfers"), "max_bytes": 64, "ttl_hours": 1, "allowed_extensions": [".png"]}) as hub:
            # Config merge: TestHub.update may replace transfers entirely; ensure dir exists
            status, _h, raw = _request(
                hub.base + "/api/transfers",
                method="POST",
                headers={"Content-Type": "image/png", "X-Filename": "big.png"},
                data=fake_png() + b"\x00" * 200,
            )
            self.assertEqual(status, 413)


class AuthTokenTests(unittest.TestCase):
    def test_per_device_token_and_sha256_hash(self) -> None:
        import hashlib

        tmp = Path(tempfile.mkdtemp(prefix="jarvis-auth-"))
        digest = "sha256:" + hashlib.sha256(b"phone-secret").hexdigest()
        with TestHub(tmp, auth_token="", auth_tokens={"phone": digest, "voice": "voice-secret"}) as hub:
            status, _h, _d = _request(hub.base + "/api/status")
            self.assertEqual(status, 401)
            status, _h, raw = _request(hub.base + "/api/status", headers={"X-Hub-Token": "phone-secret"})
            self.assertEqual(status, 200)
            status, _h, raw = _request(hub.base + "/api/status", headers={"X-Hub-Token": "voice-secret"})
            self.assertEqual(status, 200)
            status, _h, _d = _request(hub.base + "/api/status", headers={"X-Hub-Token": "wrong"})
            self.assertEqual(status, 401)
            # Household secret compatibility: auth_token still works if set alongside.
        with TestHub(tmp, auth_token="legacy", auth_tokens={"phone": "phone"}) as hub:
            status, _h, _d = _request(hub.base + "/api/status", headers={"X-Hub-Token": "legacy"})
            self.assertEqual(status, 200)
            status, _h, _d = _request(hub.base + "/api/status", headers={"Authorization": "Bearer phone"})
            self.assertEqual(status, 200)


class TvControllerTests(unittest.TestCase):
    def test_null_and_unconfigured_root_are_would_play(self) -> None:
        from jarvis.tv import DlnaController, NullController, SamsungTvRootController, build_tv_controller

        null = build_tv_controller("null")
        self.assertIsInstance(null, NullController)
        result = null.play_url("http://127.0.0.1/x", title="Inception")
        self.assertEqual(result["action"], "would_play")
        root = SamsungTvRootController({})
        result = root.play_url("http://127.0.0.1/x", title="Inception")
        self.assertEqual(result["action"], "would_play")
        self.assertIn("not set", result["message"])
        dlna = DlnaController()
        self.assertEqual(dlna.play_url("http://127.0.0.1/x")["action"], "would_play")

    def test_root_endpoint_hook(self) -> None:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        import threading
        from jarvis.tv import SamsungTvRootController

        received = {}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                received["body"] = json.loads(self.rfile.read(n))
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"ok")

            def log_message(self, *a):
                return

        httpd = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            port = httpd.server_address[1]
            ctl = SamsungTvRootController({"endpoint": f"http://127.0.0.1:{port}/play"})
            result = ctl.play_url("http://hub/stream", title="Inception")
            self.assertTrue(result["ok"])
            self.assertEqual(result["action"], "playing")
            self.assertEqual(received["body"]["url"], "http://hub/stream")
        finally:
            httpd.shutdown()


if __name__ == "__main__":
    unittest.main()
