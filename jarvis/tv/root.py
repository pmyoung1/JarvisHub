"""samsung-tv-root archive-root backend: shell out to the release executable.

Bridges phase 2 playback and phase 3 TV control for a TV that already has a
working archive-root session (QN75QN800DFXZA at the household Hub PC).

Exact host command pattern (Windows):

  <exe> --sdb <sdb_path> archive-root root <tv_ip> --command "<cmd>"

Arguments are always a list (never shell=True). OpenSSL must be on PATH for
the release binary; see docs/TV_ROOT.md.

Play is best-effort: the file is pushed with sdb into a known TV directory,
then a Tizen media-player launch command is run through the root shell. The
exact player invocation has not been verified on the live TV from this build
box — the full command string is always logged under [tv] so it can be
confirmed on the Hub PC.
"""

from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any, Callable

from jarvis.tv import wol
from jarvis.tv.controller import _unsupported

LOG = logging.getLogger("jarvis.tv")

DEFAULT_EXE = r"C:\samsung-tv-root-release\samsung-tv-root-0.0.13-windows-x86_64\samsung-tv-root.exe"
DEFAULT_SDB = r"C:\sdb\data\tools\sdb.exe"
DEFAULT_REMOTE_DIR = "/home/owner/share/tmp/jarvis"
DEFAULT_PLAYER_APP = "org.tizen.video-player-tv"
DEFAULT_RUNTIME = "6.0"
SDB_PORT = 26101

# Best-effort Tizen launch. Not verified on the QN75QN800D from this box.
# TODO(hardware): confirm on the Hub PC which of these the Tizen 6.0 / TV
# firmware accepts, and whether the player needs a file:// URI or a bare path.
PLAY_COMMAND_TEMPLATE = (
    "mkdir -p {remote_dir} && "
    "launch_app {player_app} {remote_path}"
)


