"""TV playback controllers for phase 2.

This module covers launching a local-library stream or file path on the TV,
plus pause/resume/stop/volume hooks. Power on/off and HDMI input switching
live in jarvis/tv/samsung.py (samsungtvws stub) and jarvis/tv/wol.py.

SamsungTvRootController is a configured-command hook for the external
samsung-tv-root project. Until that project is installed and
tv.controller_options.command (or .endpoint) is set, the controller
reports would_play instead of pretending the TV started playback.
"""

from __future__ import annotations

from pathlib import Path

import logging
import subprocess
from typing import Any, Protocol
from urllib.request import Request, urlopen

LOG = logging.getLogger("jarvis.tv")


class TVController(Protocol):
    name: str

    def status(self) -> dict[str, Any]:
        ...

    def play_path(self, path: str, title: str = "") -> dict[str, Any]:
        ...

    def play_url(self, url: str, title: str = "") -> dict[str, Any]:
        ...

    def pause(self) -> dict[str, Any]:
        ...

    def resume(self) -> dict[str, Any]:
        ...

    def stop(self) -> dict[str, Any]:
        ...

    def set_volume(self, level: int) -> dict[str, Any]:
        ...


def _unsupported(name: str, action: str) -> dict[str, Any]:
    return {
        "ok": False,
        "action": "unsupported",
        "controller": name,
        "error": f"{name} does not implement {action} yet",
    }


