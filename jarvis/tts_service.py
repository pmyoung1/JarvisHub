"""Hub-side talkback synthesis: text in, WAV bytes out.

Used by GET/POST /api/tts so the phone and TV dashboards can play the
reply through an <audio> element. Backends are tried in this order and all
imports are lazy, so the hub still starts (and /api/tts answers 503 with a
clear message) when none of them is installed:

  1. Piper (offline neural voice)       needs piper-tts + a voice .onnx file
  2. Windows SAPI via System.Speech     Windows only, no Python packages
  3. pyttsx3                            SAPI on Windows, espeak elsewhere

edge-tts is deliberately not an option because it needs Microsoft's
online service.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any, Callable

from jarvis.config import Config

LOG = logging.getLogger("jarvis.tts")


class TtsUnavailable(RuntimeError):
    """No backend could produce audio."""


class TtsService:
    def __init__(
        self,
        cfg: Config,
        piper_factory: Callable[[], Any] | None = None,
        sapi_factory: Callable[[], Any] | None = None,
        pyttsx3_factory: Callable[[], Any] | None = None,
    ) -> None:
        tts_cfg = getattr(cfg, "tts", {}) or {}
        self.enabled = bool(tts_cfg.get("enabled", True))
        self.max_chars = int(tts_cfg.get("max_chars") or 500)
        self._cache_items = int(tts_cfg.get("cache_items") or 32)
        voice = cfg.voice
        self._engine_pref = str(voice.get("tts_engine") or "piper").lower()
        self._voice = str(voice.get("tts_voice") or "en_US-lessac-medium")
        self._voices_dir = Path(voice["tts_voices_dir"])
        self._model_path = str(voice.get("tts_model_path") or "")
        self._piper_factory = piper_factory or self._default_piper
        self._sapi_factory = sapi_factory or self._default_sapi
        self._pyttsx3_factory = pyttsx3_factory or self._default_pyttsx3
        self._backends: list[tuple[str, Any]] | None = None
        self._errors: list[str] = []
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, tuple[bytes, str]] = OrderedDict()
        self.last_engine = ""

    # ---- backend construction (lazy, once) ------------------------------

    def _default_piper(self) -> Any:
        from jarvis.voice.tts import PiperSpeaker

        return PiperSpeaker(voice=self._voice, voices_dir=self._voices_dir, model_path=self._model_path)

    @staticmethod
    def _default_sapi() -> Any:
        from jarvis.voice.tts import SapiPowerShellSynth

        return SapiPowerShellSynth()

    @staticmethod
    def _default_pyttsx3() -> Any:
        from jarvis.voice.tts import SapiSpeaker

        return SapiSpeaker()

    def _load(self) -> list[tuple[str, Any]]:
        if self._backends is not None:
            return self._backends
        backends: list[tuple[str, Any]] = []
        order = ["piper", "sapi", "pyttsx3"]
        if self._engine_pref in {"sapi", "pyttsx3"}:
            order = ["sapi", "pyttsx3", "piper"]
        factories = {
            "piper": self._piper_factory,
            "sapi": self._sapi_factory,
            "pyttsx3": self._pyttsx3_factory,
        }
        for key in order:
            try:
                backends.append((key, factories[key]()))
            except Exception as exc:  # noqa: BLE001
                self._errors.append(f"{key}: {exc}")
                LOG.info("TTS backend %s unavailable: %s", key, exc)
        self._backends = backends
        return backends

    # ---- public API -------------------------------------------------------

    def synthesize(self, text: str) -> tuple[bytes, str]:
        """Return (wav_bytes, engine_name). Raises TtsUnavailable."""
        if not self.enabled:
            raise TtsUnavailable("talkback is disabled (tts.enabled is false)")
        text = " ".join((text or "").split())
        if not text:
            raise ValueError("text is required")
        if len(text) > self.max_chars:
            text = text[: self.max_chars].rsplit(" ", 1)[0] or text[: self.max_chars]
        key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            backends = self._load()
        failures: list[str] = []
        for name, backend in backends:
            try:
                data = backend.synthesize_wav_bytes(text)
            except Exception as exc:  # noqa: BLE001
                failures.append(f"{name}: {exc}")
                LOG.warning("TTS backend %s failed, trying the next one: %s", name, exc)
                continue
            if not data:
                failures.append(f"{name}: empty audio")
                continue
            engine = str(getattr(backend, "name", name))
            with self._lock:
                self._cache[key] = (data, engine)
                while len(self._cache) > self._cache_items:
                    self._cache.popitem(last=False)
                self.last_engine = engine
            return data, engine
        detail = "; ".join(failures or self._errors) or "no engine installed"
        raise TtsUnavailable(
            "No talkback engine could speak: "
            + detail
            + ". Install requirements-wakeword.txt and download a Piper voice (docs/INSTALL_WINDOWS.md)."
        )

    def status(self) -> dict[str, Any]:
        """Cheap description for /api/status. Never loads a voice model."""
        model = Path(self._model_path) if self._model_path else self._voices_dir / f"{self._voice}.onnx"
        return {
            "enabled": self.enabled,
            "engine_preference": self._engine_pref,
            "piper_package": importlib.util.find_spec("piper") is not None,
            "piper_voice_present": model.is_file(),
            "piper_voice": model.name,
            "last_engine": self.last_engine or None,
        }
