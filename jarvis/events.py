"""In-process Server-Sent Events fan-out for connected thin clients."""

from __future__ import annotations

import json
import queue
import threading
import time
from typing import Any, Iterator


class EventBus:
    """Thread-safe publish/subscribe used by the HTTP SSE endpoint."""

    def __init__(self, max_queue: int = 64) -> None:
        self._max_queue = max_queue
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()
        self._seq = 0

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=self._max_queue)
        with self._lock:
            self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def publish(self, event_type: str, payload: dict[str, Any]) -> int:
        with self._lock:
            self._seq += 1
            seq = self._seq
            targets = list(self._subscribers)
        message = dict(payload)
        message.setdefault("type", event_type)
        message["ts"] = time.time()
        message["seq"] = seq
        for q in targets:
            try:
                q.put_nowait((event_type, message))
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait((event_type, message))
                except queue.Full:
                    pass
        return seq

    def iter_sse(self, q: queue.Queue, keepalive: float = 15.0) -> Iterator[bytes]:
        """Yield SSE chunks, including comment keep-alives, until the client leaves."""
        yield _sse_comment("connected")
        while True:
            try:
                event_type, payload = q.get(timeout=keepalive)
            except queue.Empty:
                yield _sse_comment("ping")
                continue
            yield _sse_event(event_type, payload)


def _sse_event(event_type: str, payload: dict[str, Any]) -> bytes:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event_type}\ndata: {data}\n\n".encode("utf-8")


def _sse_comment(text: str) -> bytes:
    return f": {text}\n\n".encode("utf-8")
