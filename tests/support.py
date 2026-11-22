"""Shared helpers for phase 2 tests."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import jarvis.server as server  # noqa: E402
from jarvis.config import Config  # noqa: E402


def write_config(directory: Path, **overrides: Any) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    data = {
        "hub_name": "JarvisTest",
        "host": "127.0.0.1",
        "port": 0,
        "data_dir": str(directory / "data"),
        "media_paths": overrides.pop("media_paths", []),
        "auth_token": overrides.pop("auth_token", ""),
        "auth_tokens": overrides.pop("auth_tokens", {}),
        "public_host": overrides.pop("public_host", ""),
    }
    data.update(overrides)
    path = directory / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def fake_mp4(size: int = 32 * 1024) -> bytes:
    header = b"\x00\x00\x00\x18ftypmp41" + b"\x00" * 8
    if size < len(header):
        size = len(header)
    return header + os.urandom(size - len(header))


def fake_png() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
        + b"\x90wS\xde"
        + b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


class TestHub:
    def __init__(self, directory: Path, **overrides: Any) -> None:
        self.config_path = write_config(directory, **overrides)
        os.environ["JARVIS_CONFIG"] = str(self.config_path)
        self.cfg = Config(json.loads(self.config_path.read_text()), source=self.config_path)
        self.cfg.host = "127.0.0.1"
        self.cfg.ensure_dirs()
        server.CFG = self.cfg
        server.LOG = None
        server.STARTED_AT = time.time()
        server.INDEX, server.PIPELINE, server.TRANSFERS, server.TV, server.MIC_PROBE = server.build_app(self.cfg)
        server.VOICE_STATE.update({"state": "offline", "muted": False, "ts": 0.0, "last_transcript": ""})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.HubHandler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    @property
    def index(self):
        return server.INDEX

    @property
    def transfers(self):
        return server.TRANSFERS

    def start(self) -> "TestHub":
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> "TestHub":
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()
