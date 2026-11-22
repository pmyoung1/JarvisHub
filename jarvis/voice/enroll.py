"""Enrollment CLI for Patrick's voice.

Examples:
  python -m jarvis.voice.enroll --files a.wav b.wav c.wav
  python -m jarvis.voice.enroll --record --clips 3 --seconds 8
  python -m jarvis.voice.enroll --list
  python -m jarvis.voice.enroll --clear
  python -m jarvis.voice.enroll --download-voice
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

from jarvis.config import load_config
from jarvis.logging_setup import setup_logging
from jarvis.voice.pcm import SAMPLE_RATE, read_wav, write_wav
from jarvis.voice.speaker import EnrollmentStore, build_speaker_encoder, mean_l2


def _encoder(cfg):
    savedir = cfg.data_dir / "voice" / "models"
    encoder = build_speaker_encoder(str(cfg.voice.get("speaker_engine") or "ecapa"), savedir=savedir)
    if encoder is None:
        raise SystemExit(
            "No speaker encoder is available. Install the voice extras:\n"
            "  .venv\\Scripts\\pip install -r requirements-voice.txt"
        )
    return encoder


def cmd_files(cfg, files: list[str], name: str) -> int:
    encoder = _encoder(cfg)
    store = EnrollmentStore(Path(cfg.voice["enrollments_dir"]))
    embeddings = []
    clips_meta = []
    for file in files:
        path = Path(file)
        pcm, rate = read_wav(path)
        emb = encoder.embed(pcm, sample_rate=rate)
        embeddings.append(emb)
        clips_meta.append({"path": str(path), "rate": rate, "bytes": len(pcm)})
        print(f"  enrolled {path} ({len(pcm)} bytes, {rate} Hz)")
    profile = {
        "name": name,
        "engine": encoder.name,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "clips": clips_meta,
        "embeddings": embeddings,
        "centroid": mean_l2(embeddings),
        "sample_rate": SAMPLE_RATE,
    }
    dest = store.save(profile)
    print(f"Saved enrollment profile for {name} ({len(embeddings)} clips) to {dest}")
    return 0


def cmd_record(cfg, clips: int, seconds: float, name: str) -> int:
    from jarvis.voice.audio import iter_mic_frames
    from jarvis.voice.pcm import BYTES_PER_FRAME

    encoder = _encoder(cfg)
    store = EnrollmentStore(Path(cfg.voice["enrollments_dir"]))
    raw_dir = Path(cfg.voice["enrollments_dir"]) / "clips"
    raw_dir.mkdir(parents=True, exist_ok=True)
    embeddings = []
    clips_meta = []
    frames_needed = int(seconds * SAMPLE_RATE * 2 / BYTES_PER_FRAME)
    print(f"Recording {clips} clips of {seconds} seconds. Pause ~0.5s, then speak a full sentence.")
    for i in range(clips):
        input(f"Press Enter to record clip {i + 1}/{clips}...")
        chunks = []
        for j, frame in enumerate(iter_mic_frames(str(cfg.voice.get("input_device") or ""))):
            chunks.append(frame)
            if j + 1 >= frames_needed:
                break
        pcm = b"".join(chunks)
        path = raw_dir / f"{name}_{int(time.time())}_{i + 1}.wav"
        write_wav(path, pcm)
        emb = encoder.embed(pcm)
        embeddings.append(emb)
        clips_meta.append({"path": str(path), "rate": SAMPLE_RATE, "bytes": len(pcm)})
        print(f"  saved {path}")
    profile = {
        "name": name,
        "engine": encoder.name,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "clips": clips_meta,
        "embeddings": embeddings,
        "centroid": mean_l2(embeddings),
        "sample_rate": SAMPLE_RATE,
    }
    dest = store.save(profile)
    print(f"Saved enrollment profile for {name} ({len(embeddings)} clips) to {dest}")
    return 0


def cmd_list(cfg) -> int:
    store = EnrollmentStore(Path(cfg.voice["enrollments_dir"]))
    profile = store.load()
    if not profile:
        print("No enrollment profile yet.")
        return 0
    print(f"Name:     {profile.get('name')}")
    print(f"Engine:   {profile.get('engine')}")
    print(f"Created:  {profile.get('created')}")
    print(f"Clips:    {len(profile.get('clips') or [])}")
    print(f"Vector:   {len(profile.get('centroid') or [])} dims")
    for clip in profile.get("clips") or []:
        print(f"  - {clip.get('path')} ({clip.get('bytes')} bytes)")
    return 0


def cmd_clear(cfg) -> int:
    store = EnrollmentStore(Path(cfg.voice["enrollments_dir"]))
    store.clear()
    print("Enrollment profile removed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enroll a speaker profile for the Jarvis voice agent")
    parser.add_argument("--config", help="Path to config.json")
    parser.add_argument("--name", default="patrick", help="Profile name (default: patrick)")
    parser.add_argument("--files", nargs="+", help="WAV files (16-bit PCM, preferably 16 kHz mono)")
    parser.add_argument("--record", action="store_true", help="Record clips from the default microphone")
    parser.add_argument("--clips", type=int, default=3, help="Number of clips when recording")
    parser.add_argument("--seconds", type=float, default=8.0, help="Seconds per recorded clip")
    parser.add_argument("--list", action="store_true", dest="list_profile")
    parser.add_argument("--clear", action="store_true")
    parser.add_argument("--download-voice", action="store_true", help="Download the configured Piper voice")
    args = parser.parse_args(argv)
    if args.config:
        os.environ["JARVIS_CONFIG"] = args.config
    cfg = load_config()
    setup_logging(cfg, name="jarvis.voice.enroll")
    if args.download_voice:
        from jarvis.voice.tts import download_piper_voice

        path = download_piper_voice(
            str(cfg.voice.get("tts_voice") or "en_US-lessac-medium"),
            Path(cfg.voice["tts_voices_dir"]),
        )
        print(f"Piper voice ready at {path}")
        return 0
    if args.list_profile:
        return cmd_list(cfg)
    if args.clear:
        return cmd_clear(cfg)
    if args.files:
        return cmd_files(cfg, args.files, args.name)
    if args.record:
        return cmd_record(cfg, args.clips, args.seconds, args.name)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
