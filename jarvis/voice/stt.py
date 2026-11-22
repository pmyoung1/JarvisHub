"""Speech-to-text backends. faster-whisper first, Vosk fallback. Lazy imports."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Protocol

from jarvis.voice.pcm import SAMPLE_RATE, write_wav

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

        try:
            self._model = WhisperModel(model, device=device, compute_type=compute_type)
            self.name = f"faster-whisper:{model}:{device}"
        except Exception as exc:  # noqa: BLE001
            if device != "cpu":
                LOG.warning("faster-whisper %s failed (%s); retrying on CPU int8", device, exc)
                self._model = WhisperModel(model, device="cpu", compute_type="int8")
                self.name = f"faster-whisper:{model}:cpu"
            else:
                raise

    def transcribe(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> str:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            write_wav(tmp_path, pcm, sample_rate)
            segments, _info = self._model.transcribe(str(tmp_path), language="en", vad_filter=True)
            parts = [seg.text.strip() for seg in segments if getattr(seg, "text", "").strip()]
            return " ".join(parts).strip()
        finally:
            tmp_path.unlink(missing_ok=True)


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
    if engine in {"faster-whisper", "whisper", "auto"}:
        try:
            return FasterWhisperSTT(model=model or "small", device=device or "cuda", compute_type=compute_type or "int8_float16")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("faster-whisper unavailable: %s", exc)
            if engine != "auto":
                # Still try Vosk as a last-resort CPU fallback when auto or when whisper failed.
                pass
    if engine in {"vosk", "auto", "faster-whisper", "whisper"}:
        try:
            return VoskSTT(model_path=model if engine == "vosk" else "")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Vosk unavailable: %s", exc)
            if engine == "vosk":
                return UnavailableSTT(str(exc))
    if engine == "vosk":
        try:
            return VoskSTT(model_path=model)
        except Exception as exc:  # noqa: BLE001
            return UnavailableSTT(str(exc))
    return UnavailableSTT(f"no STT engine available ({engine})")
