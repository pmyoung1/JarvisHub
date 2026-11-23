"""Wake-word backends. openWakeWord is loaded lazily if installed."""

from __future__ import annotations

import logging
from typing import Any, Protocol

from jarvis.voice.pcm import SAMPLE_RATE, SAMPLES_PER_FRAME

LOG = logging.getLogger("jarvis.voice.wake")


class WakeEngine(Protocol):
    name: str

    def score(self, pcm_frame: bytes) -> float:
        """Return a probability/score in 0..1 for one 80 ms 16 kHz frame."""


class UnavailableWake:
    name = "unavailable"

    def __init__(self, reason: str = "openWakeWord is not installed") -> None:
        self.reason = reason

    def score(self, pcm_frame: bytes) -> float:
        del pcm_frame
        return 0.0


class OpenWakeWordEngine:
    name = "openwakeword"

    def __init__(self, model: str = "hey jarvis") -> None:
        # Lazy import — the hub process never needs this package.
        import openwakeword  # type: ignore[import-not-found]
        from openwakeword.model import Model  # type: ignore[import-not-found]

        try:
            openwakeword.utils.download_models()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("openWakeWord model download skipped or failed: %s", exc)
        # Model names in the library use spaces, e.g. "hey jarvis".
        self._model = Model(wakeword_models=[model] if model else None)
        self._label = model
        self.name = f"openwakeword:{model}"

    def reset(self) -> None:
        """Clear the model's rolling buffers so one utterance cannot trigger twice."""
        reset = getattr(self._model, "reset", None)
        if callable(reset):
            reset()

    def score(self, pcm_frame: bytes) -> float:
        import numpy as np

        if len(pcm_frame) < SAMPLES_PER_FRAME * 2:
            pcm_frame = pcm_frame + b"\x00" * (SAMPLES_PER_FRAME * 2 - len(pcm_frame))
        audio = np.frombuffer(pcm_frame[: SAMPLES_PER_FRAME * 2], dtype=np.int16)
        prediction = self._model.predict(audio)
        if not prediction:
            return 0.0
        if self._label in prediction:
            return float(prediction[self._label])
        return float(max(prediction.values()))


def build_wake_engine(engine: str, model: str) -> WakeEngine:
    engine = (engine or "openwakeword").lower()
    if engine in {"none", "off", "disabled"}:
        return UnavailableWake("wake word disabled")
    if engine in {"openwakeword", "oww", "hey jarvis"}:
        try:
            return OpenWakeWordEngine(model=model or "hey jarvis")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("openWakeWord unavailable: %s", exc)
            return UnavailableWake(str(exc))
    return UnavailableWake(f"unknown wake engine {engine}")


# Silence unused import warning if someone inspects SAMPLE_RATE.
_ = SAMPLE_RATE
