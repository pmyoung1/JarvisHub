"""Single-listener lock so two microphone programs never answer the same wake word.

Both `python -m jarvis.voice` (the full voice agent) and `scripts/wakeword.py`
take this lock. The OS releases it when the process dies, so a crash never
leaves a stale lock (no PID probing: os.kill(pid, 0) would terminate a
process on Windows).
"""

from __future__ import annotations

import os
from pathlib import Path


class ListenerLock:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+b")  # noqa: SIM115 - held for the process lifetime
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        try:
            fh.seek(0)
            fh.truncate()
            fh.write(f"{os.getpid()}\n".encode())
            fh.flush()
        except OSError:
            pass
        return True

    def release(self) -> None:
        fh, self._fh = self._fh, None
        if fh is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        fh.close()

    def __enter__(self) -> "ListenerLock":
        if not self.acquire():
            raise RuntimeError(f"another Jarvis microphone listener holds {self.path}")
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()
