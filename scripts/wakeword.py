#!/usr/bin/env python3
"""Jarvis wake-word listener: say "hey jarvis", speak a command, it goes to the hub.

Runs fully offline: openWakeWord (built-in "hey jarvis" model) hears the wake
word, faster-whisper (or Vosk) turns the next few seconds into text, and the
text is POSTed to the hub's POST /api/command.

IMPORTANT - where this runs: this program must run in your *logged-in user
session* (a Startup-folder shortcut, see scripts/install_wakeword_startup.ps1),
NOT inside the always-on JarvisHub boot task. That task runs as SYSTEM in
Session 0, and Windows does not give Session 0 services access to the
microphone. The hub itself keeps running as the boot service; only this
listener waits for you to log in.

It is the lightweight alternative to `python -m jarvis.voice` (the full voice
agent, which adds speaker verification and spoken replies on the hub PC). Run
one or the other, not both: they share a lock and the second one exits.

Examples
  python scripts\\wakeword.py --list-devices
  python scripts\\wakeword.py --dry-run          # hear + transcribe, print, send nothing
  python scripts\\wakeword.py --hub-url http://192.168.1.50:8765 --token SECRET
  python scripts\\wakeword.py --threshold 0.6 --input-device 3
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from jarvis.config import load_config  # noqa: E402
from jarvis.voice.client import HubClient  # noqa: E402
from jarvis.voice.listener import WakewordListener  # noqa: E402
from jarvis.voice.lock import ListenerLock  # noqa: E402
from jarvis.voice.pcm import BYTES_PER_FRAME  # noqa: E402
from jarvis.voice.stt import UnavailableSTT, build_transcriber  # noqa: E402
from jarvis.voice.wake import UnavailableWake, build_wake_engine  # noqa: E402

LOG = logging.getLogger("jarvis.wakeword")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Offline 'hey jarvis' listener that posts commands to the hub")
    p.add_argument("--config", help="path to config.json (default: ./config.json or JARVIS_CONFIG)")
    p.add_argument("--hub-url", help="hub base URL (default: voice.hub_url from config, http://127.0.0.1:8765)")
    p.add_argument("--token", help="device token sent as X-Hub-Token (default: $JARVIS_TOKEN, then auth_tokens.voice / auth_token)")
    p.add_argument("--threshold", type=float, help="wake score 0..1 needed to trigger (default: voice.wake_threshold, 0.5)")
    p.add_argument("--list-devices", action="store_true", help="print audio devices and exit")
    p.add_argument("--dry-run", action="store_true", help="listen and transcribe but do not POST to the hub")
    p.add_argument("--input-device", help="microphone device index or name (default: voice.input_device / system default)")
    p.add_argument("--source", default="wakeword", help="'source' field sent with each command (default: wakeword)")
    p.add_argument("--speak", action="store_true", help="also speak the hub's reply here with Piper/SAPI (off by default)")
    p.add_argument("--stt-model", help="faster-whisper model (default: voice.stt_model)")
    p.add_argument("--stt-device", help="cuda or cpu (default: voice.stt_device)")
    p.add_argument("--wav", help="test mode: read audio from a 16 kHz mono 16-bit WAV instead of the microphone")
    p.add_argument("--log-file", help="also write logs here (rotating)")
    p.add_argument("--allow-multiple", action="store_true", help="skip the single-listener lock (not recommended)")
    return p.parse_args(argv)


def setup_logging(log_file: str | None) -> None:
    logger = logging.getLogger("jarvis")
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logger.addHandler(console)
    if log_file:
        path = Path(log_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(path, maxBytes=1_048_576, backupCount=3, encoding="utf-8")
        fh.setFormatter(fmt)
        logger.addHandler(fh)
    logger.propagate = False

    def _crash(exc_type, exc, tb):  # noqa: ANN001
        logger.critical("Unhandled exception", exc_info=(exc_type, exc, tb))

    sys.excepthook = _crash


def resolve_settings(args: argparse.Namespace, cfg) -> dict:  # noqa: ANN001
    voice = cfg.voice
    token = (
        args.token
        or os.environ.get("JARVIS_TOKEN", "")
        or cfg.auth_tokens.get("voice", "")
        or cfg.auth_token
        or ""
    ).strip()
    return {
        "hub_url": (args.hub_url or voice.get("hub_url") or f"http://127.0.0.1:{cfg.port}").rstrip("/"),
        "token": token,
        "threshold": float(args.threshold if args.threshold is not None else voice.get("wake_threshold") or 0.5),
        "input_device": str(args.input_device if args.input_device is not None else voice.get("input_device") or ""),
        "client_id": f"wakeword-{socket.gethostname().lower()}",
    }


def wav_frames(path: str):
    import wave

    with wave.open(path, "rb") as wf:
        if (wf.getframerate(), wf.getnchannels(), wf.getsampwidth()) != (16000, 1, 2):
            raise SystemExit("--wav must be 16 kHz, mono, 16-bit PCM")
        while True:
            data = wf.readframes(BYTES_PER_FRAME // 2)
            if not data:
                return
            yield data.ljust(BYTES_PER_FRAME, b"\x00")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.config:
        os.environ["JARVIS_CONFIG"] = args.config
    cfg = load_config()
    setup_logging(args.log_file)

    if args.list_devices:
        from jarvis.voice.audio import list_devices

        devices = list_devices()
        if not devices:
            print("No audio devices found (is sounddevice installed? pip install -r requirements-wakeword.txt)")
            return 1
        for dev in devices:
            print(f"{dev['index']}: {dev['name']}  in={dev['max_input_channels']} out={dev['max_output_channels']}")
        return 0

    settings = resolve_settings(args, cfg)
    voice = cfg.voice

    lock = ListenerLock(cfg.data_dir / "voice" / "listener.lock")
    if not args.allow_multiple and not lock.acquire():
        LOG.error(
            "Another Jarvis microphone listener (the voice agent or a second wakeword.py) is already running. "
            "Run only one. Exiting."
        )
        return 3

    wake = build_wake_engine(str(voice.get("wake_engine") or "openwakeword"), str(voice.get("wake_model") or "hey jarvis"))
    if isinstance(wake, UnavailableWake):
        LOG.error("Wake word engine unavailable: %s. Run: .venv\\Scripts\\pip install -r requirements-wakeword.txt", wake.reason)
        return 2
    transcriber = build_transcriber(
        str(voice.get("stt_engine") or "faster-whisper"),
        str(args.stt_model or voice.get("stt_model") or "small"),
        str(args.stt_device or voice.get("stt_device") or "cuda"),
        str(voice.get("stt_compute_type") or "int8_float16"),
    )
    if isinstance(transcriber, UnavailableSTT):
        LOG.error("Speech-to-text unavailable: %s. Run: .venv\\Scripts\\pip install -r requirements-wakeword.txt", transcriber.reason)
        return 2

    client = None if args.dry_run else HubClient(settings["hub_url"], token=settings["token"], client_id=settings["client_id"])

    speaker = None
    if args.speak:
        from jarvis.voice.tts import build_speaker

        speaker = build_speaker(
            str(voice.get("tts_engine") or "piper"),
            str(voice.get("tts_voice") or "en_US-lessac-medium"),
            Path(voice["tts_voices_dir"]),
            str(voice.get("tts_fallback") or "sapi"),
            str(voice.get("output_device") or ""),
            model_path=str(voice.get("tts_model_path") or ""),
        )

    listener = WakewordListener(
        wake,
        transcriber,
        client,
        threshold=settings["threshold"],
        source=args.source,
        dry_run=args.dry_run,
        command_seconds=float(voice.get("command_seconds") or 6.0),
        min_command_seconds=float(voice.get("min_command_seconds") or 1.0),
        speaker=speaker,
    )

    def _stop(*_a: object) -> None:
        listener.stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    if args.wav:
        frames = wav_frames(args.wav)
    else:
        from jarvis.voice.audio import iter_mic_frames

        frames = iter_mic_frames(settings["input_device"])

    LOG.info(
        "Listening for '%s' (wake=%s threshold=%.2f stt=%s) -> %s%s",
        voice.get("wake_model") or "hey jarvis",
        getattr(wake, "name", "?"),
        settings["threshold"],
        getattr(transcriber, "name", "?"),
        "(dry run, nothing is sent)" if args.dry_run else settings["hub_url"] + "/api/command",
        " [token set]" if settings["token"] and not args.dry_run else "",
    )
    try:
        return listener.run(frames)
    except KeyboardInterrupt:
        return 0
    finally:
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
