"""Read Kodi-style .nfo XML and locate local poster sidecar art."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp")

_MAX_NFO_BYTES = 512 * 1024


def read_nfo(path: Path) -> dict[str, Any]:
    """Parse a Kodi <movie> nfo. Returns {} on any parse problem.

    Some nfo files are just a single IMDb URL line; those yield {}.
    Defused-ish: we cap size and only read text of known tags.
    """
    try:
        if path.stat().st_size > _MAX_NFO_BYTES:
            return {}
        raw = path.read_bytes()
    except OSError:
        return {}
    text = raw.decode("utf-8", errors="replace").lstrip("\ufeff")
    start = text.find("<")
    if start < 0:
        return {}
    text = text[start:]
    # Reject DTD/entity declarations (XML entity-expansion guard).
    if re.search(r"<!DOCTYPE|<!ENTITY", text, re.IGNORECASE):
        return {}
    # Trim trailing junk after the root (e.g. appended IMDb URL).
    end = max(text.rfind("</movie>"), text.rfind("</episodedetails>"))
    if end >= 0:
        closing = text.find(">", end)
        text = text[: closing + 1]
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {}
    if root.tag.lower() not in {"movie", "episodedetails", "tvshow", "musicvideo"}:
        return {}

    def first(*tags: str) -> str | None:
        for tag in tags:
            node = root.find(tag)
            if node is not None and node.text and node.text.strip():
                return node.text.strip()
        return None

    out: dict[str, Any] = {}
    title = first("title", "originaltitle")
    if title:
        out["title"] = title
    year_text = first("year")
    if not year_text:
        premiered = first("premiered", "releasedate", "aired")
        if premiered and re.match(r"^\d{4}", premiered):
            year_text = premiered[:4]
    if year_text and re.fullmatch(r"\d{4}", year_text):
        out["year"] = int(year_text)
    plot = first("plot", "outline")
    if plot:
        out["plot"] = plot
    genres = [g.text.strip() for g in root.findall("genre") if g.text and g.text.strip()]
    if genres:
        # Some scrapers pack "Action / Sci-Fi" into one tag.
        split: list[str] = []
        for g in genres:
            split.extend(part.strip() for part in re.split(r"[/|,]", g) if part.strip())
        out["genres"] = list(dict.fromkeys(split))
    runtime = first("runtime")
    if runtime and runtime.isdigit():
        out["runtime"] = int(runtime)
    rating = first("rating")
    if rating:
        try:
            out["rating"] = float(rating)
        except ValueError:
            pass
    return out


def find_nfo(video: Path) -> Path | None:
    candidates = [
        video.with_suffix(".nfo"),
        video.parent / "movie.nfo",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def find_poster(video: Path) -> Path | None:
    """Return the first local poster sidecar for a video, if any."""
    stem = video.stem
    folder = video.parent
    names: list[str] = []
    for ext in IMAGE_EXTENSIONS:
        names.extend(
            [
                f"{stem}-poster{ext}",
                f"{stem}.poster{ext}",
                f"{stem}{ext}",
            ]
        )
    for ext in IMAGE_EXTENSIONS:
        names.extend(
            [
                f"poster{ext}",
                f"folder{ext}",
                f"cover{ext}",
                f"movie{ext}",
            ]
        )
    # Case-insensitive lookup within the folder (Windows-friendly, also Linux).
    try:
        entries = {p.name.lower(): p for p in folder.iterdir() if p.is_file()}
    except OSError:
        return None
    for name in names:
        hit = entries.get(name.lower())
        if hit is not None:
            return hit
    return None
