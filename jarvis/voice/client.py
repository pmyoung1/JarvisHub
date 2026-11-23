"""Localhost HTTP + SSE client used by the voice agent to talk to the hub."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any, Iterator

LOG = logging.getLogger("jarvis.voice.client")


class HubClient:
    def __init__(
        self,
        base_url: str,
        token: str = "",
        client_id: str = "hub-voice",
        timeout: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.client_id = client_id
        self.timeout = timeout

    def _headers(self, content_type: str | None = "application/json") -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        if self.token:
            headers["X-Hub-Token"] = self.token
        return headers

    def _request(self, method: str, path: str, body: bytes | None = None, timeout: float | None = None) -> tuple[int, dict[str, Any] | str]:
        req = urllib.request.Request(
            self.base_url + path,
            data=body,
            headers=self._headers(),
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                status = resp.status
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except urllib.error.URLError as exc:
            raise ConnectionError(f"hub unreachable at {self.base_url}: {exc}") from exc
        if raw.startswith("{") or raw.startswith("["):
            try:
                return status, json.loads(raw)
            except json.JSONDecodeError:
                return status, raw
        return status, raw

    def send_command(self, text: str, source: str = "hub-mic") -> dict[str, Any]:
        payload = json.dumps(
            {"text": text, "source": source, "client_id": self.client_id}
        ).encode("utf-8")
        status, data = self._request("POST", "/api/command", payload)
        if isinstance(data, dict):
            data.setdefault("http_status", status)
            return data
        return {"ok": False, "error": str(data), "http_status": status}

    def heartbeat(self, state: str, extra: dict[str, Any] | None = None) -> None:
        body = {"state": state, "client_id": self.client_id, "ts": time.time()}
        if extra:
            body.update(extra)
        try:
            self._request("POST", "/api/voice/status", json.dumps(body).encode("utf-8"), timeout=5)
        except Exception as exc:  # noqa: BLE001
            LOG.debug("voice heartbeat failed: %s", exc)

    def get_voice_status(self) -> dict[str, Any]:
        status, data = self._request("GET", "/api/voice/status", None, timeout=5)
        return data if isinstance(data, dict) else {"ok": False, "http_status": status}

    def set_mute(self, muted: bool) -> dict[str, Any]:
        payload = json.dumps({"muted": bool(muted), "client_id": self.client_id}).encode("utf-8")
        status, data = self._request("POST", "/api/voice/mute", payload)
        if isinstance(data, dict):
            return data
        return {"ok": False, "error": str(data), "http_status": status}

    def iter_events(self) -> Iterator[dict[str, Any]]:
        """Yield parsed SSE events (type, data) until the connection drops."""
        req = urllib.request.Request(
            self.base_url + "/api/events",
            headers={**self._headers(None), "Accept": "text/event-stream"},
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            event = "message"
            data_lines: list[str] = []
            while True:
                raw = resp.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace")
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
                    continue
                if line.strip() == "":
                    if data_lines:
                        blob = "\n".join(data_lines)
                        try:
                            parsed = json.loads(blob)
                        except json.JSONDecodeError:
                            parsed = {"raw": blob}
                        yield {"type": event, "data": parsed}
                    event = "message"
                    data_lines = []
