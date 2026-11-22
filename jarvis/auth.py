"""Shared token checks for the household secret and per-device tokens."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from pathlib import Path
from typing import Iterable

from jarvis.config import Config


def configured_secrets(cfg: Config) -> list[str]:
    secrets_list: list[str] = []
    if cfg.auth_token:
        secrets_list.append(cfg.auth_token)
    for value in cfg.auth_tokens.values():
        if value and value not in secrets_list:
            secrets_list.append(value)
    return secrets_list


def auth_required(cfg: Config) -> bool:
    return bool(configured_secrets(cfg))


def token_matches(provided: str, secrets_list: Iterable[str]) -> bool:
    provided = (provided or "").strip()
    if not provided:
        return False
    for secret in secrets_list:
        if not secret:
            continue
        if secret.startswith("sha256:"):
            digest = hashlib.sha256(provided.encode("utf-8")).hexdigest()
            if hmac.compare_digest(secret[7:], digest):
                return True
        elif hmac.compare_digest(provided, secret):
            return True
    return False


def provided_token(headers, query: dict[str, list[str]]) -> str:  # noqa: ANN001
    header = (headers.get("X-Hub-Token") or "").strip()
    if header:
        return header
    auth = headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return (query.get("token") or [""])[0].strip()


def is_authorized(cfg: Config, headers, query: dict[str, list[str]]) -> bool:  # noqa: ANN001
    secrets_list = configured_secrets(cfg)
    if not secrets_list:
        return True
    return token_matches(provided_token(headers, query), secrets_list)


def stream_secret(cfg: Config) -> bytes:
    """Stable HMAC key for short-lived stream URLs, stored under data_dir."""
    path = cfg.data_dir / "stream_secret"
    if path.is_file():
        return path.read_bytes().strip() or _new_secret(path)
    return _new_secret(path)


def _new_secret(path: Path) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    value = secrets.token_hex(32).encode("ascii")
    path.write_bytes(value)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return value


def sign_stream(cfg: Config, kind: str, item_id: int, ttl: int | None = None) -> tuple[int, str]:
    ttl = int(ttl if ttl is not None else (cfg.tv.get("stream_token_ttl_seconds") or 300))
    exp = int(time.time()) + max(30, ttl)
    msg = f"{kind}:{item_id}:{exp}".encode("ascii")
    sig = hmac.new(stream_secret(cfg), msg, hashlib.sha256).hexdigest()
    return exp, sig


def verify_stream(cfg: Config, kind: str, item_id: int, exp: str | int, sig: str) -> bool:
    try:
        exp_i = int(exp)
    except (TypeError, ValueError):
        return False
    if exp_i < int(time.time()):
        return False
    if not sig:
        return False
    msg = f"{kind}:{item_id}:{exp_i}".encode("ascii")
    expected = hmac.new(stream_secret(cfg), msg, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)
