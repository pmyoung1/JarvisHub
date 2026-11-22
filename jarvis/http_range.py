"""HTTP Range (RFC 9110) responses for media and transfer streams."""

from __future__ import annotations

import mimetypes
import re
from pathlib import Path
from typing import Any, Callable

_RANGE = re.compile(r"bytes=(\d*)-(\d*)$", re.IGNORECASE)


def parse_range(header: str | None, size: int) -> tuple[int, int] | None:
    """Return inclusive (start, end) or None for a full-file response.

    Raises ValueError if the header is present but unsatisfiable / invalid.
    """
    if not header:
        return None
    header = header.strip()
    if not header:
        return None
    # Multiple ranges are not supported in phase 2; refuse them.
    if "," in header:
        raise ValueError("multiple ranges are not supported")
    match = _RANGE.fullmatch(header.replace(" ", ""))
    if not match:
        raise ValueError("invalid range")
    start_s, end_s = match.group(1), match.group(2)
    if start_s == "" and end_s == "":
        raise ValueError("invalid range")
    if start_s == "":
        # suffix: last N bytes
        length = int(end_s)
        if length <= 0:
            raise ValueError("unsatisfiable range")
        if length >= size:
            return 0, size - 1
        return size - length, size - 1
    start = int(start_s)
    end = int(end_s) if end_s else size - 1
    if start >= size or start < 0 or end < start:
        raise ValueError("unsatisfiable range")
    end = min(end, size - 1)
    return start, end


def guess_media_type(path: Path) -> str:
    ctype = mimetypes.guess_type(str(path))[0]
    return ctype or "application/octet-stream"


def send_range(
    handler: Any,
    path: Path,
    range_header: str | None,
    *,
    content_type: str | None = None,
    extra_headers: Callable[[], None] | None = None,
    chunk_size: int = 64 * 1024,
) -> None:
    size = path.stat().st_size
    ctype = content_type or guess_media_type(path)
    try:
        span = parse_range(range_header, size)
    except ValueError:
        handler.send_response(416)
        handler.send_header("Content-Range", f"bytes */{size}")
        handler.send_header("Accept-Ranges", "bytes")
        handler.send_header("Content-Length", "0")
        if extra_headers:
            extra_headers()
        handler.end_headers()
        return
    if span is None:
        start, end = 0, size - 1 if size else 0
        status = 200
        length = size
    else:
        start, end = span
        status = 206
        length = end - start + 1
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(length))
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("Cache-Control", "private, max-age=0")
    if extra_headers:
        extra_headers()
    handler.end_headers()
    if handler.command == "HEAD":
        return
    with path.open("rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            chunk = fh.read(min(chunk_size, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)
