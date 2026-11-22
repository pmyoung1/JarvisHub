"""PCM / WAV helpers. Avoid audioop (removed in Python 3.13)."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path


SAMPLE_RATE = 16_000
SAMPLE_WIDTH = 2
CHANNELS = 1
FRAME_MS = 80
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 1280
BYTES_PER_FRAME = SAMPLES_PER_FRAME * SAMPLE_WIDTH


def rms(pcm: bytes) -> float:
    if len(pcm) < 2:
        return 0.0
    count = len(pcm) // 2
    # Unpack in chunks to avoid a huge struct format on long clips.
    total = 0.0
    offset = 0
    while offset + 2 <= len(pcm):
        chunk = pcm[offset : offset + 2048 * 2]
        n = len(chunk) // 2
        if n:
            samples = struct.unpack("<" + "h" * n, chunk[: n * 2])
            total += sum(s * s for s in samples)
        offset += n * 2
    return math.sqrt(total / max(1, count)) / 32768.0


def write_wav(path: Path, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)


def read_wav(path: Path) -> tuple[bytes, int]:
    with wave.open(str(path), "rb") as wf:
        rate = wf.getframerate()
        width = wf.getsampwidth()
        chans = wf.getnchannels()
        frames = wf.readframes(wf.getnframes())
    if width != 2:
        raise ValueError(f"{path} must be 16-bit PCM")
    if chans == 2:
        # Downmix stereo to mono.
        samples = struct.unpack("<" + "h" * (len(frames) // 2), frames)
        mono = [(samples[i] + samples[i + 1]) // 2 for i in range(0, len(samples) - 1, 2)]
        frames = struct.pack("<" + "h" * len(mono), *mono)
    elif chans != 1:
        raise ValueError(f"{path} must be mono or stereo")
    return frames, rate


def silence(seconds: float, sample_rate: int = SAMPLE_RATE) -> bytes:
    n = int(seconds * sample_rate)
    return b"\x00\x00" * n


def sine_pcm(seconds: float, freq: float = 440.0, amplitude: float = 0.2, sample_rate: int = SAMPLE_RATE) -> bytes:
    n = int(seconds * sample_rate)
    samples = [
        int(32767 * amplitude * math.sin(2 * math.pi * freq * i / sample_rate))
        for i in range(n)
    ]
    return struct.pack("<" + "h" * n, *samples)
