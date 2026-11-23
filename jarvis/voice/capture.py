"""Command capture after the wake word: energy VAD over 80 ms frames.

Shared by the full voice agent (jarvis/voice/agent.py) and the standalone
listener (scripts/wakeword.py) so both stop recording the same way.
"""

from __future__ import annotations

from typing import Callable, Iterator

from jarvis.voice.pcm import BYTES_PER_FRAME, SAMPLE_RATE, rms

# Simple energy VAD thresholds for 16-bit audio normalized 0..1.
VAD_START = 0.018
VAD_CONTINUE = 0.010
SILENCE_FRAMES_TO_STOP = 15  # about 1.2 s of quiet


def capture_command(
    frames: Iterator[bytes],
    command_seconds: float = 6.0,
    min_command_seconds: float = 1.0,
    should_stop: Callable[[], bool] | None = None,
) -> bytes:
    """Collect frames until trailing silence, the time limit, or should_stop()."""
    max_frames = max(1, int(command_seconds * SAMPLE_RATE * 2 / BYTES_PER_FRAME))
    min_frames = max(1, int(min_command_seconds * SAMPLE_RATE * 2 / BYTES_PER_FRAME))
    collected: list[bytes] = []
    silent = 0
    started = False
    for i, frame in enumerate(frames):
        if i >= max_frames or (should_stop is not None and should_stop()):
            break
        collected.append(frame)
        level = rms(frame)
        if level >= VAD_START:
            started = True
            silent = 0
        elif started and level < VAD_CONTINUE:
            silent += 1
            if len(collected) >= min_frames and silent >= SILENCE_FRAMES_TO_STOP:
                break
    return b"".join(collected)
