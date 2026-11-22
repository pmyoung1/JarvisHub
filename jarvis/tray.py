"""Optional stdlib tkinter helper for a laptop: open the dashboard or mute the hub mic."""

from __future__ import annotations

import json
import sys
import urllib.request
from jarvis.config import load_config


def _url(cfg, path: str) -> str:
    voice_url = str(cfg.voice.get("hub_url") or f"http://127.0.0.1:{cfg.port}")
    return voice_url.rstrip("/") + path


def _token(cfg) -> str:
    return (cfg.auth_tokens.get("laptop") or cfg.auth_token or "").strip()


def _request(cfg, method: str, path: str, payload: dict | None = None) -> dict:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Accept": "application/json"}
    token = _token(cfg)
    if token:
        headers["X-Hub-Token"] = token
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(_url(cfg, path), data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read().decode("utf-8") or "{}")


def main(argv: list[str] | None = None) -> int:
    del argv
    try:
        import tkinter as tk
        from tkinter import messagebox
        import webbrowser
    except Exception as exc:  # noqa: BLE001
        print(f"tkinter is not available: {exc}", file=sys.stderr)
        return 1
    cfg = load_config()
    root = tk.Tk()
    root.title("Jarvis Hub")
    root.resizable(False, False)
    status = tk.StringVar(value="Checking…")

    def refresh() -> None:
        try:
            body = _request(cfg, "GET", "/api/voice/status")
            muted = body.get("muted")
            state = body.get("state") or "unknown"
            status.set(f"{state}" + (" (muted)" if muted else ""))
        except Exception as exc:  # noqa: BLE001
            status.set(f"hub unreachable: {exc}")

    def open_dash() -> None:
        webbrowser.open(_url(cfg, "/laptop"))

    def toggle_mute() -> None:
        try:
            body = _request(cfg, "GET", "/api/voice/status")
            muted = not bool(body.get("muted"))
            _request(cfg, "POST", "/api/voice/mute", {"muted": muted})
            refresh()
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror("Jarvis", str(exc))

    tk.Label(root, textvariable=status, padx=16, pady=8).pack()
    tk.Button(root, text="Open dashboard", command=open_dash, width=24).pack(padx=16, pady=4)
    tk.Button(root, text="Toggle hub mute", command=toggle_mute, width=24).pack(padx=16, pady=4)
    tk.Button(root, text="Quit", command=root.destroy, width=24).pack(padx=16, pady=(4, 12))
    refresh()
    root.after(5000, refresh)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
