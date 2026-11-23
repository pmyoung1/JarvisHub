"""Speech-to-text backends. faster-whisper first, Vosk fallback. Lazy imports."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Protocol

from jarvis.voice.pcm import SAMPLE_RATE

LOG = logging.getLogger("jarvis.voice.stt")


class Transcriber(Protocol):
    name: str

    def transcribe(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> str:
        ...


class UnavailableSTT:
    name = "unavailable"

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def transcribe(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> str:
        del pcm, sample_rate
        raise RuntimeError(self.reason)


class FasterWhisperSTT:
    name = "faster-whisper"

    def __init__(
        self,
        model: str = "small",
        device: str = "cuda",
        compute_type: str = "int8_float16",
    ) -> None:
        from faster_whisper import WhisperModel  # type: ignore[import-not-found]

        if device == "cpu" and "float16" in (compute_type or ""):
            compute_type = "int8"  # CPUs do not support the GPU-oriented default
        self._whisper_cls = WhisperModel
        self._model_name = model
        self._device = device
        try:
            self._model = WhisperModel(model, device=device, compute_type=compute_type)
            self.name = f"faster-whisper:{model}:{device}"
        except Exception as exc:  # noqa: BLE001
            if device != "cpu":
                LOG.warning("faster-whisper %s failed (%s); retrying on CPU int8", device, exc)
                self._model = WhisperModel(model, device="cpu", compute_type="int8")
                self._device = "cpu"
                self.name = f"faster-whisper:{model}:cpu"
            else:
                raise

    def transcribe(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> str:
        import numpy as np

        # Hand faster-whisper a float32 array directly: no temp WAV, and no
        # dependency on PyAV's file decoding (which is picky about versions).
        if sample_rate != SAMPLE_RATE:
            raise ValueError(f"expected {SAMPLE_RATE} Hz PCM, got {sample_rate}")
        audio = np.frombuffer(pcm[: len(pcm) - (len(pcm) % 2)], dtype=np.int16).astype(np.float32) / 32768.0
        try:
            return self._run(audio)
        except RuntimeError as exc:
            # CTranslate2 loads fine without the CUDA runtime DLLs and only fails
            # when it first runs (cublas64_12.dll / cudnn not found). Drop to CPU once.
            if self._device == "cpu":
                raise
            LOG.warning("faster-whisper failed on %s (%s); switching to CPU int8", self._device, exc)
            self._model = self._whisper_cls(self._model_name, device="cpu", compute_type="int8")
            self._device = "cpu"
            self.name = f"faster-whisper:{self._model_name}:cpu"
            return self._run(audio)

    def _run(self, audio) -> str:  # noqa: ANN001
        segments, _info = self._model.transcribe(audio, language="en", vad_filter=True)
        parts = [seg.text.strip() for seg in segments if getattr(seg, "text", "").strip()]
        return " ".join(parts).strip()


class VoskSTT:
    name = "vosk"

    def __init__(self, model_path: str = "") -> None:
        import vosk  # type: ignore[import-not-found]

        vosk.SetLogLevel(-1)
        if not model_path:
            raise RuntimeError("Vosk model path is empty (set voice.stt_model to a local model directory)")
        path = Path(model_path)
        if not path.exists():
            raise RuntimeError(f"Vosk model not found at {path}")
        self._model = vosk.Model(str(path))
        self.name = f"vosk:{path.name}"

    def transcribe(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> str:
        import json
        import vosk  # type: ignore[import-not-found]

        rec = vosk.KaldiRecognizer(self._model, sample_rate)
        rec.SetWords(True)
        rec.AcceptWaveform(pcm)
        result = json.loads(rec.FinalResult() or "{}")
        return str(result.get("text") or "").strip()


def build_transcriber(engine: str, model: str, device: str, compute_type: str) -> Transcriber:
    engine = (engine or "faster-whisper").lower()
    errors: list[str] = []
    if engine in {"faster-whisper", "whisper", "auto"}:
        try:
            return FasterWhisperSTT(model=model or "small", device=device or "cuda", compute_type=compute_type or "int8_float16")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"faster-whisper: {exc}")
            LOG.warning("faster-whisper unavailable: %s", exc)
    if engine in {"vosk", "auto", "faster-whisper", "whisper"}:
        # Vosk needs a local model directory, so it is only a real fallback when voice.stt_model points at one.
        try:
            return VoskSTT(model_path=model if engine == "vosk" else "")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"vosk: {exc}")
            LOG.warning("Vosk unavailable: %s", exc)
    return UnavailableSTT("; ".join(errors) or f"no STT engine available ({engine})")
