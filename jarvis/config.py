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
    "public_host": "",
    "voice": {
        "enabled": True,
        "hub_url": "http://127.0.0.1:8765",
        "wake_engine": "openwakeword",
        "wake_model": "hey jarvis",
        "wake_threshold": 0.5,
        "speaker_engine": "ecapa",
        "speaker_threshold": 0.65,
        "enrollments_dir": "data/voice/enrollments",
        "stt_engine": "faster-whisper",
        "stt_model": "small",
        "stt_device": "cuda",
        "stt_compute_type": "int8_float16",
        "tts_engine": "piper",
        "tts_voice": "en_US-lessac-medium",
        "tts_voices_dir": "data/voices",
        "tts_model_path": "",
        "tts_fallback": "sapi",
        "input_device": "",
        "output_device": "",
        "speak_sources": ["hub-mic"],
        "retain_recordings": False,
        "mute": False,
        "command_seconds": 6.0,
        "min_command_seconds": 1.0,
        "client_id": "hub-voice",
    },
    "tts": {
        "enabled": True,
        "max_chars": 500,
        "cache_items": 32,
    },
    "grok": {
        "enabled": True,
        "api_key_env": "XAI_API_KEY",
        "base_url": "https://api.x.ai/v1",
        "model": "grok-4.7",
        "timeout_seconds": 30,
        "history_turns": 8,
        "provider_tools": False,
    },
    "tv": {
        "controller": "null",
        "controller_options": {},
        "stream_token_ttl_seconds": 300,
        # Used by tv.controller = "samsungtvws" (phase 2 stub; see docs/TV_CONTROL.md)
        "ip": "",
        "mac": "",
        "token_file": "data/tv/samsung_token.txt",
        "port": 8002,
        "name": "JarvisHub",
        "broadcast": "255.255.255.255",
    },
    "transfers": {
        "dir": "data/transfers",
        "max_bytes": 1073741824,
        "ttl_hours": 48,
        "allowed_extensions": [".mp4", ".mkv", ".webm", ".mov", ".m4v", ".jpg", ".jpeg", ".png", ".webp", ".mp3", ".wav"],
    },
    "auth_tokens": {
        "phone": "",
        "tv": "",
        "laptop": "",
        "voice": "",
        "transfers": "",
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
        self.public_host: str = str(merged.get("public_host") or "").strip()

        self.voice: dict[str, Any] = dict(merged.get("voice") or {})
        self.voice["enrollments_dir"] = str(_resolve_path(self.voice.get("enrollments_dir") or "data/voice/enrollments"))
        self.voice["tts_voices_dir"] = str(_resolve_path(self.voice.get("tts_voices_dir") or "data/voices"))

        model_path = str(self.voice.get("tts_model_path") or "").strip()
        self.voice["tts_model_path"] = str(_resolve_path(model_path)) if model_path else ""

        self.tts: dict[str, Any] = dict(merged.get("tts") or {})
        self.grok: dict[str, Any] = dict(merged.get("grok") or {})
        self.tv: dict[str, Any] = dict(merged.get("tv") or {})
        self.tv["token_file"] = str(_resolve_path(self.tv.get("token_file") or "data/tv/samsung_token.txt"))
        self.transfers: dict[str, Any] = dict(merged.get("transfers") or {})
        self.transfers["dir"] = str(_resolve_path(self.transfers.get("dir") or "data/transfers"))
        self.auth_tokens: dict[str, str] = {
            str(k): str(v).strip() for k, v in (merged.get("auth_tokens") or {}).items()
        }

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
        Path(self.voice["enrollments_dir"]).mkdir(parents=True, exist_ok=True)
        Path(self.voice["tts_voices_dir"]).mkdir(parents=True, exist_ok=True)
        Path(self.transfers["dir"]).mkdir(parents=True, exist_ok=True)
        (self.data_dir / "tv").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "voice").mkdir(parents=True, exist_ok=True)

    def public_view(self) -> dict[str, Any]:
        """Safe subset for /api/status (no token, no raw paths leaked carelessly)."""
        return {
            "hub_name": self.hub_name,
            "port": self.port,
            "ssl": self.ssl_enabled,
            "wake_word": self.wake_word,
            "media_library_count": len(self.media_paths),
            "auth_required": bool(self.auth_token) or any(self.auth_tokens.values()),
            "grok_configured": bool(os.environ.get(str(self.grok.get("api_key_env") or "XAI_API_KEY"))),
            "tv_controller": str(self.tv.get("controller") or "null"),
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