class RootShell:
    """Thin wrapper around the samsung-tv-root.exe archive-root root command."""

    def __init__(
        self,
        exe: str = DEFAULT_EXE,
        sdb: str = DEFAULT_SDB,
        ip: str = "",
        runtime: str = DEFAULT_RUNTIME,
        timeout_seconds: float = 60.0,
        runner: Callable[..., Any] | None = None,
        openssl_bin: str = r"C:\Program Files\OpenSSL-Win64\bin",
    ) -> None:
        self.exe = str(exe or DEFAULT_EXE)
        self.sdb = str(sdb or DEFAULT_SDB)
        self.ip = str(ip or "").strip()
        self.runtime = str(runtime or DEFAULT_RUNTIME).strip() or DEFAULT_RUNTIME
        self.timeout_seconds = float(timeout_seconds or 60)
        self._runner = runner or subprocess.run
        self.openssl_bin = str(openssl_bin or "").strip()

    def configured(self) -> bool:
        return bool(self.exe and self.sdb and self.ip)

    def build_root_args(self, command: str) -> list[str]:
        """Build the argv for archive-root root <ip> --command <cmd>.

        --sdb is a global flag and MUST come before the archive-root subcommand
        (samsung-tv-root's own CLI quirk; see docs/TV_ROOT.md). Runtime is kept
        as metadata for status/logging; the required host pattern does not pass it.
        """
        if not self.ip:
            raise ValueError("tv IP is not set")
        return [self.exe, "--sdb", self.sdb, "archive-root", "root", self.ip, "--command", command]

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.openssl_bin and Path(self.openssl_bin).is_dir():
            env["PATH"] = self.openssl_bin + os.pathsep + env.get("PATH", "")
        return env

    def run(self, command: str) -> dict[str, Any]:
        """Run one root command. Never raises; returns a structured result."""
        if not self.configured():
            return {
                "ok": False,
                "action": "root_command",
                "error": "TV root is not configured (need tv.ip / tv_ip, tv_root_exe_path, and tv_sdb_path).",
            }
        if self._runner is subprocess.run and not Path(self.exe).is_file():
            return {
                "ok": False,
                "action": "root_command",
                "error": f"samsung-tv-root executable not found at {self.exe}",
                "hint": "Install the 0.0.13 Windows release or set tv_root_exe_path / tv.root_exe_path.",
            }
        try:
            args = self.build_root_args(command)
        except ValueError as exc:
            return {"ok": False, "action": "root_command", "error": str(exc)}

        LOG.info("[tv] root exec: %s", _format_args(args))
        started = time.monotonic()
        try:
            completed = self._runner(
                args,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=self._env(),
                check=False,
            )
        except FileNotFoundError as exc:
            LOG.warning("[tv] root exe missing: %s", exc)
            return {
                "ok": False,
                "action": "root_command",
                "error": f"samsung-tv-root executable not found: {exc}",
                "args": args,
            }
        except subprocess.TimeoutExpired as exc:
            LOG.warning("[tv] root timed out after %ss: %s", self.timeout_seconds, command)
            return {
                "ok": False,
                "action": "root_command",
                "error": f"TV root command timed out after {self.timeout_seconds:.0f}s (TV off, unreachable, or SDB stuck).",
                "args": args,
                "stdout": (exc.stdout or "") if isinstance(exc.stdout, str) else "",
                "stderr": (exc.stderr or "") if isinstance(exc.stderr, str) else "",
            }
        except OSError as exc:
            LOG.warning("[tv] root OSError: %s", exc)
            return {"ok": False, "action": "root_command", "error": str(exc), "args": args}

        elapsed = round(time.monotonic() - started, 3)
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        ok = completed.returncode == 0
        LOG.info(
            "[tv] root rc=%s elapsed=%.3fs cmd=%r stdout=%r stderr=%r",
            completed.returncode,
            elapsed,
            command,
            _clip(stdout),
            _clip(stderr),
        )
        result: dict[str, Any] = {
            "ok": ok,
            "action": "root_command",
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "elapsed_seconds": elapsed,
            "args": args,
            "command": command,
            "tv_ip": self.ip,
            "runtime": self.runtime,
        }
        if not ok:
            detail = stderr or stdout or f"exit {completed.returncode}"
            result["error"] = (
                f"TV root command failed ({detail}). "
                "Is the TV on, Developer Mode still enabled, and SDB reachable?"
            )
        return result

    def build_sdb_push_args(self, local_path: str, remote_path: str) -> list[str]:
        return [self.sdb, "-s", f"{self.ip}:{SDB_PORT}", "push", local_path, remote_path]

    def push(self, local_path: str, remote_path: str) -> dict[str, Any]:
        """Push a host file onto the TV via sdb. Never raises."""
        if not self.ip or not self.sdb:
            return {"ok": False, "action": "sdb_push", "error": "tv IP or sdb path is not set"}
        local = Path(local_path)
        if self._runner is subprocess.run and not local.is_file():
            return {"ok": False, "action": "sdb_push", "error": f"local file not found: {local}"}
        if self._runner is subprocess.run and not Path(self.sdb).is_file():
            return {"ok": False, "action": "sdb_push", "error": f"sdb not found at {self.sdb}"}
        args = self.build_sdb_push_args(str(local), remote_path)
        LOG.info("[tv] sdb push: %s", _format_args(args))
        try:
            completed = self._runner(
                args,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=self._env(),
                check=False,
            )
        except FileNotFoundError as exc:
            return {"ok": False, "action": "sdb_push", "error": f"sdb not found: {exc}", "args": args}
        except subprocess.TimeoutExpired:
            return {
                "ok": False,
                "action": "sdb_push",
                "error": f"sdb push timed out after {self.timeout_seconds:.0f}s (TV off or unreachable).",
                "args": args,
            }
        except OSError as exc:
            return {"ok": False, "action": "sdb_push", "error": str(exc), "args": args}
        stdout = (completed.stdout or "").strip()
        stderr = (completed.stderr or "").strip()
        # sdb can exit 0 while printing "download failed" — treat that as failure.
        blob = f"{stdout}\n{stderr}".lower()
        ok = completed.returncode == 0 and "failed" not in blob and "error" not in blob
        LOG.info(
            "[tv] sdb push rc=%s ok=%s stdout=%r stderr=%r",
            completed.returncode,
            ok,
            _clip(stdout),
            _clip(stderr),
        )
        result: dict[str, Any] = {
            "ok": ok,
            "action": "sdb_push",
            "returncode": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "args": args,
            "local": str(local),
            "remote": remote_path,
        }
        if not ok:
            result["error"] = stderr or stdout or f"sdb push failed (exit {completed.returncode})"
        return result


