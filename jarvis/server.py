"""Local-network HTTP server for the dashboard and the command / media APIs."""

from __future__ import annotations

import json
import mimetypes
import posixpath
import re
import ssl
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from jarvis import __version__
from jarvis.auth import is_authorized, sign_stream, verify_stream
from jarvis.config import Config, load_config
from jarvis.events import EventBus
from jarvis.http_range import send_range
from jarvis.logging_setup import setup_logging
from jarvis.media.index import MediaIndex
from jarvis.mic_probe import MAX_BODY_BYTES as MIC_PROBE_MAX, MicProbeStore, sanitize as sanitize_mic_probe
from jarvis.multipart import parse_multipart
from jarvis.pipeline import CommandRequest, Pipeline
from jarvis.router import build_default_router
from jarvis.specialists.grok import GrokSpecialist
from jarvis.transfers import TransferStore
from jarvis.tts_service import TtsService, TtsUnavailable
from jarvis.tv import build_tv_controller

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
STARTED_AT = time.time()

# Shared process state set in main().
CFG: Config
LOG = None
INDEX: MediaIndex
BUS = EventBus()
PIPELINE: Pipeline
SCAN_LOCK = threading.Lock()
TRANSFERS: TransferStore
TV = None
VOICE_STATE: dict[str, Any] = {
    "state": "offline",
    "muted": False,
    "ts": 0.0,
    "client_id": "hub-voice",
    "last_transcript": "",
}
REQUEST_CTX = threading.local()
MIC_PROBE: MicProbeStore
TTS: TtsService | None = None


