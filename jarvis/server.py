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
from jarvis.config import Config, load_config
from jarvis.events import EventBus
from jarvis.logging_setup import setup_logging
from jarvis.media.index import MediaIndex
from jarvis.pipeline import CommandRequest, Pipeline
from jarvis.router import build_default_router

WEB_ROOT = Path(__file__).resolve().parent.parent / "web"
STARTED_AT = time.time()

# Shared process state set in main().
CFG: Config
LOG = None
INDEX: MediaIndex
BUS = EventBus()
PIPELINE: Pipeline
SCAN_LOCK = threading.Lock()


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

        media_poster = re.fullmatch(r"/api/media/(\d+)/poster", path)
        if method == "GET" and media_poster:
            return self._media_poster(int(media_poster.group(1)))
        media_one = re.fullmatch(r"/api/media/(\d+)", path)
        if method == "GET" and media_one:
            return self._media_one(int(media_one.group(1)))

        # Hook only — video is not streamed in phase 1.
        media_stream = re.fullmatch(r"/api/media/(\d+)/stream", path)
        if media_stream:
            return self._json(
                501,
                {
                    "ok": False,
                    "error": "Video streaming is not implemented in phase 1.",
                    "hook": "phase-2",
                },
            )

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
        }
        self._json(200, body)

    def _status(self) -> None:
        body = {
            "ok": True,
            "version": __version__,
            "phase": 1,
            "uptime_seconds": int(time.time() - STARTED_AT),
            "platform": sys.platform,
            "media_count": INDEX.count(),
            "scanning": INDEX.scanning,
            "last_scan": INDEX.last_scan,
            "sse_clients": BUS.subscriber_count,
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

    # ---- static files ----------------------------------------------------

    def _try_static(self, path: str) -> bool:
        aliases = {
            "/": "index.html",
            "/index.html": "index.html",
            "/phone": "index.html",
            "/tv": "tv.html",
            "/tv.html": "tv.html",
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
        token = CFG.auth_token
        if not token:
            return True
        header = self.headers.get("X-Hub-Token") or ""
        if header == token:
            return True
        auth = self.headers.get("Authorization") or ""
        if auth.lower().startswith("bearer ") and auth[7:].strip() == token:
            return True
        provided = (query.get("token") or [""])[0]
        return provided == token

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
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

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
    }


def _display(payload: dict[str, Any]) -> None:
    BUS.publish("reply", payload)


def build_app(cfg: Config) -> tuple[MediaIndex, Pipeline]:
    index = MediaIndex(cfg.db_path, cfg.media_paths)
    router = build_default_router(media_index=index)
    pipeline = Pipeline(
        router,
        wake_word=cfg.wake_word,
        display=_display,
        context_factory=_context_factory,
    )
    return index, pipeline


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
    global CFG, LOG, INDEX, PIPELINE, STARTED_AT
    STARTED_AT = time.time()
    CFG = load_config()
    LOG = setup_logging(CFG, name="jarvis")
    INDEX, PIPELINE = build_app(CFG)
    # Rebind the pipeline's display/context now that globals exist (they already do).
    PIPELINE._display = _display
    PIPELINE._context_factory = _context_factory
    httpd = make_server(CFG)
    serve(CFG, httpd)
    return 0


if __name__ == "__main__":
    sys.exit(main())
