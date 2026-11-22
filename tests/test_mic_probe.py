"""Mic-probe endpoint tests (the TV page itself is not run on a Tizen set here)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.support import TestHub


def _request(url, method="GET", headers=None, data=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class MicProbeEndpointTests(unittest.TestCase):
    def test_page_is_served_and_report_roundtrip(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-micprobe-"))
        with TestHub(tmp) as hub:
            status, headers, body = _request(hub.base + "/tv/mic-probe")
            self.assertEqual(status, 200)
            text = body.decode("utf-8", errors="replace")
            self.assertIn("TV microphone probe", text)
            self.assertIn("mic-probe.js", text)

            payload = {
                "client_ts": "2026-11-22T16:00:00Z",
                "tizen_api": "absent",
                "getusermedia": "fail",
                "live_audio": False,
                "secure_context": False,
                "protocol": "http:",
                "href": hub.base + "/tv/mic-probe",
                "peak_amplitude": 0,
                "steps": [{"kind": "info", "title": "Load webapis", "detail": "script-error"}],
                "errors": [{"name": "Error", "message": "Failed to load", "code": ""}],
                "notes": "No live audio",
            }
            status, _h, raw = _request(
                hub.base + "/api/mic-probe",
                method="POST",
                headers={"Content-Type": "application/json", "User-Agent": "TizenTest/1.0"},
                data=json.dumps(payload).encode(),
            )
            self.assertEqual(status, 201, raw)
            created = json.loads(raw)
            self.assertTrue(created["ok"])
            self.assertEqual(created["item"]["tizen_api"], "absent")
            self.assertEqual(created["item"]["getusermedia"], "fail")
            self.assertFalse(created["item"]["live_audio"])
            self.assertEqual(created["item"]["user_agent"], "TizenTest/1.0")
            self.assertTrue(created["item"]["client_ip"])
            self.assertEqual(created["summary"]["tizen_api"], "absent")
            self.assertFalse(created["summary"]["live_audio"])

            status, _h, raw = _request(hub.base + "/api/mic-probe")
            self.assertEqual(status, 200)
            listing = json.loads(raw)
            self.assertEqual(len(listing["items"]), 1)

            status, _h, raw = _request(hub.base + "/api/health")
            self.assertEqual(status, 200)
            health = json.loads(raw)
            self.assertIn("mic_probe", health)
            self.assertEqual(health["mic_probe"]["tizen_api"], "absent")
            self.assertFalse(health["mic_probe"]["live_audio"])
            self.assertTrue(health["mic_probe"]["last_at"])

    def test_rejects_invalid_and_oversized(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-micprobe-bad-"))
        with TestHub(tmp) as hub:
            status, _h, raw = _request(
                hub.base + "/api/mic-probe",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=json.dumps({"tizen_api": "maybe"}).encode(),
            )
            self.assertEqual(status, 400)
            status, _h, raw = _request(
                hub.base + "/api/mic-probe",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=b"{",
            )
            self.assertEqual(status, 400)
            huge = b"{" + (b"x" * (65 * 1024)) + b"}"
            status, _h, raw = _request(
                hub.base + "/api/mic-probe",
                method="POST",
                headers={"Content-Type": "application/json"},
                data=huge,
            )
            self.assertEqual(status, 413)


if __name__ == "__main__":
    unittest.main()
