"""Store the latest TV / browser microphone probe reports."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

MAX_REPORTS = 20
MAX_BODY_BYTES = 64 * 1024
MAX_STRING = 2000
MAX_STEPS = 40

ALLOWED_TIZEN = {"ok", "fail", "absent"}
ALLOWED_GUM = {"ok", "fail", "absent", "insecure"}


def summarize(report: dict[str, Any] | None) -> dict[str, Any]:
    if not report:
        return {
            "last_at": None,
            "tizen_api": "absent",
            "getusermedia": "absent",
            "live_audio": False,
        }
    return {
        "last_at": report.get("received_at") or report.get("client_ts"),
        "tizen_api": report.get("tizen_api") or "absent",
        "getusermedia": report.get("getusermedia") or "absent",
        "live_audio": bool(report.get("live_audio")),
    }


def sanitize(payload: Any, observed_ip: str, user_agent: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("json object required")
    tizen = str(payload.get("tizen_api") or "absent").lower()
    if tizen not in ALLOWED_TIZEN:
        raise ValueError("tizen_api must be ok, fail, or absent")
    gum = str(payload.get("getusermedia") or "absent").lower()
    if gum not in ALLOWED_GUM:
        raise ValueError("getusermedia must be ok, fail, absent, or insecure")
    steps_in = payload.get("steps") or []
    if not isinstance(steps_in, list):
        raise ValueError("steps must be a list")
    steps = [_clip_obj(s) for s in steps_in[:MAX_STEPS]]
    return {
        "received_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "received_epoch": time.time(),
        "client_ts": _clip(payload.get("client_ts")),
        "user_agent": _clip(user_agent or payload.get("user_agent") or ""),
        "client_ip": _clip(observed_ip),
        "reported_ip": _clip(payload.get("client_ip") or ""),
        "secure_context": bool(payload.get("secure_context")),
        "protocol": _clip(payload.get("protocol") or ""),
        "href": _clip(payload.get("href") or ""),
        "tizen_api": tizen,
        "getusermedia": gum,
        "live_audio": bool(payload.get("live_audio")),
        "peak_amplitude": _num(payload.get("peak_amplitude")),
        "notes": _clip(payload.get("notes") or ""),
        "steps": steps,
        "errors": [_clip_obj(e) for e in (payload.get("errors") or [])[:MAX_STEPS]]
        if isinstance(payload.get("errors"), list)
        else [],
    }


def _clip(value: Any, limit: int = MAX_STRING) -> str:
    text = "" if value is None else str(value)
    return text[:limit]


def _num(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip_obj(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for i, (k, v) in enumerate(value.items()):
            if i >= 24:
                break
            key = _clip(k, 80)
            if isinstance(v, (int, float, bool)) or v is None:
                out[key] = v
            else:
                out[key] = _clip(v)
        return out
    if isinstance(value, list):
        return [_clip(v) for v in value[:24]]
    return _clip(value)


class MicProbeStore:
    def __init__(self, path: Path, max_reports: int = MAX_REPORTS) -> None:
        self.path = Path(path)
        self.max_reports = max_reports
        self._lock = threading.Lock()
        self._reports: list[dict[str, Any]] = []
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                self._reports = [r for r in data if isinstance(r, dict)][-self.max_reports :]
        except (OSError, json.JSONDecodeError):
            self._reports = []

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._reports, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, report: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._reports.append(report)
            self._reports = self._reports[-self.max_reports :]
            self._save()
        return report

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(reversed(self._reports))

    def latest(self) -> dict[str, Any] | None:
        with self._lock:
            return dict(self._reports[-1]) if self._reports else None

    def summary(self) -> dict[str, Any]:
        return summarize(self.latest())