class NullController:
    """Default when no TV backend is configured. Keeps phase 1 would_play replies."""

    name = "null"

    def status(self) -> dict[str, Any]:
        return {"ok": True, "controller": self.name, "configured": False, "playing": False}

    def play_path(self, path: str, title: str = "") -> dict[str, Any]:
        return {
            "ok": True,
            "action": "would_play",
            "controller": self.name,
            "path": path,
            "title": title,
            "message": "No TV controller is configured; the file is resolved but not launched.",
        }

    def play_url(self, url: str, title: str = "") -> dict[str, Any]:
        return {
            "ok": True,
            "action": "would_play",
            "controller": self.name,
            "url": url,
            "title": title,
            "message": "No TV controller is configured; the stream URL is ready but not launched.",
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


class SamsungTvRootController:
    """Hook for the external samsung-tv-root project.

    Configure one of:
      tv.controller_options.command  — a shell command with {url}, {path}, {title} placeholders
      tv.controller_options.endpoint — HTTP URL that receives JSON {url, path, title, action}

    Hardware playback is untested until those options point at a real installer.
    """

    name = "samsung-tv-root"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = dict(options or {})
        self.command = str(self.options.get("command") or "").strip()
        self.endpoint = str(self.options.get("endpoint") or "").strip()
        self.timeout = float(self.options.get("timeout_seconds") or 15)

    def configured(self) -> bool:
        return bool(self.command or self.endpoint)

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "controller": self.name,
            "configured": self.configured(),
            "command": bool(self.command),
            "endpoint": self.endpoint or None,
        }

    def play_path(self, path: str, title: str = "") -> dict[str, Any]:
        return self._launch("play_path", url="", path=path, title=title)

    def play_url(self, url: str, title: str = "") -> dict[str, Any]:
        return self._launch("play_url", url=url, path="", title=title)

    def pause(self) -> dict[str, Any]:
        return self._launch("pause", url="", path="", title="")

    def resume(self) -> dict[str, Any]:
        return self._launch("resume", url="", path="", title="")

    def stop(self) -> dict[str, Any]:
        return self._launch("stop", url="", path="", title="")

    def set_volume(self, level: int) -> dict[str, Any]:
        return self._launch("set_volume", url="", path="", title="", extra={"level": int(level)})

    def _launch(
        self,
        action: str,
        url: str,
        path: str,
        title: str,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self.configured():
            return {
                "ok": True,
                "action": "would_play",
                "controller": self.name,
                "url": url or None,
                "path": path or None,
                "title": title,
                "message": (
                    "samsung-tv-root is selected but tv.controller_options.command "
                    "or .endpoint is not set. The stream URL is ready; install the "
                    "root project and point the hook at it before playback will start."
                ),
            }
        payload = {"action": action, "url": url, "path": path, "title": title}
        if extra:
            payload.update(extra)
        try:
            if self.command:
                formatted = self.command.format(url=url, path=path, title=title, action=action)
                completed = subprocess.run(
                    formatted,
                    shell=True,  # command is taken from local config, not from a client
                    check=False,
                    timeout=self.timeout,
                    capture_output=True,
                    text=True,
                )
                if completed.returncode != 0:
                    err = (completed.stderr or completed.stdout or "").strip() or f"exit {completed.returncode}"
                    LOG.warning("samsung-tv-root command failed: %s", err)
                    return {"ok": False, "action": action, "controller": self.name, "error": err}
            if self.endpoint:
                import json

                body = json.dumps(payload).encode("utf-8")
                req = Request(
                    self.endpoint,
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urlopen(req, timeout=self.timeout) as resp:
                    if resp.status >= 400:
                        return {"ok": False, "action": action, "controller": self.name, "error": f"HTTP {resp.status}"}
            return {
                "ok": True,
                "action": "playing" if action.startswith("play") else action,
                "controller": self.name,
                "url": url or None,
                "path": path or None,
                "title": title,
            }
        except Exception as exc:  # noqa: BLE001
            LOG.warning("samsung-tv-root hook failed: %s", exc)
            return {"ok": False, "action": action, "controller": self.name, "error": str(exc)}


class DlnaController:
    """Documented fallback. Phase 2 does not pretend DLNA advertisement works without a real stack."""

    name = "dlna"

    def __init__(self, options: dict[str, Any] | None = None) -> None:
        self.options = dict(options or {})
        self._last_url = ""

    def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "controller": self.name,
            "configured": False,
            "message": (
                "DLNA/UPnP advertisement is stubbed in phase 2. The hub can already "
                "serve HTTP Range streams; a real SSDP/UPnP stack is needed before "
                "the TV will discover them automatically."
            ),
            "last_url": self._last_url or None,
        }

    def play_path(self, path: str, title: str = "") -> dict[str, Any]:
        return {
            "ok": True,
            "action": "would_play",
            "controller": self.name,
            "path": path,
            "title": title,
            "message": "DLNA is not fully implemented; use samsung-tv-root or open the stream URL on the TV.",
        }

    def play_url(self, url: str, title: str = "") -> dict[str, Any]:
        self._last_url = url
        return {
            "ok": True,
            "action": "would_play",
            "controller": self.name,
            "url": url,
            "title": title,
            "message": "DLNA is not fully implemented; the HTTP Range stream URL is ready for a client or a later UPnP layer.",
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



def resolve_tv_controller_name(tv_config: dict[str, Any] | None = None, *, log: Any | None = None) -> str:
    """Pick a controller name from config.

    Treats null / "null" / "none" / "" as unset. When unset, auto-selects
    ``root`` if ``root_exe_path`` (or exe) points at an existing file.
    Never raises.
    """
    tv = dict(tv_config or {})
    raw = tv.get("controller")
    if raw is None:
        key = ""
    else:
        key = str(raw).strip().lower()
    if key in {"", "null", "none", "off"}:
        opts = dict(tv.get("controller_options") or {})
        exe = str(tv.get("root_exe_path") or tv.get("exe") or opts.get("root_exe_path") or opts.get("exe") or "").strip()
        if exe and Path(exe).is_file():
            if log is not None:
                try:
                    log.info("[tv] auto-selected controller=root (found %s)", exe)
                except Exception:  # noqa: BLE001
                    pass
            return "root"
        if exe and log is not None:
            try:
                log.info("[tv] controller unset; root exe not found at %s - using null", exe)
            except Exception:  # noqa: BLE001
                pass
        return "null"
    return key


def build_tv_controller(
    name: str,
    options: dict[str, Any] | None = None,
    tv_config: dict[str, Any] | None = None,
) -> TVController:
    """tv_config is the whole `tv` config block (ip, mac, token_file, ...) for samsungtvws."""
    key = (name or "null").strip().lower()
    if key in {"samsungtvws", "samsung", "samsung-ws", "samsung_tv_ws"}:
        from jarvis.tv.samsung import SamsungTVController  # lazy: keeps the package import light

        merged = {k: v for k, v in (tv_config or {}).items() if k not in {"controller", "controller_options"}}
        merged.update(options or {})
        return SamsungTVController(merged)
    if key in {"samsung-tv-root-hook", "root-hook", "root_hook"}:
        # Legacy phase-2 command/endpoint hook (kept for tests and external scripts).
        return SamsungTvRootController(options)
    if key in {"samsung-tv-root", "samsung_tv_root", "root", "archive-root", "archive_root"}:
        opts = dict(options or {})
        # Old phase-2 hook: only a shell command or HTTP endpoint, no root exe.
        if (opts.get("command") or opts.get("endpoint")) and not (
            opts.get("exe") or opts.get("root_exe_path") or (tv_config or {}).get("root_exe_path")
        ):
            return SamsungTvRootController(opts)
        from jarvis.tv.root import ArchiveRootController

        merged = {k: v for k, v in (tv_config or {}).items() if k not in {"controller", "controller_options"}}
        merged.update(opts)
        key_sender = None
        if merged.get("ip") and merged.get("use_samsungtvws_keys", True):
            try:
                from jarvis.tv.samsung import SamsungTVController

                key_sender = SamsungTVController(merged)
            except Exception:  # noqa: BLE001
                key_sender = None
        return ArchiveRootController(merged, key_sender=key_sender)
    if key in {"dlna", "upnp"}:
        return DlnaController(options)
    if key in {"null", "none", "off", ""}:
        return NullController()
    LOG.warning("Unknown tv.controller %s; using NullController", name)
    return NullController()
