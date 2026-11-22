"""Load and validate hub configuration from config.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULTS: dict[str, Any] = {
    "hub_name": "Jarvis",
    "host": "0.0.0.0",
    "port": 8765,
    "data_dir": "data",
    "media_paths": [],
    "auth_token": "",
    "wake_word": "jarvis",
    "log_max_bytes": 1_048_576,
    "log_backup_count": 5,
    "ssl": {
        "enabled": False,
        "certfile": "certs/cert.pem",
        "keyfile": "certs/key.pem",
    },
}


class Config:
    """Typed view over the JSON config file plus defaults."""

    def __init__(self, raw: dict[str, Any], source: Path | None = None) -> None:
        merged = _deep_merge(DEFAULTS, raw)
        self.raw = merged
        self.source = source
        self.hub_name: str = str(merged.get("hub_name") or "Jarvis")
        self.host: str = str(merged.get("host") or "0.0.0.0")
        self.port: int = int(merged.get("port") or 8765)
        self.wake_word: str = str(merged.get("wake_word") or "jarvis").strip()
        self.auth_token: str = str(merged.get("auth_token") or "").strip()
        self.log_max_bytes: int = int(merged.get("log_max_bytes") or 1_048_576)
        self.log_backup_count: int = int(merged.get("log_backup_count") or 5)

        ssl_raw = merged.get("ssl") or {}
        self.ssl_enabled: bool = bool(ssl_raw.get("enabled"))
        self.ssl_certfile = _resolve_path(ssl_raw.get("certfile") or "certs/cert.pem")
        self.ssl_keyfile = _resolve_path(ssl_raw.get("keyfile") or "certs/key.pem")

        data_dir = merged.get("data_dir") or "data"
        self.data_dir = _resolve_path(data_dir)
        self.media_paths = [_resolve_path(p) for p in (merged.get("media_paths") or []) if p]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "media.sqlite3"

    @property
    def log_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def log_file(self) -> Path:
        return self.log_dir / "hub.log"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def public_view(self) -> dict[str, Any]:
        """Safe subset for /api/status (no token, no raw paths leaked carelessly)."""
        return {
            "hub_name": self.hub_name,
            "port": self.port,
            "ssl": self.ssl_enabled,
            "wake_word": self.wake_word,
            "media_library_count": len(self.media_paths),
            "auth_required": bool(self.auth_token),
        }


def _resolve_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    else:
        path = path.resolve()
    return path


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def find_config_path(explicit: str | Path | None = None) -> Path | None:
    if explicit:
        path = Path(explicit).expanduser().resolve()
        return path if path.is_file() else None
    env = os.environ.get("JARVIS_CONFIG")
    if env:
        path = Path(env).expanduser().resolve()
        if path.is_file():
            return path
    candidates = [
        Path.cwd() / "config.json",
        REPO_ROOT / "config.json",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_config(explicit: str | Path | None = None) -> Config:
    path = find_config_path(explicit)
    if path is None:
        cfg = Config({}, source=None)
        cfg.ensure_dirs()
        return cfg
    with path.open("r", encoding="utf-8") as fh:
        raw = json.load(fh)
    if not isinstance(raw, dict):
        raise ValueError(f"Config file {path} must contain a JSON object")
    cfg = Config(raw, source=path)
    cfg.ensure_dirs()
    return cfg
