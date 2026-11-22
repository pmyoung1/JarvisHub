"""SQLite media index with incremental mtime-based rescans."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterable

from jarvis.media.nfo import find_nfo, find_poster, read_nfo
from jarvis.media.parser import is_video_file, normalize_for_match, title_from_path

SCHEMA = """
CREATE TABLE IF NOT EXISTS media (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    year INTEGER,
    plot TEXT,
    genres TEXT,
    poster_path TEXT,
    file_size INTEGER,
    mtime REAL,
    sidecar_mtime REAL,
    indexed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_media_title ON media(title);
CREATE INDEX IF NOT EXISTS idx_media_year ON media(year);

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


class MediaIndex:
    def __init__(self, db_path: Path, library_paths: Iterable[Path | str]) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.library_paths = [Path(p) for p in library_paths]
        self._lock = threading.RLock()
        self._scanning = False
        self._last_scan: dict[str, Any] | None = None
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._connect()
            try:
                conn.executescript(SCHEMA)
                conn.commit()
            finally:
                conn.close()

    @property
    def scanning(self) -> bool:
        return self._scanning

    @property
    def last_scan(self) -> dict[str, Any] | None:
        return self._last_scan

    def count(self) -> int:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT COUNT(*) AS n FROM media").fetchone()
                return int(row["n"]) if row else 0
            finally:
                conn.close()

    def get(self, media_id: int) -> dict[str, Any] | None:
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT * FROM media WHERE id = ?", (media_id,)).fetchone()
                return _row_to_dict(row) if row else None
            finally:
                conn.close()

    def poster_path_for(self, media_id: int) -> Path | None:
        """Return the indexed poster path only — never an arbitrary file.

        The public item dict does not include poster_path (clients fetch
        art via /api/media/<id>/poster). This looks the path up in SQLite
        and only returns it if that exact file still exists.
        """
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT poster_path FROM media WHERE id = ?",
                    (media_id,),
                ).fetchone()
            finally:
                conn.close()
        if not row or not row["poster_path"]:
            return None
        path = Path(row["poster_path"])
        try:
            if path.is_file():
                return path
        except OSError:
            return None
        return None

    def file_path_for(self, media_id: int) -> Path | None:
        """Return the indexed video path only if that exact file still exists."""
        with self._lock:
            conn = self._connect()
            try:
                row = conn.execute("SELECT path FROM media WHERE id = ?", (media_id,)).fetchone()
            finally:
                conn.close()
        if not row or not row["path"]:
            return None
        path = Path(row["path"])
        try:
            if path.is_file():
                return path
        except OSError:
            return None
        return None

    def search(self, query: str = "", limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))
        q = (query or "").strip()
        with self._lock:
            conn = self._connect()
            try:
                if not q:
                    rows = conn.execute(
                        "SELECT * FROM media ORDER BY title COLLATE NOCASE, year, id "
                        "LIMIT ? OFFSET ?",
                        (limit, offset),
                    ).fetchall()
                else:
                    like = f"%{q}%"
                    rows = conn.execute(
                        "SELECT * FROM media WHERE title LIKE ? OR path LIKE ? "
                        "OR IFNULL(plot, '') LIKE ? OR IFNULL(genres, '') LIKE ? "
                        "ORDER BY title COLLATE NOCASE, year, id LIMIT ? OFFSET ?",
                        (like, like, like, like, limit, offset),
                    ).fetchall()
                return [_row_to_dict(r) for r in rows]
            finally:
                conn.close()

    def fuzzy_match(self, query: str, limit: int = 5, cutoff: float = 0.52) -> list[dict[str, Any]]:
        """Rank titles with difflib; used to resolve spoken / typed play commands."""
        needle = normalize_for_match(query)
        if not needle:
            return []
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT * FROM media").fetchall()
            finally:
                conn.close()
        scored: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            title = normalize_for_match(row["title"] or "")
            year = row["year"]
            variants = [title]
            if year:
                variants.append(f"{title} {year}")
            score = max(SequenceMatcher(None, needle, v).ratio() for v in variants)
            # Bonus if the query is a contiguous substring of the title.
            if needle in title or title in needle:
                score = max(score, 0.85)
            if score >= cutoff:
                scored.append((score, row))
        scored.sort(key=lambda pair: (-pair[0], pair[1]["title"] or ""))
        return [_row_to_dict(r) for _, r in scored[:limit]]

    def resolve_title(self, query: str) -> dict[str, Any] | None:
        """Best single match for a play/find command, or None."""
        hits = self.fuzzy_match(query, limit=5, cutoff=0.52)
        if hits:
            return hits[0]
        # Last chance: LIKE search.
        likes = self.search(query=query, limit=5, offset=0)
        return likes[0] if likes else None

    def scan(self, full: bool = False) -> dict[str, Any]:
        """Walk library roots and upsert / prune the index.

        Incremental by default: a file whose mtime and sidecar mtimes
        are unchanged is left alone. ``full=True`` re-parses everything.
        """
        started = time.time()
        self._scanning = True
        added = updated = unchanged = removed = errors = 0
        seen: set[str] = set()
        roots = list(self.library_paths)
        try:
            for root in roots:
                if not root.exists() or not root.is_dir():
                    continue
                for video in _iter_videos(root):
                    key = _canon(video)
                    seen.add(key)
                    try:
                        action = self._index_one(video, root, full=full)
                    except OSError:
                        errors += 1
                        continue
                    if action == "added":
                        added += 1
                    elif action == "updated":
                        updated += 1
                    else:
                        unchanged += 1
            removed = self._prune(seen, roots)
            result = {
                "ok": True,
                "added": added,
                "updated": updated,
                "unchanged": unchanged,
                "removed": removed,
                "errors": errors,
                "total": self.count(),
                "seconds": round(time.time() - started, 3),
                "full": full,
            }
            self._last_scan = result
            return result
        finally:
            self._scanning = False

    def _index_one(self, video: Path, root: Path, *, full: bool) -> str:
        stat = video.stat()
        mtime = stat.st_mtime
        size = stat.st_size
        nfo_path = find_nfo(video)
        poster_path = find_poster(video)
        sidecar_mtime = _sidecar_mtime(nfo_path, poster_path)
        key = _canon(video)
        with self._lock:
            conn = self._connect()
            try:
                existing = conn.execute("SELECT * FROM media WHERE path = ?", (key,)).fetchone()
                if (
                    existing
                    and not full
                    and existing["mtime"] == mtime
                    and (existing["sidecar_mtime"] or 0) == sidecar_mtime
                    and existing["file_size"] == size
                ):
                    # If a previously recorded poster vanished, fall through and update.
                    stored_poster = existing["poster_path"]
                    if (not stored_poster and not poster_path) or (
                        stored_poster and Path(stored_poster).is_file()
                    ):
                        return "unchanged"

                title, year = title_from_path(video)
                plot = None
                genres: list[str] = []
                if nfo_path:
                    meta = read_nfo(nfo_path)
                    title = meta.get("title") or title
                    year = meta.get("year") if meta.get("year") else year
                    plot = meta.get("plot")
                    genres = meta.get("genres") or []

                now = time.time()
                payload = (
                    key,
                    title,
                    year,
                    plot,
                    json.dumps(genres) if genres else None,
                    str(poster_path) if poster_path else None,
                    size,
                    mtime,
                    sidecar_mtime,
                    now,
                )
                if existing:
                    conn.execute(
                        "UPDATE media SET title=?, year=?, plot=?, genres=?, poster_path=?, "
                        "file_size=?, mtime=?, sidecar_mtime=?, indexed_at=? WHERE path=?",
                        payload[1:] + (key,),
                    )
                    conn.commit()
                    return "updated"
                conn.execute(
                    "INSERT INTO media (path, title, year, plot, genres, poster_path, "
                    "file_size, mtime, sidecar_mtime, indexed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    payload,
                )
                conn.commit()
                return "added"
            finally:
                conn.close()

    def _prune(self, seen: set[str], roots: list[Path]) -> int:
        """Drop rows whose files vanished, limited to the configured roots."""
        prefixes = [_canon(r) for r in roots]
        with self._lock:
            conn = self._connect()
            try:
                rows = conn.execute("SELECT id, path FROM media").fetchall()
                drop: list[int] = []
                for row in rows:
                    path = row["path"]
                    if not any(path == pfx or path.startswith(pfx + "/") or path.startswith(pfx + "\\")
                               for pfx in prefixes):
                        continue
                    if path not in seen:
                        drop.append(int(row["id"]))
                if drop:
                    conn.executemany("DELETE FROM media WHERE id = ?", [(i,) for i in drop])
                    conn.commit()
                return len(drop)
            finally:
                conn.close()


def _iter_videos(root: Path) -> Iterable[Path]:
    skip_dirs = {".git", "__pycache__", "@eadir", "#recycle", "$recycle.bin", "system volume information"}
    try:
        for dirpath, dirnames, filenames in _walk(root, skip_dirs):
            for name in filenames:
                path = dirpath / name
                if is_video_file(path):
                    yield path
    except OSError:
        return


def _walk(root: Path, skip_dirs: set[str]):
    # pathlib.rglob would follow the same idea; os.walk via Path keeps skip control.
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d.lower() not in skip_dirs and not d.startswith(".")]
        yield Path(dirpath), dirnames, filenames


def _canon(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


def _sidecar_mtime(*paths: Path | None) -> float:
    latest = 0.0
    for path in paths:
        if path is None:
            continue
        try:
            latest = max(latest, path.stat().st_mtime)
        except OSError:
            continue
    return latest


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    item = {
        "id": row["id"],
        "path": row["path"],
        "title": row["title"],
        "year": row["year"],
        "plot": row["plot"],
        "genres": json.loads(row["genres"]) if row["genres"] else [],
        "has_poster": bool(row["poster_path"]),
        "file_size": row["file_size"],
        "mtime": row["mtime"],
    }
    return item