class HubHandler(BaseHTTPRequestHandler):
    server_version = f"JarvisHub/{__version__}"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        if LOG:
            LOG.info("%s - " + fmt, self.address_string(), *args)
        else:
            super().log_message(fmt, *args)

    # ---- routing ---------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._dispatch("GET")
        except BrokenPipeError:
            return
        except Exception:
            self._handle_error()

    def do_POST(self) -> None:  # noqa: N802
        try:
            self._dispatch("POST")
        except BrokenPipeError:
            return
        except Exception:
            self._handle_error()

    def do_HEAD(self) -> None:  # noqa: N802
        try:
            self._dispatch("HEAD")
        except BrokenPipeError:
            return
        except Exception:
            self._handle_error()

    def do_DELETE(self) -> None:  # noqa: N802
        try:
            self._dispatch("DELETE")
        except BrokenPipeError:
            return
        except Exception:
            self._handle_error()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _dispatch(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path or "/")
        query = parse_qs(parsed.query or "")

        if path != "/" and path.endswith("/"):
            path = path.rstrip("/") or "/"

        # Public static + health (health is useful for the watchdog / LAN probes).
        if method == "GET" and path in {"/api/health", "/health"}:
            return self._health()
        if method == "GET" and self._try_static(path):
            return

        media_stream = re.fullmatch(r"/api/media/(\d+)/stream", path)
        transfer_stream = re.fullmatch(r"/api/transfers/([^/]+)/stream", path)
        if method in {"GET", "HEAD"} and (media_stream or transfer_stream):
            if not self._stream_authorized(query, media_stream, transfer_stream):
                return self._json(401, {"ok": False, "error": "unauthorized"})
            if media_stream:
                return self._media_stream(int(media_stream.group(1)))
            return self._transfer_stream(transfer_stream.group(1))

        if not self._authorized(query):
            return self._json(401, {"ok": False, "error": "unauthorized"})

        if method == "GET" and path == "/api/status":
            return self._status()
        if method == "GET" and path == "/api/events":
            return self._events()
        if method == "GET" and path == "/api/media":
            return self._media_list(query)
        if method == "POST" and path == "/api/media/rescan":
            return self._media_rescan(query)
        if method == "POST" and path == "/api/command":
            return self._command()
        if method == "GET" and path == "/api/voice/status":
            return self._voice_status()
        if method == "POST" and path == "/api/voice/status":
            return self._voice_heartbeat()
        if method == "POST" and path == "/api/voice/mute":
            return self._voice_mute()
        if method == "GET" and path == "/api/transfers":
            return self._transfers_list()
        if method == "POST" and path == "/api/transfers":
            return self._transfers_upload()
        if method == "GET" and path == "/api/tv/status":
            return self._tv_status()
        if method in {"GET", "POST"} and path == "/api/tts":
            return self._tts(method, query)
        if method == "POST" and path == "/api/tv/power":
            return self._tv_power()
        if method == "POST" and path == "/api/tv/input":
            return self._tv_input()
        if method == "GET" and path == "/api/mic-probe":
            return self._mic_probe_list()
        if method == "POST" and path == "/api/mic-probe":
            return self._mic_probe_post()

        media_poster = re.fullmatch(r"/api/media/(\d+)/poster", path)
        if method == "GET" and media_poster:
            return self._media_poster(int(media_poster.group(1)))
        media_one = re.fullmatch(r"/api/media/(\d+)", path)
        if method == "GET" and media_one:
            return self._media_one(int(media_one.group(1)))
        transfer_one = re.fullmatch(r"/api/transfers/([^/]+)", path)
        if transfer_one and method == "GET":
            return self._transfer_one(transfer_one.group(1))
        if transfer_one and method == "DELETE":
            return self._transfer_delete(transfer_one.group(1))
        if transfer_one and method == "POST" and path.endswith("/play"):
            return self._transfer_play(transfer_one.group(1))

        transfer_play = re.fullmatch(r"/api/transfers/([^/]+)/play", path)
        if method == "POST" and transfer_play:
            return self._transfer_play(transfer_play.group(1))

        return self._json(404, {"ok": False, "error": "not found"})

    # ---- handlers --------------------------------------------------------

    def _health(self) -> None:
        body = {
            "ok": True,
            "status": "healthy",
            "hub_name": CFG.hub_name,
            "version": __version__,
            "uptime_seconds": int(time.time() - STARTED_AT),
            "scanning": INDEX.scanning,
            "mic_probe": MIC_PROBE.summary() if MIC_PROBE is not None else {"last_at": None},
        }
        self._json(200, body)

    def _status(self) -> None:
        body = {
            "ok": True,
            "version": __version__,
            "phase": 2,
            "uptime_seconds": int(time.time() - STARTED_AT),
            "platform": sys.platform,
            "media_count": INDEX.count(),
            "scanning": INDEX.scanning,
            "last_scan": INDEX.last_scan,
            "sse_clients": BUS.subscriber_count,
            "voice": dict(VOICE_STATE),
            "tv": TV.status() if TV is not None else {"controller": "none"},
            "tts": TTS.status() if TTS is not None else {"enabled": False},
            "config": CFG.public_view(),
        }
        self._json(200, body)

    def _command(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        text = str(payload.get("text") or "")
        source = str(payload.get("source") or "api")
        client_id = str(payload.get("client_id") or "")
        REQUEST_CTX.base_url = self._public_base()
        response = PIPELINE.process(CommandRequest(text=text, source=source, client_id=client_id))
        self._json(200, response.to_dict())

    def _events(self) -> None:
        q = BUS.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._cors()
        self.end_headers()
        try:
            for chunk in BUS.iter_sse(q):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        finally:
            BUS.unsubscribe(q)

    def _media_list(self, query: dict[str, list[str]]) -> None:
        q = (query.get("q") or [""])[0]
        limit = _int_arg(query, "limit", 48)
        offset = _int_arg(query, "offset", 0)
        items = INDEX.search(query=q, limit=limit, offset=offset)
        # For unfiltered lists the total is the whole library; for searches
        # we report the size of this page plus a hint (full count is cheap).
        total = INDEX.count() if not q else len(items) + offset
        self._json(200, {"ok": True, "items": items, "total": total, "limit": limit, "offset": offset, "q": q})

    def _media_one(self, media_id: int) -> None:
        item = INDEX.get(media_id)
        if not item:
            return self._json(404, {"ok": False, "error": "not found"})
        self._json(200, {"ok": True, "item": item})

    def _media_poster(self, media_id: int) -> None:
        item = INDEX.get(media_id)
        if not item:
            return self._json(404, {"ok": False, "error": "not found"})
        poster = INDEX.poster_path_for(media_id)
        if poster is not None:
            return self._send_file(poster, cache=True)
        svg = _placeholder_svg(item.get("title") or "Untitled", item.get("year"))
        data = svg.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=300")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _media_rescan(self, query: dict[str, list[str]]) -> None:
        wait = (query.get("wait") or ["0"])[0] in {"1", "true", "yes"}
        full = (query.get("full") or ["0"])[0] in {"1", "true", "yes"}
        if wait:
            if not SCAN_LOCK.acquire(blocking=False):
                return self._json(409, {"ok": False, "error": "a scan is already running"})
            try:
                result = INDEX.scan(full=full)
            finally:
                SCAN_LOCK.release()
            BUS.publish("media", {"type": "rescan", "result": result})
            return self._json(200, {"ok": True, **result})

        def _bg() -> None:
            if not SCAN_LOCK.acquire(blocking=False):
                BUS.publish("media", {"type": "rescan", "status": "already_running"})
                return
            try:
                result = INDEX.scan(full=full)
                BUS.publish("media", {"type": "rescan", "result": result})
            finally:
                SCAN_LOCK.release()

        threading.Thread(target=_bg, name="jarvis-rescan", daemon=True).start()
        self._json(202, {"ok": True, "status": "started", "full": full})


    def _mic_probe_list(self) -> None:
        self._json(200, {"ok": True, "items": MIC_PROBE.list(), "summary": MIC_PROBE.summary()})

    def _mic_probe_post(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        if length <= 0:
            return self._json(400, {"ok": False, "error": "Content-Length is required"})
        if length > MIC_PROBE_MAX:
            return self._json(413, {"ok": False, "error": "report exceeds size limit"})
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._json(400, {"ok": False, "error": "invalid json"})
        try:
            ip = self.client_address[0] if self.client_address else ""
            report = sanitize_mic_probe(payload, ip, self.headers.get("User-Agent") or "")
        except ValueError as exc:
            return self._json(400, {"ok": False, "error": str(exc)})
        MIC_PROBE.add(report)
        if LOG:
            LOG.info(
                "mic-probe tizen_api=%s getusermedia=%s live_audio=%s ip=%s",
                report.get("tizen_api"),
                report.get("getusermedia"),
                report.get("live_audio"),
                report.get("client_ip"),
            )
        BUS.publish("mic-probe", {"type": "report", "summary": MIC_PROBE.summary()})
        self._json(201, {"ok": True, "item": report, "summary": MIC_PROBE.summary()})

    def _media_stream(self, media_id: int) -> None:
        path = INDEX.file_path_for(media_id)
        if path is None:
            return self._json(404, {"ok": False, "error": "not found"})
        send_range(self, path, self.headers.get("Range"), extra_headers=self._cors)

    def _voice_status(self) -> None:
        self._json(200, {"ok": True, **VOICE_STATE})

    def _voice_heartbeat(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        VOICE_STATE["state"] = str(payload.get("state") or VOICE_STATE.get("state") or "unknown")
        VOICE_STATE["ts"] = float(payload.get("ts") or time.time())
        VOICE_STATE["client_id"] = str(payload.get("client_id") or VOICE_STATE.get("client_id") or "hub-voice")
        if "last_transcript" in payload:
            VOICE_STATE["last_transcript"] = str(payload.get("last_transcript") or "")
        if "muted" in payload:
            VOICE_STATE["muted"] = bool(payload["muted"])
        BUS.publish("voice", dict(VOICE_STATE))
        self._json(200, {"ok": True, **VOICE_STATE})

    def _voice_mute(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        muted = bool(payload.get("muted"))
        VOICE_STATE["muted"] = muted
        VOICE_STATE["state"] = "muted" if muted else "listening"
        VOICE_STATE["ts"] = time.time()
        flag = CFG.data_dir / "voice" / "muted.flag"
        flag.parent.mkdir(parents=True, exist_ok=True)
        if muted:
            flag.write_text("1", encoding="utf-8")
        elif flag.exists():
            flag.unlink()
        BUS.publish("voice", dict(VOICE_STATE))
        self._json(200, {"ok": True, **VOICE_STATE})

    def _tts(self, method: str, query: dict[str, list[str]]) -> None:
        """Talkback: GET /api/tts?text=... or POST {"text": ...} returns audio/wav.

        POST keeps the reply text out of URLs and access logs; GET exists so
        an <audio src> or curl can use it directly.
        """
        if TTS is None:
            return self._json(503, {"ok": False, "error": "talkback is not initialised"})
        if method == "POST":
            payload = self._read_json()
            if payload is None:
                return
            text = str(payload.get("text") or "")
        else:
            text = (query.get("text") or [""])[0]
        if not text.strip():
            return self._json(400, {"ok": False, "error": "text is required"})
        try:
            data, engine = TTS.synthesize(text)
        except TtsUnavailable as exc:
            return self._json(503, {"ok": False, "error": str(exc)})
        except ValueError as exc:
            return self._json(400, {"ok": False, "error": str(exc)})
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Jarvis-TTS-Engine", engine)
        self.send_header("Access-Control-Expose-Headers", "X-Jarvis-TTS-Engine")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _tv_power(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        handler = getattr(TV, "power", None)
        if not callable(handler):
            return self._tv_unsupported("power")
        result = handler(str(payload.get("action") or ""))
        self._json(200 if result.get("ok") else 502, result)

    def _tv_input(self) -> None:
        payload = self._read_json()
        if payload is None:
            return
        handler = getattr(TV, "set_input", None)
        if not callable(handler):
            return self._tv_unsupported("input")
        try:
            times = max(1, min(10, int(payload.get("times") or 1)))
        except (TypeError, ValueError):
            times = 1
        result = handler(str(payload.get("input") or "next"), times=times)
        self._json(200 if result.get("ok") else 502, result)

    def _tv_unsupported(self, what: str) -> None:
        name = getattr(TV, "name", "none")
        self._json(
            501,
            {
                "ok": False,
                "action": "unsupported",
                "controller": name,
                "error": f"TV {what} control is not enabled. Set tv.controller to 'samsungtvws' (and tv.ip / tv.mac) in config.json.",
            },
        )

    def _tv_status(self) -> None:
        body = TV.status() if TV is not None else {"ok": True, "controller": "none"}
        self._json(200, {"ok": True, **body})

    def _transfers_list(self) -> None:
        items = [self._public_transfer(t) for t in TRANSFERS.list()]
        self._json(200, {"ok": True, "items": items})

    def _transfer_one(self, ident: str) -> None:
        item = TRANSFERS.get(ident)
        if not item:
            return self._json(404, {"ok": False, "error": "not found"})
        self._json(200, {"ok": True, "item": self._public_transfer(item)})

    def _transfers_upload(self) -> None:
        length = int(self.headers.get("Content-Length") or "0")
        max_bytes = int(CFG.transfers.get("max_bytes") or 1_073_741_824)
        if length <= 0:
            return self._json(400, {"ok": False, "error": "Content-Length is required"})
        if length > max_bytes:
            return self._json(413, {"ok": False, "error": "file exceeds max_bytes"})
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type") or ""
        try:
            if ctype.lower().startswith("multipart/"):
                files = parse_multipart(ctype, body)
                if not files:
                    return self._json(400, {"ok": False, "error": "no file in upload"})
                upload = files[0]
                item = TRANSFERS.add(upload.filename, upload.data, upload.content_type)
            else:
                filename = (self.headers.get("X-Filename") or "upload.bin").split("/")[-1]
                item = TRANSFERS.add(filename, body, ctype)
        except ValueError as exc:
            return self._json(400, {"ok": False, "error": str(exc)})
        public = self._public_transfer(item)
        BUS.publish("transfer", {"type": "created", "item": public})
        self._json(201, {"ok": True, "item": public})

    def _transfer_delete(self, ident: str) -> None:
        item = TRANSFERS.get(ident)
        if not item:
            return self._json(404, {"ok": False, "error": "not found"})
        TRANSFERS.delete(ident)
        BUS.publish("transfer", {"type": "deleted", "id": item["id"], "public_id": item["public_id"]})
        self._json(200, {"ok": True, "deleted": True})

    def _transfer_stream(self, ident: str) -> None:
        path = TRANSFERS.path_for(ident)
        if path is None:
            return self._json(404, {"ok": False, "error": "not found"})
        send_range(self, path, self.headers.get("Range"), extra_headers=self._cors)

    def _transfer_play(self, ident: str) -> None:
        item = TRANSFERS.get(ident)
        path = TRANSFERS.path_for(ident)
        if not item or path is None:
            return self._json(404, {"ok": False, "error": "not found"})
        url = self._transfer_stream_url(item)
        title = item.get("filename") or "upload"
        if TV is not None and hasattr(TV, "play_url"):
            result = TV.play_url(url, title=title)
        else:
            result = {"ok": True, "action": "would_play", "url": url, "title": title}
        action = result.get("action") or "would_play"
        reply = (
            f"Playing {title} on the TV."
            if action == "playing"
            else f"I would play {title} on the TV. A stream URL is ready."
        )
        payload = {"reply": reply, "intent": "transfer.play", "data": {"item": self._public_transfer(item), **result}}
        BUS.publish("reply", payload)
        self._json(200, {"ok": True, **payload})

    def _public_transfer(self, item: dict[str, Any]) -> dict[str, Any]:
        public = {k: v for k, v in item.items() if k != "stored_path"}
        public["stream_url"] = self._transfer_stream_url(item)
        return public

    def _transfer_stream_url(self, item: dict[str, Any]) -> str:
        ident = int(item["id"])
        exp, sig = sign_stream(CFG, "transfer", ident)
        return f"{self._public_base()}/api/transfers/{item['public_id']}/stream?exp={exp}&sig={sig}"

    def _public_base(self) -> str:
        if CFG.public_host:
            host = CFG.public_host
            if host.startswith("http://") or host.startswith("https://"):
                return host.rstrip("/")
            scheme = "https" if CFG.ssl_enabled else "http"
            if ":" in host:
                return f"{scheme}://{host}"
            return f"{scheme}://{host}:{CFG.port}"
        host = self.headers.get("Host") or f"127.0.0.1:{CFG.port}"
        scheme = "https" if CFG.ssl_enabled else "http"
        # Prefer the address the client used, which is what phones/TVs can reach.
        return f"{scheme}://{host}"

    def _stream_authorized(self, query, media_stream, transfer_stream) -> bool:
        if self._authorized(query):
            return True
        exp = (query.get("exp") or [""])[0]
        sig = (query.get("sig") or [""])[0]
        if media_stream:
            return verify_stream(CFG, "media", int(media_stream.group(1)), exp, sig)
        item = TRANSFERS.get(transfer_stream.group(1))
        if not item:
            return False
        return verify_stream(CFG, "transfer", int(item["id"]), exp, sig)

    # ---- static files ----------------------------------------------------

    def _try_static(self, path: str) -> bool:
        aliases = {
            "/": "index.html",
            "/index.html": "index.html",
            "/phone": "index.html",
            "/laptop": "index.html",
            "/tv": "tv.html",
            "/tv.html": "tv.html",
            "/tv/mic-probe": "mic-probe.html",
            "/tv/mic-probe.html": "mic-probe.html",
            "/fire": "tv.html",
            "/echo": "index.html",
            "/manifest.webmanifest": "manifest.webmanifest",
            "/manifest.json": "manifest.webmanifest",
        }
        if path in aliases:
            rel = aliases[path]
        elif path.startswith("/css/") or path.startswith("/js/") or path.startswith("/img/"):
            rel = path.lstrip("/")
        else:
            return False
        # Reject any attempt to walk out of WEB_ROOT.
        rel = posixpath.normpath(rel).lstrip("/")
        if rel.startswith("..") or "/../" in f"/{rel}/":
            self._json(403, {"ok": False, "error": "forbidden"})
            return True
        target = (WEB_ROOT / rel).resolve()
        try:
            target.relative_to(WEB_ROOT.resolve())
        except ValueError:
            self._json(403, {"ok": False, "error": "forbidden"})
            return True
        if not target.is_file():
            return False
        self._send_file(target, cache=False)
        return True

    def _send_file(self, path: Path, *, cache: bool) -> None:
        ctype = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix.lower() == ".webmanifest":
            ctype = "application/manifest+json"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400" if cache else "no-cache")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    # ---- helpers ---------------------------------------------------------

    def _authorized(self, query: dict[str, list[str]]) -> bool:
        return is_authorized(CFG, self.headers, query)

    def _read_json(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or "0")
        if length < 0 or length > 1_000_000:
            self._json(400, {"ok": False, "error": "invalid content-length"})
            return None
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._json(400, {"ok": False, "error": "invalid json"})
            return None
        if not isinstance(payload, dict):
            self._json(400, {"ok": False, "error": "json object required"})
            return None
        return payload

    def _json(self, status: int, body: dict[str, Any]) -> None:
        data = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self._cors()
        self.end_headers()
        self.wfile.write(data)

    def _cors(self) -> None:
        # LAN dashboards are same-origin; CORS is left loose for future thin clients.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Hub-Token, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, HEAD, OPTIONS")

    def _handle_error(self) -> None:
        if LOG:
            LOG.exception("Request failed: %s %s", self.command, self.path)
        else:
            traceback.print_exc()
        try:
            self._json(500, {"ok": False, "error": "internal error"})
        except Exception:  # noqa: BLE001
            return


def _int_arg(query: dict[str, list[str]], name: str, default: int) -> int:
    raw = (query.get(name) or [str(default)])[0]
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _placeholder_svg(title: str, year: int | None) -> str:
    safe = _xml_escape(title)
    year_line = f'<text x="150" y="268" text-anchor="middle" fill="#8aa0b8" font-size="18" font-family="sans-serif">{year}</text>' if year else ""
    # Wrap long titles roughly.
    if len(safe) > 22:
        mid = safe.rfind(" ", 0, 22)
        if mid < 8:
            mid = 22
        line1, line2 = safe[:mid].strip(), safe[mid:].strip()
        title_svg = (
            f'<text x="150" y="220" text-anchor="middle" fill="#e8eef7" font-size="20" font-family="sans-serif">{_xml_escape(line1)}</text>'
            f'<text x="150" y="246" text-anchor="middle" fill="#e8eef7" font-size="20" font-family="sans-serif">{_xml_escape(line2)}</text>'
        )
        if year:
            year_line = f'<text x="150" y="280" text-anchor="middle" fill="#8aa0b8" font-size="18" font-family="sans-serif">{year}</text>'
    else:
        title_svg = f'<text x="150" y="230" text-anchor="middle" fill="#e8eef7" font-size="22" font-family="sans-serif">{safe}</text>'
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="300" height="450" viewBox="0 0 300 450">'
        '<rect width="300" height="450" fill="#121826"/>'
        '<rect x="12" y="12" width="276" height="426" fill="none" stroke="#2d4a6f" stroke-width="2"/>'
        '<circle cx="150" cy="130" r="36" fill="none" stroke="#3d8fd1" stroke-width="3"/>'
        '<text x="150" y="138" text-anchor="middle" fill="#3d8fd1" font-size="28" font-family="sans-serif">J</text>'
        f"{title_svg}{year_line}"
        "</svg>"
    )


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _context_factory() -> dict[str, Any]:
    return {
        "hub_name": CFG.hub_name,
        "wake_word": CFG.wake_word,
        "started_at": STARTED_AT,
        "media_count": INDEX.count(),
        "scanning": INDEX.scanning,
        "sse_clients": BUS.subscriber_count,
        "uptime_seconds": int(time.time() - STARTED_AT),
        "base_url": getattr(REQUEST_CTX, "base_url", ""),
        "voice": dict(VOICE_STATE),
    }


def _stream_url_for(item: dict[str, Any], context: dict[str, Any] | None = None) -> str | None:
    media_id = item.get("id")
    if media_id is None:
        return None
    exp, sig = sign_stream(CFG, "media", int(media_id))
    base = ""
    if context:
        base = str(context.get("base_url") or "")
    if not base:
        base = getattr(REQUEST_CTX, "base_url", "") or ""
    if not base:
        host = CFG.public_host or f"127.0.0.1:{CFG.port}"
        if host.startswith("http://") or host.startswith("https://"):
            base = host.rstrip("/")
        else:
            scheme = "https" if CFG.ssl_enabled else "http"
            base = f"{scheme}://{host}" if ":" in host.split("//")[-1] else f"{scheme}://{host}:{CFG.port}"
    return f"{base}/api/media/{int(media_id)}/stream?exp={exp}&sig={sig}"


def _play_for_grok(title: str, context: dict[str, Any]) -> dict[str, Any]:
    media = next((s for s in PIPELINE.router.specialists if s.name == "media"), None)
    if media is None:
        return {"ok": False, "error": "media specialist missing"}
    return media._play(title, context).get("data") or {}


def _display(payload: dict[str, Any]) -> None:
    BUS.publish("reply", payload)


def build_app(cfg: Config) -> tuple[MediaIndex, Pipeline, TransferStore, Any, MicProbeStore]:
    index = MediaIndex(cfg.db_path, cfg.media_paths)
    transfers = TransferStore(
        db_path=cfg.data_dir / "transfers.sqlite3",
        directory=Path(cfg.transfers["dir"]),
        max_bytes=int(cfg.transfers.get("max_bytes") or 1_073_741_824),
        ttl_hours=float(cfg.transfers.get("ttl_hours") or 48),
        allowed_extensions=list(cfg.transfers.get("allowed_extensions") or []),
    )
    tv = build_tv_controller(
        str(cfg.tv.get("controller") or "null"),
        cfg.tv.get("controller_options") or {},
        tv_config=cfg.tv,
    )
    global TTS
    TTS = TtsService(cfg)
    grok = GrokSpecialist.from_config(
        cfg.grok,
        media_index=index,
        context_tools=_context_factory,
        play_handler=_play_for_grok,
    )
    router = build_default_router(
        media_index=index,
        grok=grok,
        tv=tv,
        stream_url_factory=_stream_url_for,
    )
    pipeline = Pipeline(
        router,
        wake_word=cfg.wake_word,
        display=_display,
        context_factory=_context_factory,
    )
    mic_probe = MicProbeStore(cfg.data_dir / "mic_probe.json")
    return index, pipeline, transfers, tv, mic_probe


def serve(cfg: Config, httpd: ThreadingHTTPServer) -> None:
    scheme = "https" if cfg.ssl_enabled else "http"
    if LOG:
        LOG.info(
            "Jarvis Hub '%s' listening on %s://%s:%s",
            cfg.hub_name,
            scheme,
            cfg.host,
            cfg.port,
        )
        if cfg.media_paths:
            LOG.info("Media libraries: %s", ", ".join(str(p) for p in cfg.media_paths))
        else:
            LOG.info("No media_paths configured; the index will stay empty until you add some.")

    def _startup_scan() -> None:
        if not SCAN_LOCK.acquire(blocking=False):
            return
        try:
            result = INDEX.scan(full=False)
            if LOG:
                LOG.info("Startup media scan: %s", result)
            BUS.publish("media", {"type": "rescan", "result": result, "reason": "startup"})
        except Exception:
            if LOG:
                LOG.exception("Startup media scan failed")
        finally:
            SCAN_LOCK.release()

    threading.Thread(target=_startup_scan, name="jarvis-startup-scan", daemon=True).start()
    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        if LOG:
            LOG.info("Keyboard interrupt — shutting down.")
    finally:
        httpd.server_close()


def make_server(cfg: Config) -> ThreadingHTTPServer:
    httpd = ThreadingHTTPServer((cfg.host, cfg.port), HubHandler)
    httpd.daemon_threads = True
    if cfg.ssl_enabled:
        if not cfg.ssl_certfile.is_file() or not cfg.ssl_keyfile.is_file():
            raise FileNotFoundError(
                "SSL is enabled but cert/key files are missing. "
                "See scripts/generate_cert.ps1 or scripts/generate_cert.sh."
            )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(str(cfg.ssl_certfile), str(cfg.ssl_keyfile))
        httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    return httpd


def main(argv: list[str] | None = None) -> int:
    del argv
    global CFG, LOG, INDEX, PIPELINE, STARTED_AT, TRANSFERS, TV, VOICE_STATE, MIC_PROBE
    STARTED_AT = time.time()
    CFG = load_config()
    LOG = setup_logging(CFG, name="jarvis")
    INDEX, PIPELINE, TRANSFERS, TV, MIC_PROBE = build_app(CFG)
    mute_flag = CFG.data_dir / "voice" / "muted.flag"
    VOICE_STATE["muted"] = mute_flag.is_file() or bool(CFG.voice.get("mute"))
    if VOICE_STATE["muted"]:
        VOICE_STATE["state"] = "muted"
    # Rebind the pipeline's display/context now that globals exist (they already do).
    PIPELINE._display = _display
    PIPELINE._context_factory = _context_factory
    httpd = make_server(CFG)
    serve(CFG, httpd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
