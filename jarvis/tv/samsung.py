"""samsungtvws controller (phase 2 stub): TV power and input switching.

What this does
  power_off()   KEY_POWER over the TV's WebSocket remote API
  power_on()    Wake-on-LAN magic packet (jarvis/tv/wol.py), because a TV in
                standby has no WebSocket to talk to
  set_input()   KEY_HDMI cycles through inputs, KEY_SOURCE opens the source
                menu. KEY_HDMI1..KEY_HDMI4 are unreliable on 2024 models
                (QN800D included), so they are refused unless
                tv.controller_options.direct_hdmi_keys is true.

Connection: wss on port 8002 (tv.port) with a token_file. The first time,
the TV shows an "Allow" prompt; the token it returns is written to
tv.token_file and reused afterwards.

TV settings that must be on for power_on to work:
  Settings > General > Network > Expert Settings > Power On with Mobile
  Settings > General > Network > Expert Settings > IP Remote
Use Ethernet if you can; Wi-Fi standby is flaky for Wake-on-LAN.

samsungtvws (LGPL-3.0) is imported lazily, only when a key is sent, so the
hub starts and stays stdlib-only without it. Hardware behavior is
unverified until it runs against the real TV; see docs/TV_CONTROL.md.

Not here (phase 3): playback launching, app control, HDMI-CEC tricks,
scheduled power rules.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable

from jarvis.tv import wol
from jarvis.tv.controller import NullController, SamsungTvRootController

LOG = logging.getLogger("jarvis.tv.samsung")

KEY_POWER = "KEY_POWER"
KEY_HDMI = "KEY_HDMI"
KEY_SOURCE = "KEY_SOURCE"

_INPUT_ALIASES_CYCLE = {"next", "cycle", "hdmi", "input", "switch"}
_INPUT_ALIASES_MENU = {"source", "menu", "sources"}


class SamsungTVController:
    name = "samsungtvws"

    def __init__(
        self,
        options: dict[str, Any] | None = None,
        tvws_factory: Callable[..., Any] | None = None,
        wol_sender: Callable[..., int] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        opts = dict(options or {})
        self.options = opts
        self.ip = str(opts.get("ip") or "").strip()
        self.mac = str(opts.get("mac") or "").strip()
        self.token_file = str(opts.get("token_file") or "").strip()
        self.port = int(opts.get("port") or 8002)
        self.remote_name = str(opts.get("name") or "JarvisHub")
        self.broadcast = str(opts.get("broadcast") or "255.255.255.255")
        self.timeout = float(opts.get("timeout_seconds") or 10)
        self.direct_hdmi_keys = bool(opts.get("direct_hdmi_keys"))
        self.key_delay = float(opts.get("key_delay_seconds") or 1.0)
        self._tvws_factory = tvws_factory
        self._wol = wol_sender or wol.send_magic_packet
        self._sleep = sleep
        self._lock = threading.Lock()
        playback = opts.get("playback") or {}
        # Optional: keep samsung-tv-root (or another hook) for playback while this class does power/input.
        self._playback = SamsungTvRootController(playback) if playback else NullController()

    # ---- helpers ----------------------------------------------------------

    def configured(self) -> bool:
        return bool(self.ip)

    def _not_configured(self, action: str, need: str = "tv.ip") -> dict[str, Any]:
        return {
            "ok": False,
            "action": action,
            "controller": self.name,
            "error": f"{need} is not set in config.json; samsungtvws control is disabled until it is.",
        }

    def _connect(self) -> Any:
        factory = self._tvws_factory
        if factory is None:
            try:
                from samsungtvws import SamsungTVWS  # type: ignore[import-not-found]
            except ImportError as exc:
                raise RuntimeError(
                    "samsungtvws is not installed. Run: .venv\\Scripts\\pip install -r requirements-tv.txt"
                ) from exc
            factory = SamsungTVWS
        if self.token_file:
            Path(self.token_file).parent.mkdir(parents=True, exist_ok=True)
        return factory(
            host=self.ip,
            port=self.port,
            token_file=self.token_file or None,
            timeout=self.timeout,
            name=self.remote_name,
        )

    def send_key(self, key: str, times: int = 1) -> dict[str, Any]:
        """Send KEY_* `times` times over wss:8002. Never raises; returns an ok/error dict."""
        if not self.configured():
            return self._not_configured("send_key")
        with self._lock:
            tv = None
            try:
                tv = self._connect()
                for i in range(max(1, int(times))):
                    tv.send_key(key)
                    if i + 1 < times:
                        self._sleep(self.key_delay)
            except Exception as exc:  # noqa: BLE001 - library raises many types (ConnectionFailure, OSError, ...)
                LOG.warning("samsungtvws %s failed: %s", key, exc)
                return {
                    "ok": False,
                    "action": "send_key",
                    "controller": self.name,
                    "key": key,
                    "error": str(exc) or exc.__class__.__name__,
                    "hint": "Is the TV on and on the same network? On first use, accept the prompt on the TV.",
                }
            finally:
                close = getattr(tv, "close", None)
                if callable(close):
                    try:
                        close()
                    except Exception:  # noqa: BLE001
                        pass
        return {"ok": True, "action": "send_key", "controller": self.name, "key": key, "times": int(times)}

    # ---- power ------------------------------------------------------------

    def power_off(self) -> dict[str, Any]:
        result = self.send_key(KEY_POWER)
        result["action"] = "power_off"
        return result

    def power_on(self) -> dict[str, Any]:
        """Wake-on-LAN. KEY_POWER over wss cannot turn a standby TV on."""
        if not self.mac:
            return self._not_configured("power_on", "tv.mac")
        try:
            size = self._wol(self.mac, broadcast=self.broadcast)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Wake-on-LAN failed: %s", exc)
            return {"ok": False, "action": "power_on", "controller": self.name, "error": str(exc)}
        return {
            "ok": True,
            "action": "power_on",
            "controller": self.name,
            "method": "wake-on-lan",
            "bytes": size,
            "note": "Needs 'Power On with Mobile' enabled on the TV; Ethernet is more reliable than Wi-Fi.",
        }

    def power(self, action: str) -> dict[str, Any]:
        action = (action or "").strip().lower()
        if action in {"on", "power_on", "wake"}:
            return self.power_on()
        if action in {"off", "power_off", "standby"}:
            return self.power_off()
        return {"ok": False, "action": "power", "controller": self.name, "error": "action must be 'on' or 'off'"}

    # ---- input ------------------------------------------------------------

    def set_input(self, target: str = "next", times: int = 1) -> dict[str, Any]:
        """'next' presses KEY_HDMI (cycle). 'source' opens the source menu. 'hdmi1'..'hdmi4' need direct_hdmi_keys."""
        key = (target or "next").strip().lower().replace(" ", "")
        if key in _INPUT_ALIASES_CYCLE:
            result = self.send_key(KEY_HDMI, times=times)
        elif key in _INPUT_ALIASES_MENU:
            result = self.send_key(KEY_SOURCE)
        elif key in {"hdmi1", "hdmi2", "hdmi3", "hdmi4"}:
            if not self.direct_hdmi_keys:
                return {
                    "ok": False,
                    "action": "set_input",
                    "controller": self.name,
                    "error": (
                        "KEY_HDMI1..4 are unreliable on 2024 Samsung models. Use input 'next' (KEY_HDMI cycle) "
                        "or 'source' (KEY_SOURCE menu), or set tv.controller_options.direct_hdmi_keys to true to try anyway."
                    ),
                }
            result = self.send_key("KEY_" + key.upper())
            result["warning"] = "KEY_HDMIn is unreliable on 2024 models"
        else:
            return {
                "ok": False,
                "action": "set_input",
                "controller": self.name,
                "error": "input must be one of: next, source, hdmi1..hdmi4",
            }
        result["action"] = "set_input"
        result["input"] = key
        return result

    # ---- TVController protocol -------------------------------------------

    def status(self) -> dict[str, Any]:
        token_present = False
        if self.token_file:
            try:
                token_present = Path(self.token_file).is_file() and Path(self.token_file).stat().st_size > 0
            except OSError:
                token_present = False
        try:
            import importlib.util

            library = importlib.util.find_spec("samsungtvws") is not None
        except Exception:  # noqa: BLE001
            library = False
        return {
            "ok": True,
            "controller": self.name,
            "configured": self.configured(),
            "ip": self.ip or None,
            "wake_on_lan": bool(self.mac),
            "port": self.port,
            "token_present": token_present,
            "library_installed": library or self._tvws_factory is not None,
            "capabilities": ["power_on", "power_off", "input_next", "input_source"],
        }

    def play_path(self, path: str, title: str = "") -> dict[str, Any]:
        return self._playback.play_path(path, title)

    def play_url(self, url: str, title: str = "") -> dict[str, Any]:
        return self._playback.play_url(url, title)

    def pause(self) -> dict[str, Any]:
        return self._playback.pause()

    def resume(self) -> dict[str, Any]:
        return self._playback.resume()

    def stop(self) -> dict[str, Any]:
        return self._playback.stop()

    def set_volume(self, level: int) -> dict[str, Any]:
        return self._playback.set_volume(level)


def main(argv: list[str] | None = None) -> int:
    """Hardware helper: python -m jarvis.tv.samsung --pair | --off | --on | --input next|source | --key KEY_X"""
    import argparse
    import json

    from jarvis.config import load_config

    parser = argparse.ArgumentParser(description="Try the Samsung TV controller from the command line")
    parser.add_argument("--config", help="path to config.json")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--pair", action="store_true", help="connect once (accept the prompt on the TV) and store the token")
    group.add_argument("--off", action="store_true", help="send KEY_POWER")
    group.add_argument("--on", action="store_true", help="send a Wake-on-LAN packet to tv.mac")
    group.add_argument("--input", metavar="next|source|hdmi1..4", help="switch input")
    group.add_argument("--key", metavar="KEY_X", help="send any remote key (e.g. KEY_MUTE)")
    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    options = {**cfg.tv, **(cfg.tv.get("controller_options") or {})}
    tv = SamsungTVController(options)
    if args.pair:
        result = _pair(tv)
    elif args.off:
        result = tv.power_off()
    elif args.on:
        result = tv.power_on()
    elif args.input:
        result = tv.set_input(args.input)
    else:
        result = tv.send_key(args.key)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


def _pair(tv: SamsungTVController) -> dict[str, Any]:
    """Open the connection (token handshake) without pressing a key."""
    if not tv.configured():
        return tv._not_configured("pair")
    try:
        conn = tv._connect()
        conn.open()
        close = getattr(conn, "close", None)
        if callable(close):
            close()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "action": "pair", "controller": tv.name, "error": str(exc) or exc.__class__.__name__}
    return {"ok": True, "action": "pair", "controller": tv.name, "token_file": tv.token_file}


if __name__ == "__main__":
    raise SystemExit(main())
