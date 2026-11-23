"""Wake-on-LAN packets and the samsungtvws controller (mocked; no TV needed)."""

from __future__ import annotations

import json
import socket
import sys
import tempfile
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import jarvis.server as server
from jarvis.tv import build_tv_controller, wol
from jarvis.tv.samsung import SamsungTVController
from tests.support import TestHub


class FakeTVWS:
    instances: list["FakeTVWS"] = []
    fail_with: Exception | None = None

    def __init__(self, host, port=8001, token_file=None, timeout=None, name="x", **kw):
        self.host, self.port, self.token_file, self.timeout, self.name = host, port, token_file, timeout, name
        self.keys: list[str] = []
        self.closed = False
        FakeTVWS.instances.append(self)

    def send_key(self, key, times=1, cmd="Click"):
        if FakeTVWS.fail_with:
            raise FakeTVWS.fail_with
        self.keys.append(key)

    def open(self):
        self.keys.append("OPEN")

    def close(self):
        self.closed = True


class WolTests(unittest.TestCase):
    def test_magic_packet_layout(self) -> None:
        pkt = wol.build_magic_packet("AA:bb:CC:dd:EE:ff")
        self.assertEqual(len(pkt), 102)
        self.assertEqual(pkt[:6], b"\xff" * 6)
        mac = bytes.fromhex("aabbccddeeff")
        for i in range(16):
            self.assertEqual(pkt[6 + 6 * i : 12 + 6 * i], mac)

    def test_mac_formats_and_invalid(self) -> None:
        want = bytes.fromhex("0123456789ab")
        for form in ("01:23:45:67:89:ab", "01-23-45-67-89-AB", "0123.4567.89ab", "0123456789ab"):
            self.assertEqual(wol.parse_mac(form), want, form)
        for bad in ("", "01:23:45", "zz:23:45:67:89:ab", "01:23:45:67:89:ab:cd"):
            with self.assertRaises(ValueError):
                wol.parse_mac(bad)

    def test_send_uses_broadcast_udp_and_repeats(self) -> None:
        sent: list[tuple[bytes, tuple]] = []
        opts: list[tuple] = []

        class FakeSock:
            def __init__(self, family, kind):
                self.family, self.kind, self.closed = family, kind, False

            def setsockopt(self, *a):
                opts.append(a)

            def sendto(self, data, addr):
                sent.append((data, addr))

            def close(self):
                self.closed = True

        n = wol.send_magic_packet("01:23:45:67:89:ab", broadcast="192.168.1.255", port=7, repeat=2, sock_factory=FakeSock)
        self.assertEqual(n, 102)
        self.assertEqual(len(sent), 2)
        self.assertEqual(sent[0][1], ("192.168.1.255", 7))
        self.assertEqual(sent[0][0], wol.build_magic_packet("01:23:45:67:89:ab"))
        self.assertIn((socket.SOL_SOCKET, socket.SO_BROADCAST, 1), opts)


class SamsungControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeTVWS.instances = []
        FakeTVWS.fail_with = None
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-tv-"))
        self.wol_calls: list[tuple] = []
        self.opts = {"ip": "192.168.1.60", "mac": "01:23:45:67:89:ab", "token_file": str(self.tmp / "tok" / "t.txt"), "port": 8002}

    def make(self, **extra):
        def fake_wol(mac, broadcast="255.255.255.255", **kw):
            self.wol_calls.append((mac, broadcast))
            return 102

        return SamsungTVController({**self.opts, **extra}, tvws_factory=FakeTVWS, wol_sender=fake_wol, sleep=lambda s: None)

    def test_power_off_sends_key_power_over_8002_with_token_file(self) -> None:
        result = self.make().power_off()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "power_off")
        tv = FakeTVWS.instances[0]
        self.assertEqual(tv.keys, ["KEY_POWER"])
        self.assertEqual((tv.host, tv.port), ("192.168.1.60", 8002))
        self.assertTrue(tv.token_file.endswith("t.txt"))
        self.assertTrue(tv.closed)
        self.assertTrue((self.tmp / "tok").is_dir(), "token directory is created for the library")

    def test_power_on_is_wake_on_lan_not_a_key(self) -> None:
        result = self.make(broadcast="192.168.1.255").power("on")
        self.assertTrue(result["ok"])
        self.assertEqual(result["method"], "wake-on-lan")
        self.assertEqual(self.wol_calls, [("01:23:45:67:89:ab", "192.168.1.255")])
        self.assertEqual(FakeTVWS.instances, [])

    def test_power_on_without_mac_is_a_clear_error(self) -> None:
        result = SamsungTVController({"ip": "1.2.3.4"}, tvws_factory=FakeTVWS).power_on()
        self.assertFalse(result["ok"])
        self.assertIn("tv.mac", result["error"])

    def test_input_next_source_and_cycle_count(self) -> None:
        ctl = self.make()
        self.assertTrue(ctl.set_input("next")["ok"])
        self.assertTrue(ctl.set_input("source")["ok"])
        self.assertTrue(ctl.set_input("hdmi", times=3)["ok"])
        self.assertEqual(FakeTVWS.instances[0].keys, ["KEY_HDMI"])
        self.assertEqual(FakeTVWS.instances[1].keys, ["KEY_SOURCE"])
        self.assertEqual(FakeTVWS.instances[2].keys, ["KEY_HDMI"] * 3)

    def test_direct_hdmi_keys_refused_unless_enabled(self) -> None:
        refused = self.make().set_input("hdmi2")
        self.assertFalse(refused["ok"])
        self.assertIn("unreliable", refused["error"])
        self.assertEqual(FakeTVWS.instances, [])
        allowed = self.make(direct_hdmi_keys=True).set_input("HDMI 2")
        self.assertTrue(allowed["ok"])
        self.assertEqual(FakeTVWS.instances[0].keys, ["KEY_HDMI2"])
        self.assertIn("unreliable", allowed["warning"])
        self.assertFalse(self.make().set_input("vga")["ok"])

    def test_connection_errors_become_error_dicts(self) -> None:
        FakeTVWS.fail_with = OSError("timed out")
        result = self.make().power_off()
        self.assertFalse(result["ok"])
        self.assertIn("timed out", result["error"])
        self.assertTrue(FakeTVWS.instances[0].closed)

    def test_not_configured_is_disabled(self) -> None:
        ctl = SamsungTVController({}, tvws_factory=FakeTVWS)
        self.assertFalse(ctl.configured())
        self.assertFalse(ctl.power_off()["ok"])
        self.assertFalse(ctl.set_input("next")["ok"])
        self.assertEqual(FakeTVWS.instances, [])

    def test_lazy_import_uses_the_real_module_name_and_reports_missing(self) -> None:
        fake_mod = types.ModuleType("samsungtvws")
        fake_mod.SamsungTVWS = FakeTVWS
        saved = sys.modules.get("samsungtvws", "absent")
        try:
            sys.modules["samsungtvws"] = fake_mod
            ctl = SamsungTVController(self.opts)  # no factory: must import samsungtvws lazily
            self.assertTrue(ctl.send_key("KEY_MUTE")["ok"])
            self.assertEqual(FakeTVWS.instances[-1].keys, ["KEY_MUTE"])
            sys.modules["samsungtvws"] = None  # makes `import samsungtvws` raise ImportError
            result = SamsungTVController(self.opts).send_key("KEY_MUTE")
            self.assertFalse(result["ok"])
            self.assertIn("requirements-tv.txt", result["error"])
        finally:
            if saved == "absent":
                sys.modules.pop("samsungtvws", None)
            else:
                sys.modules["samsungtvws"] = saved

    def test_playback_is_delegated_and_defaults_to_would_play(self) -> None:
        ctl = self.make()
        self.assertEqual(ctl.play_url("http://x/y.mp4", "T")["action"], "would_play")
        self.assertEqual(ctl.status()["controller"], "samsungtvws")
        self.assertIn("power_off", ctl.status()["capabilities"])

    def test_build_tv_controller_merges_tv_block(self) -> None:
        ctl = build_tv_controller(
            "samsungtvws",
            {"direct_hdmi_keys": True},
            tv_config={"controller": "samsungtvws", "ip": "10.0.0.9", "mac": "aa:bb:cc:dd:ee:ff", "token_file": "t.txt", "port": 8002},
        )
        self.assertIsInstance(ctl, SamsungTVController)
        self.assertEqual((ctl.ip, ctl.mac, ctl.direct_hdmi_keys, ctl.port), ("10.0.0.9", "aa:bb:cc:dd:ee:ff", True, 8002))


def _post(url, body, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json", **(headers or {})}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class TvEndpointTests(unittest.TestCase):
    def test_endpoints_are_501_when_no_samsung_controller(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-tvapi-"))
        with TestHub(tmp) as hub:
            for path, body in (("/api/tv/power", {"action": "off"}), ("/api/tv/input", {"input": "next"})):
                status, data = _post(hub.base + path, body)
                self.assertEqual(status, 501, data)
                self.assertFalse(data["ok"])
                self.assertIn("samsungtvws", data["error"])

    def test_endpoints_call_the_controller_when_enabled(self) -> None:
        FakeTVWS.instances = []
        FakeTVWS.fail_with = None
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-tvapi-"))
        with TestHub(tmp, auth_token="s3cret") as hub:
            wol_calls = []
            server.TV = SamsungTVController(
                {"ip": "1.2.3.4", "mac": "01:23:45:67:89:ab"},
                tvws_factory=FakeTVWS,
                wol_sender=lambda mac, **kw: wol_calls.append(mac) or 102,
                sleep=lambda s: None,
            )
            self.assertEqual(_post(hub.base + "/api/tv/power", {"action": "off"})[0], 401)
            hdr = {"X-Hub-Token": "s3cret"}
            status, data = _post(hub.base + "/api/tv/power", {"action": "off"}, hdr)
            self.assertEqual((status, data["action"]), (200, "power_off"))
            status, data = _post(hub.base + "/api/tv/power", {"action": "on"}, hdr)
            self.assertEqual((status, data["method"]), (200, "wake-on-lan"))
            self.assertEqual(wol_calls, ["01:23:45:67:89:ab"])
            status, data = _post(hub.base + "/api/tv/input", {"input": "next", "times": 2}, hdr)
            self.assertEqual(status, 200, data)
            self.assertEqual(FakeTVWS.instances[-1].keys, ["KEY_HDMI", "KEY_HDMI"])
            status, data = _post(hub.base + "/api/tv/input", {"input": "hdmi3"}, hdr)
            self.assertEqual(status, 502)
            status, data = _post(hub.base + "/api/tv/power", {"action": "sideways"}, hdr)
            self.assertEqual(status, 502)


if __name__ == "__main__":
    unittest.main()
