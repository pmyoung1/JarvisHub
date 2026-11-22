"""Phone-to-hub file transfers that the TV can then stream.

Uploads land under data/transfers/ with SQLite metadata, an extension +
magic-byte allowlist, a size cap, and TTL cleanup. The phone never talks
to the TV except through the hub.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

LOG = logging.getLogger("jarvis.transfers")

SCHEMA = """
CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT NOT NULL UNIQUE,
    filename TEXT NOT NULL,
    stored_path TEXT NOT NULL,
    size INTEGER NOT NULL,
    content_type TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
"""

# (extension, magic prefixes). Empty prefixes means "extension only".
ALLOWED_MAGIC: dict[str, tuple[bytes, ...]] = {
    ".mp4": (b"",),  # ftyp box is often at offset 4; checked separately
    ".m4v": (b"",),
    ".mov": (b"",),
    ".mkv": (b"\x1a\x45\xdf\xa3",),
    ".webm": (b"\x1a\x45\xdf\xa3",),
    ".jpg": (b"\xff\xd8\xff",),
    ".jpeg": (b"\xff\xd8\xff",),
    ".png": (b"\x89PNG\r\n\x1a\n",),
    ".webp": (b"RIFF",),
    ".mp3": (b"ID3", b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"),
    ".wav": (b"RIFF",),
}

FTPY_EXTS = {".mp4", ".m4v", ".mov"}


def looks_like_mp4(header: bytes) -> bool:
    return len(header) >= 12 and header[4:8] == b"ftyp"


def allowed_file(filename: str, header: bytes, allowed_exts: list[str]) -> tuple[bool, str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix not in {e.lower() if e.startswith(".") else f".{e.lower()}" for e in allowed_exts}:
        return False, suffix, f"extension {suffix or '(none)'} is not allowed"
    if suffix in FTPY_EXTS:
        if looks_like_mp4(header) or header.startswith(b"\x00\x00\x00"):
            # \x00\x00\x00 is a weak prefix; still require ftyp when we have 12 bytes
            if len(header) >= 12 and not looks_like_mp4(header):
                return False, suffix, "file does not look like an MP4/MOV container"
            return True, suffix, "ok"
        return False, suffix, "file does not look like an MP4/MOV container"
    prefixes = ALLOWED_MAGIC.get(suffix, (b"",))
    if prefixes == (b"",):
        return True, suffix, "ok"
    if any(header.startswith(p) for p in prefixes if p):
        # webp is RIFF....WEBP
        if suffix == ".webp" and len(header) >= 12 and header[8:12] != b"WEBP":
            return False, suffix, "file does not look like a WebP image"
        if suffix == ".wav" and len(header) >= 12 and header[8:12] != b"WAVE":
            return False, suffix, "file does not look like a WAV"
        return True, suffix, "ok"
    return False, suffix, f"magic bytes do not match {suffix}"


class TransferStore:
    def __init__(self, db_path: Path, directory: Path, max_bytes: int, ttl_hours: float, allowed_extensions: list[str]) -> None:
        self.db_path = Path(db_path)
        self.directory = Path(directory)
        self.max_bytes = int(max_bytes)
        self.ttl_seconds = float(ttl_hours) * 3600.0
        self.allowed_extensions = list(allowed_extensions)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.execute(SCHEMA)
                conn.commit()
            finally:
                conn.close()

    def cleanup_expired(self) -> int:
        now = time.time()
        removed = 0
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT id, stored_path FROM transfers WHERE expires_at < ?", (now,)).fetchall()
                for row in rows:
                    path = Path(row["stored_path"])
                    try:
                        if path.is_file():
                            path.unlink()
                    except OSError:
                        pass
                    conn.execute("DELETE FROM transfers WHERE id = ?", (row["id"],))
                    removed += 1
                conn.commit()
            finally:
                conn.close()
        return removed

    def add(self, filename: str, data: bytes, content_type: str = "") -> dict[str, Any]:
        self.cleanup_expired()
        if len(data) > self.max_bytes:
            raise ValueError(f"file exceeds max_bytes ({self.max_bytes})")
        if not data:
            raise ValueError("empty upload")
        ok, suffix, reason = allowed_file(filename, data[:64], self.allowed_extensions)
        if not ok:
            raise ValueError(reason)
        safe = "".join(ch for ch in Path(filename).name if ch.isalnum() or ch in "._- ")[:80] or "upload"
        public_id = uuid.uuid4().hex
        stored = self.directory / f"{public_id}{suffix}"
        stored.write_bytes(data)
        now = time.time()
        expires = now + self.ttl_seconds
        with self._lock:
            conn = self._connect()
            try:
                cur = conn.execute(
                    "INSERT INTO transfers (public_id, filename, stored_path, size, content_type, created_at, expires_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (public_id, safe, str(stored), len(data), content_type or "", now, expires),
                )
                row_id = int(cur.lastrowid)
                conn.commit()
            finally:
                conn.close()
        return self.get(row_id)  # type: ignore[return-value]

    def get(self, ident: int | str) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                if isinstance(ident, int) or (isinstance(ident, str) and ident.isdigit()):
                    row = conn.execute("SELECT * FROM transfers WHERE id = ?", (int(ident),)).fetchone()
                else:
                    row = conn.execute("SELECT * FROM transfers WHERE public_id = ?", (str(ident),)).fetchone()
                return self._row(row) if row else None
            finally:
                conn.close()

    def path_for(self, ident: int | str) -> Path | None:
        item = self.get(ident)
        if not item:
            return None
        path = Path(item["stored_path"])
        try:
            if path.is_file():
                return path
        except OSError:
            return None
        return None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        self.cleanup_expired()
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM transfers ORDER BY id DESC LIMIT ?",
                    (max(1, min(limit, 200)),),
                ).fetchall()
                return [self._row(r) for r in rows]
            finally:
                conn.close()

    def delete(self, ident: int | str) -> bool:
        item = self.get(ident)
        if not item:
            return False
        path = Path(item["stored_path"])
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass
        with self._lock:
            conn = self._connect()
            try:
                conn.execute("DELETE FROM transfers WHERE id = ?", (item["id"],))
                conn.commit()
            finally:
                conn.close()
        return True

    @staticmethod
    def _row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "public_id": row["public_id"],
            "filename": row["filename"],
            "size": row["size"],
            "content_type": row["content_type"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "stored_path": row["stored_path"],
        }
