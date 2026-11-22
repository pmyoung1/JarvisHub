"""Minimal multipart/form-data reader for phone uploads (stdlib only)."""

from __future__ import annotations

from dataclasses import dataclass
from email.parser import BytesParser
from email.policy import default as email_policy


@dataclass
class UploadedFile:
    field_name: str
    filename: str
    content_type: str
    data: bytes


def parse_multipart(content_type: str, body: bytes) -> list[UploadedFile]:
    if not content_type.lower().startswith("multipart/"):
        raise ValueError("expected multipart/form-data")
    # email parser wants a full message with headers
    message = BytesParser(policy=email_policy).parsebytes(
        f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode("utf-8") + body
    )
    files: list[UploadedFile] = []
    if not message.is_multipart():
        raise ValueError("no multipart parts")
    for part in message.iter_parts():
        disp = part.get("Content-Disposition") or ""
        if "form-data" not in disp.lower() and not part.get_filename():
            continue
        filename = part.get_filename() or ""
        name = part.get_param("name", header="Content-Disposition") or ""
        payload = part.get_payload(decode=True) or b""
        ctype = part.get_content_type() or "application/octet-stream"
        if filename or name in {"file", "upload", "media"}:
            files.append(
                UploadedFile(
                    field_name=str(name),
                    filename=str(filename or name or "upload"),
                    content_type=str(ctype),
                    data=payload,
                )
            )
    return files