class ArchiveRootController:
    """TVController that plays files through samsung-tv-root archive-root.

    Power on uses Wake-on-LAN. Power off and HDMI input reuse the samsungtvws
    key path when available (same KEY_POWER / KEY_HDMI / KEY_SOURCE caveats as
    jarvis/tv/samsung.py); otherwise they fall back to a best-effort root
    shell command that is logged for on-device verification.
    """

    name = "root"
    prefers_path = True

    def __init__(
        self,
        options: dict[str, Any] | None = None,
        shell: RootShell | None = None,
        key_sender: Any | None = None,
        wol_sender: Callable[..., int] | None = None,
    ) -> None:
        opts = dict(options or {})
        self.options = opts
        self.ip = str(opts.get("ip") or "").strip()
        self.mac = str(opts.get("mac") or "").strip()
        self.broadcast = str(opts.get("broadcast") or "255.255.255.255")
        self.remote_dir = str(opts.get("remote_dir") or DEFAULT_REMOTE_DIR).rstrip("/")
        self.player_app = str(opts.get("player_app") or DEFAULT_PLAYER_APP)
        self.play_template = str(opts.get("play_command_template") or PLAY_COMMAND_TEMPLATE)
        self.skip_push_if_remote = bool(opts.get("skip_push_if_remote", False))
        self.direct_hdmi_keys = bool(opts.get("direct_hdmi_keys"))
        self.shell = shell or RootShell(
            exe=str(opts.get("exe") or opts.get("root_exe_path") or DEFAULT_EXE),
            sdb=str(opts.get("sdb") or opts.get("sdb_path") or DEFAULT_SDB),
            ip=self.ip,
            runtime=str(opts.get("runtime") or DEFAULT_RUNTIME),
            timeout_seconds=float(opts.get("timeout_seconds") or 90),
            openssl_bin=str(opts.get("openssl_bin") or r"C:\Program Files\OpenSSL-Win64\bin"),
        )
        self._key_sender = key_sender
        self._wol = wol_sender or wol.send_magic_packet

    def configured(self) -> bool:
        return self.shell.configured()

    def status(self) -> dict[str, Any]:
        exe_present = Path(self.shell.exe).is_file()
        sdb_present = Path(self.shell.sdb).is_file()
        return {
            "ok": True,
            "controller": self.name,
            "configured": self.configured(),
            "ip": self.ip or None,
            "mac": self.mac or None,
            "wake_on_lan": bool(self.mac),
            "runtime": self.shell.runtime,
            "exe": self.shell.exe,
            "exe_present": exe_present,
            "sdb": self.shell.sdb,
            "sdb_present": sdb_present,
            "remote_dir": self.remote_dir,
            "player_app": self.player_app,
            "capabilities": ["play_path", "power_on", "power_off", "input_next", "input_source"],
            "note": "Play launch command is best-effort until verified on the live TV.",
        }

    # ---- playback ---------------------------------------------------------

    def remote_path_for(self, local_path: str) -> str:
        name = Path(local_path).name or "media.bin"
        # Keep the name POSIX-safe for the TV filesystem.
        safe = "".join(ch if ch.isalnum() or ch in "._- " else "_" for ch in name).strip() or "media.bin"
        return f"{self.remote_dir}/{safe}"

    def build_play_command(self, remote_path: str) -> str:
        return self.play_template.format(
            remote_dir=self.remote_dir,
            player_app=self.player_app,
            remote_path=remote_path,
        )

    def play_path(self, path: str, title: str = "") -> dict[str, Any]:
        if not self.configured():
            return {
                "ok": False,
                "action": "would_play",
                "controller": self.name,
                "path": path,
                "title": title,
                "error": "TV root is not configured (set tv.controller to 'root' and tv_ip / tv_root_exe_path / tv_sdb_path).",
            }
        local = Path(path)
        if not local.is_file():
            return {
                "ok": False,
                "action": "would_play",
                "controller": self.name,
                "path": path,
                "title": title,
                "error": f"Media file is missing on the Hub PC: {path}",
            }
        remote = self.remote_path_for(str(local))
        push = self.shell.push(str(local), remote)
        if not push.get("ok"):
            return {
                "ok": False,
                "action": "would_play",
                "controller": self.name,
                "path": path,
                "title": title,
                "remote": remote,
                "push": push,
                "error": push.get("error") or "sdb push failed",
            }
        command = self.build_play_command(remote)
        LOG.info("[tv] play command (best-effort, unverified on live TV): %s", command)
        root = self.shell.run(command)
        result: dict[str, Any] = {
            "ok": bool(root.get("ok")),
            "action": "playing" if root.get("ok") else "would_play",
            "controller": self.name,
            "path": str(local),
            "title": title,
            "remote": remote,
            "play_command": command,
            "push": {"ok": True, "remote": remote},
            "root": {k: root.get(k) for k in ("ok", "returncode", "stdout", "stderr", "error", "elapsed_seconds")},
            "todo": (
                "TODO(hardware): confirm on the QN75QN800D that "
                f"`{command}` actually starts playback. Adjust tv.controller_options.play_command_template if needed."
            ),
        }
        if not root.get("ok"):
            result["error"] = root.get("error") or "root play command failed"
            result["message"] = result["error"]
        else:
            result["message"] = f"Pushed to {remote} and ran the Tizen launch command (best-effort)."
        return result

    def play_url(self, url: str, title: str = "") -> dict[str, Any]:
        # Root playback prefers a local file. A bare URL is opened only as a last resort.
        if not self.configured():
            return {
                "ok": False,
                "action": "would_play",
                "controller": self.name,
                "url": url,
                "title": title,
                "error": "TV root is not configured.",
            }
        command = f"launch_app {self.player_app} {url}"
        LOG.info("[tv] play_url command (best-effort): %s", command)
        root = self.shell.run(command)
        return {
            "ok": bool(root.get("ok")),
            "action": "playing" if root.get("ok") else "would_play",
            "controller": self.name,
            "url": url,
            "title": title,
            "play_command": command,
            "root": {k: root.get(k) for k in ("ok", "returncode", "stdout", "stderr", "error")},
            "error": None if root.get("ok") else (root.get("error") or "root play_url failed"),
            "todo": "TODO(hardware): URL launch via the stock video player is unverified; prefer play_path.",
        }

    # ---- power / input ----------------------------------------------------

    def power_on(self) -> dict[str, Any]:
        if not self.mac:
            return {
                "ok": False,
                "action": "power_on",
                "controller": self.name,
                "error": "tv.mac is not set; Wake-on-LAN needs the TV's Ethernet MAC.",
            }
        try:
            size = self._wol(self.mac, broadcast=self.broadcast)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("[tv] Wake-on-LAN failed: %s", exc)
            return {"ok": False, "action": "power_on", "controller": self.name, "error": str(exc)}
        LOG.info("[tv] Wake-on-LAN sent to %s (%s bytes)", self.mac, size)
        return {
            "ok": True,
            "action": "power_on",
            "controller": self.name,
            "method": "wake-on-lan",
            "bytes": size,
            "note": "Needs 'Power On with Mobile' on the TV; Ethernet is more reliable than Wi-Fi.",
        }

    def power_off(self) -> dict[str, Any]:
        if self._key_sender is not None:
            result = self._key_sender.send_key("KEY_POWER")
            result = dict(result)
            result["action"] = "power_off"
            result["controller"] = self.name
            result["method"] = "samsungtvws"
            return result
        # Best-effort root fallback (unverified): try a common key-injection helper if present.
        command = "input keyevent 26 || echo KEY_POWER_UNVERIFIED"
        root = self.shell.run(command)
        return {
            "ok": bool(root.get("ok")),
            "action": "power_off",
            "controller": self.name,
            "method": "root",
            "play_command": command,
            "error": None if root.get("ok") else (root.get("error") or "power_off failed"),
            "todo": "TODO(hardware): prefer samsungtvws KEY_POWER; root keyevent 26 is unverified on Tizen TV.",
        }

    def power(self, action: str) -> dict[str, Any]:
        action = (action or "").strip().lower()
        if action in {"on", "power_on", "wake"}:
            return self.power_on()
        if action in {"off", "power_off", "standby"}:
            return self.power_off()
        return {"ok": False, "action": "power", "controller": self.name, "error": "action must be 'on' or 'off'"}

    def set_input(self, target: str = "next", times: int = 1) -> dict[str, Any]:
        key = (target or "next").strip().lower().replace(" ", "")
        if self._key_sender is not None:
            if key in {"next", "cycle", "hdmi", "input", "switch"}:
                result = self._key_sender.send_key("KEY_HDMI", times=times)
            elif key in {"source", "menu", "sources"}:
                result = self._key_sender.send_key("KEY_SOURCE")
            elif key in {"hdmi1", "hdmi2", "hdmi3", "hdmi4"}:
                if not self.direct_hdmi_keys:
                    return {
                        "ok": False,
                        "action": "set_input",
                        "controller": self.name,
                        "error": (
                            "KEY_HDMI1..4 are unreliable on 2024 Samsung models. Use input 'next' "
                            "or 'source', or set tv.controller_options.direct_hdmi_keys to true."
                        ),
                    }
                result = self._key_sender.send_key("KEY_" + key.upper())
                result["warning"] = "KEY_HDMIn is unreliable on 2024 models"
            else:
                return {
                    "ok": False,
                    "action": "set_input",
                    "controller": self.name,
                    "error": "input must be one of: next, source, hdmi1..hdmi4",
                }
            result = dict(result)
            result["action"] = "set_input"
            result["input"] = key
            result["controller"] = self.name
            result["method"] = "samsungtvws"
            return result
        return {
            "ok": False,
            "action": "set_input",
            "controller": self.name,
            "error": (
                "Input switching needs samsungtvws (install requirements-tv.txt and ensure the TV "
                "has IP Remote on). Root-only input is not wired yet."
            ),
        }

    def pause(self) -> dict[str, Any]:
        return _unsupported(self.name, "pause")

    def resume(self) -> dict[str, Any]:
        return _unsupported(self.name, "resume")

    def stop(self) -> dict[str, Any]:
        return _unsupported(self.name, "stop")

    def set_volume(self, level: int) -> dict[str, Any]:
        del level
        return _unsupported(self.name, "set_volume")


def _format_args(args: list[str]) -> str:
    return " ".join(shlex.quote(a) for a in args)


def _clip(text: str, limit: int = 400) -> str:
    text = text or ""
    return text if len(text) <= limit else text[: limit - 3] + "..."
