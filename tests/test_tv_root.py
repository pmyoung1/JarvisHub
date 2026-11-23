"""samsung-tv-root archive-root backend: mocked subprocess, no real TV."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.config import Config
from jarvis.specialists.media import MediaSpecialist
from jarvis.specialists.tv import TvSpecialist
from jarvis.tv import build_tv_controller
from jarvis.tv.root import PLAY_COMMAND_TEMPLATE, ArchiveRootController, RootShell


class FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RootShellTests(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[dict] = []

        def runner(args, **kwargs):
            self.calls.append({"args": list(args), "kwargs": kwargs})
            assert kwargs.get("check") is False
            assert "shell" not in kwargs or kwargs["shell"] is False
            assert kwargs.get("capture_output") is True
            return FakeCompleted(0, "uid=0(owner)", "")

        self.runner = runner
        self.shell = RootShell(
            exe=r"C:\samsung-tv-root-release\samsung-tv-root.exe",
            sdb=r"C:\sdb\data\tools\sdb.exe",
            ip="10.0.0.7",
            runtime="6.0",
            runner=runner,
        )

    def test_build_root_args_match_required_pattern(self) -> None:
        args = self.shell.build_root_args("id; uname -a")
        self.assertEqual(
            args,
            [
                r"C:\samsung-tv-root-release\samsung-tv-root.exe",
                "--sdb",
                r"C:\sdb\data\tools\sdb.exe",
                "archive-root",
                "root",
                "10.0.0.7",
                "--command",
                "id; uname -a",
            ],
        )
        # --sdb must come before archive-root
        self.assertLess(args.index("--sdb"), args.index("archive-root"))

    def test_run_success_logs_structured_result(self) -> None:
        result = self.shell.run("id")
        self.assertTrue(result["ok"])
        self.assertEqual(result["returncode"], 0)
        self.assertEqual(result["stdout"], "uid=0(owner)")
        self.assertEqual(self.calls[0]["args"][-1], "id")
        self.assertIs(self.calls[0]["kwargs"].get("shell", False), False)

    def test_nonzero_exit_is_error_not_exception(self) -> None:
        def runner(args, **kwargs):
            return FakeCompleted(1, "", "connection refused")

        shell = RootShell(exe="e", sdb="s", ip="10.0.0.7", runner=runner)
        result = shell.run("id")
        self.assertFalse(result["ok"])
        self.assertIn("connection refused", result["error"])
        self.assertEqual(result["returncode"], 1)

    def test_timeout_is_error_not_exception(self) -> None:
        def runner(args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args, timeout=5)

        shell = RootShell(exe="e", sdb="s", ip="10.0.0.7", timeout_seconds=5, runner=runner)
        result = shell.run("id")
        self.assertFalse(result["ok"])
        self.assertIn("timed out", result["error"])

    def test_missing_exe_is_error_not_exception(self) -> None:
        def runner(args, **kwargs):
            raise FileNotFoundError(2, "No such file", args[0])

        shell = RootShell(exe=r"C:\missing\samsung-tv-root.exe", sdb="s", ip="10.0.0.7", runner=runner)
        result = shell.run("id")
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"].lower())

    def test_unconfigured_shell_does_not_call_subprocess(self) -> None:
        shell = RootShell(exe="e", sdb="s", ip="", runner=self.runner)
        result = shell.run("id")
        self.assertFalse(result["ok"])
        self.assertEqual(self.calls, [])

    def test_sdb_push_args_and_failure_detection(self) -> None:
        args = self.shell.build_sdb_push_args(r"D:\Movies\x.mp4", "/home/owner/share/tmp/jarvis/x.mp4")
        self.assertEqual(
            args,
            [r"C:\sdb\data\tools\sdb.exe", "-s", "10.0.0.7:26101", "push", r"D:\Movies\x.mp4", "/home/owner/share/tmp/jarvis/x.mp4"],
        )

        def runner(args, **kwargs):
            return FakeCompleted(0, "download failed[16]", "")

        shell = RootShell(exe="e", sdb="s", ip="10.0.0.7", runner=runner)
        result = shell.push(r"D:\Movies\x.mp4", "/tmp/x.mp4")
        self.assertFalse(result["ok"])
        self.assertIn("failed", result["error"].lower())


class ArchiveRootControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="jarvis-root-"))
        self.media = self.tmp / "Inception (2010).mp4"
        self.media.write_bytes(b"\x00\x00\x00\x18ftypmp41" + b"\x00" * 64)
        self.calls: list[list[str]] = []

        def runner(args, **kwargs):
            self.calls.append(list(args))
            return FakeCompleted(0, "ok", "")

        self.shell = RootShell(exe="exe", sdb="sdb", ip="10.0.0.7", runner=runner)
        self.ctl = ArchiveRootController(
            {"ip": "10.0.0.7", "mac": "aa:bb:cc:dd:ee:ff", "remote_dir": "/home/owner/share/tmp/jarvis"},
            shell=self.shell,
            wol_sender=lambda mac, broadcast="255.255.255.255", **kw: 102,
        )

    def test_play_path_pushes_then_runs_launch_command(self) -> None:
        result = self.ctl.play_path(str(self.media), title="Inception (2010)")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["action"], "playing")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0][1:4], ["-s", "10.0.0.7:26101", "push"])
        self.assertEqual(self.calls[0][4], str(self.media))
        remote = self.calls[0][5]
        self.assertTrue(remote.startswith("/home/owner/share/tmp/jarvis/"))
        self.assertEqual(self.calls[1][0], "exe")
        self.assertEqual(self.calls[1][3:6], ["archive-root", "root", "10.0.0.7"])
        command = self.calls[1][-1]
        self.assertEqual(command, PLAY_COMMAND_TEMPLATE.format(remote_dir="/home/owner/share/tmp/jarvis", player_app="org.tizen.video-player-tv", remote_path=remote))
        self.assertEqual(result["play_command"], command)
        self.assertIn("TODO(hardware)", result["todo"])

    def test_play_path_push_failure_does_not_launch(self) -> None:
        def runner(args, **kwargs):
            self.calls.append(list(args))
            if "push" in args:
                return FakeCompleted(1, "", "device offline")
            return FakeCompleted(0, "ok", "")

        ctl = ArchiveRootController({"ip": "10.0.0.7"}, shell=RootShell(exe="e", sdb="s", ip="10.0.0.7", runner=runner))
        result = ctl.play_path(str(self.media), title="X")
        self.assertFalse(result["ok"])
        self.assertIn("device offline", result["error"])
        self.assertEqual(len(self.calls), 1)  # push only

    def test_power_on_is_wake_on_lan(self) -> None:
        result = self.ctl.power("on")
        self.assertTrue(result["ok"])
        self.assertEqual(result["method"], "wake-on-lan")
        self.assertEqual(self.calls, [])  # no root call

    def test_power_off_uses_key_sender_when_present(self) -> None:
        class Keys:
            def send_key(self, key, times=1):
                return {"ok": True, "action": "send_key", "key": key, "times": times}

        ctl = ArchiveRootController({"ip": "10.0.0.7"}, shell=self.shell, key_sender=Keys())
        result = ctl.power_off()
        self.assertTrue(result["ok"])
        self.assertEqual(result["method"], "samsungtvws")
        self.assertEqual(result["key"], "KEY_POWER")

    def test_build_tv_controller_root_alias(self) -> None:
        ctl = build_tv_controller(
            "root",
            {},
            tv_config={"ip": "10.0.0.7", "root_exe_path": "exe", "sdb_path": "sdb", "runtime": "6.0", "use_samsungtvws_keys": False},
        )
        self.assertIsInstance(ctl, ArchiveRootController)
        self.assertEqual(ctl.shell.ip, "10.0.0.7")
        self.assertTrue(ctl.prefers_path)

    def test_legacy_hook_still_selected_when_only_endpoint_set(self) -> None:
        from jarvis.tv.controller import SamsungTvRootController

        ctl = build_tv_controller("samsung-tv-root", {"endpoint": "http://127.0.0.1/play"})
        self.assertIsInstance(ctl, SamsungTvRootController)


class SpecialistTests(unittest.TestCase):
    def test_media_prefers_path_and_surfaces_errors(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="jarvis-media-root-"))
        media = tmp / "Film.mp4"
        media.write_bytes(b"\x00\x00\x00\x18ftypmp41" + b"\x00" * 32)

        class Index:
            def resolve_title(self, title):
                return {"id": 1, "title": "Film", "year": 2020, "path": str(media)}

        class TV:
            name = "root"
            prefers_path = True

            def play_path(self, path, title=""):
                return {"ok": False, "action": "would_play", "controller": "root", "error": "TV unreachable", "path": path}

            def play_url(self, url, title=""):
                raise AssertionError("play_url should not be used when prefers_path is set")

        specialist = MediaSpecialist(index=Index(), tv=TV(), stream_url_factory=lambda *a, **k: "http://hub/stream")
        out = specialist.handle("play film on the tv", {})
        self.assertIn("could not start it on the TV", out["reply"])
        self.assertIn("TV unreachable", out["reply"])
        self.assertEqual(out["data"]["error"], "TV unreachable")

    def test_tv_specialist_on_off_input(self) -> None:
        class TV:
            name = "root"

            def power(self, action):
                return {"ok": True, "action": f"power_{action}", "method": "wake-on-lan" if action == "on" else "samsungtvws"}

            def set_input(self, target="next", times=1):
                return {"ok": True, "action": "set_input", "input": target}

        specialist = TvSpecialist(tv=TV())
        self.assertTrue(specialist.can_handle("tv on", {}))
        self.assertIn("Wake-on-LAN", specialist.handle("turn the tv on", {})["reply"])
        self.assertIn("Turning the TV off", specialist.handle("tv off", {})["reply"])
        self.assertIn("Switching", specialist.handle("next input", {})["reply"])
        err = TvSpecialist(tv=None).handle("tv on", {})
        self.assertIn("No TV controller", err["reply"])

    def test_config_top_level_tv_keys(self) -> None:
        cfg = Config(
            {
                "tv_ip": "10.0.0.7",
                "tv_sdb_path": r"C:\sdb\data\tools\sdb.exe",
                "tv_root_exe_path": r"C:\samsung-tv-root-release\samsung-tv-root.exe",
                "tv_runtime": "6.0",
                "tv": {"controller": "root", "mac": "11:22:33:44:55:66"},
            }
        )
        self.assertEqual(cfg.tv_ip, "10.0.0.7")
        self.assertEqual(cfg.tv["ip"], "10.0.0.7")
        self.assertEqual(cfg.tv["controller_options"]["exe"], r"C:\samsung-tv-root-release\samsung-tv-root.exe")
        self.assertEqual(cfg.tv["controller_options"]["mac"], "11:22:33:44:55:66")
        self.assertEqual(cfg.tv_runtime, "6.0")


if __name__ == "__main__":
    unittest.main()
