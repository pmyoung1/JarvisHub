"""Watchdog launcher: keeps the hub server alive across crashes.

The scheduled-task / startup entry point should be this module
(``python -m jarvis`` or ``python -m jarvis.launcher``). It spawns
``python -m jarvis.server`` as a child and restarts it on a non-zero
exit with exponential backoff. A clean zero exit (or a terminating
signal) stops the loop.

Task Scheduler "restart on failure" is a second safety net on Windows.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from jarvis.config import load_config
from jarvis.logging_setup import setup_logging

MIN_BACKOFF = 1.0
MAX_BACKOFF = 30.0

_child: subprocess.Popen | None = None
_stopping = False


def _handle_stop(signum, _frame) -> None:  # noqa: ANN001
    global _stopping
    _stopping = True
    if _child is not None and _child.poll() is None:
        try:
            _child.send_signal(signum)
        except (OSError, ValueError):
            _child.terminate()


def main(argv: list[str] | None = None) -> int:
    del argv  # reserved for future flags
    cfg = load_config()
    log = setup_logging(cfg, name="jarvis.launcher")
    pid_file = cfg.data_dir / "launcher.pid"
    pid_file.write_text(str(os.getpid()), encoding="utf-8")

    # SIGBREAK exists on Windows; SIGTERM/SIGINT everywhere we care about.
    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handle_stop)
            except (OSError, ValueError):
                pass

    cmd = [sys.executable, "-m", "jarvis.server"]
    cwd = Path(__file__).resolve().parent.parent
    env = os.environ.copy()
    if cfg.source is not None:
        env.setdefault("JARVIS_CONFIG", str(cfg.source))

    log.info("Launcher starting (pid %s). Child command: %s", os.getpid(), " ".join(cmd))
    backoff = MIN_BACKOFF
    exit_code = 0
    global _child

    try:
        while not _stopping:
            start = time.time()
            try:
                _child = subprocess.Popen(cmd, cwd=str(cwd), env=env)
            except OSError as exc:
                log.error("Failed to spawn server: %s", exc)
                return 1
            log.info("Server child pid %s started", _child.pid)
            (cfg.data_dir / "server.pid").write_text(str(_child.pid), encoding="utf-8")
            exit_code = _child.wait()
            _child = None
            if _stopping:
                log.info("Launcher received stop signal; not restarting.")
                break
            if exit_code == 0:
                log.info("Server exited cleanly (code 0). Launcher stopping.")
                break
            lived = time.time() - start
            log.warning(
                "Server crashed with code %s after %.1fs; restarting in %.1fs",
                exit_code,
                lived,
                backoff,
            )
            # Reset backoff if the process stayed up for a while.
            if lived >= 60:
                backoff = MIN_BACKOFF
            deadline = time.time() + backoff
            while not _stopping and time.time() < deadline:
                time.sleep(0.2)
            backoff = min(MAX_BACKOFF, backoff * 2)
    finally:
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass

    return 0 if _stopping else exit_code


if __name__ == "__main__":
    sys.exit(main())
