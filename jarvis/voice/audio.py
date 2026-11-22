"""Microphone capture via sounddevice (lazy). Used only in the voice agent process."""

from __future__ import annotations

import logging
from typing import Iterator

from jarvis.voice.pcm import BYTES_PER_FRAME, SAMPLE_RATE, SAMPLES_PER_FRAME

LOG = logging.getLogger("jarvis.voice.audio")


def list_devices() -> list[dict]:
    try:
        import sounddevice as sd  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001
        LOG.warning("sounddevice is not installed: %s", exc)
        return []
    devices = []
    for i, dev in enumerate(sd.query_devices()):
        devices.append(
            {
                "index": i,
                "name": dev.get("name"),
                "max_input_channels": dev.get("max_input_channels"),
                "max_output_channels": dev.get("max_output_channels"),
                "default_samplerate": dev.get("default_samplerate"),
            }
        )
    return devices


def iter_mic_frames(input_device: str = "", sample_rate: int = SAMPLE_RATE) -> Iterator[bytes]:
    import sounddevice as sd  # type: ignore[import-not-found]

    kwargs: dict = {
        "samplerate": sample_rate,
        "channels": 1,
        "dtype": "int16",
        "blocksize": SAMPLES_PER_FRAME,
    }
    if input_device:
        try:
            kwargs["device"] = int(input_device)
        except ValueError:
            kwargs["device"] = input_device
    with sd.RawInputStream(**kwargs) as stream:
        while True:
            frame, overflowed = stream.read(SAMPLES_PER_FRAME)
            if overflowed:
                LOG.debug("mic overflow")
            data = bytes(frame)
            if len(data) < BYTES_PER_FRAME:
                data = data + b"\x00" * (BYTES_PER_FRAME - len(data))
            yield data[:BYTES_PER_FRAME]
