"""Talkback: Piper first (offline), then Windows SAPI via pyttsx3."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol

LOG = logging.getLogger("jarvis.voice.tts")

PIPER_VOICE_FILES = {
    "en_US-lessac-medium": (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx",
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json",
    ),
}


class Speaker(Protocol):
    name: str

    def speak(self, text: str) -> None:
        ...


class NullSpeaker:
    name = "null"

    def speak(self, text: str) -> None:
        LOG.info("TTS (null): %s", text)


class PiperSpeaker:
    name = "piper"

    def __init__(self, voice: str, voices_dir: Path, output_device: str = "") -> None:
        self.voice = voice or "en_US-lessac-medium"
        self.voices_dir = Path(voices_dir)
        self.output_device = output_device or ""
        self.onnx = self.voices_dir / f"{self.voice}.onnx"
        self.config = self.voices_dir / f"{self.voice}.onnx.json"
        if not self.onnx.is_file():
            raise FileNotFoundError(
                f"Piper voice not found at {self.onnx}. Run scripts/install_voice_agent.ps1 "
                "or python -m jarvis.voice.enroll --download-voice"
            )
        self._piper = None
        try:
            from piper import PiperVoice  # type: ignore[import-not-found]

            self._piper = PiperVoice.load(str(self.onnx), config_path=str(self.config) if self.config.is_file() else None)
        except Exception as exc:  # noqa: BLE001
            # Command-line piper is fine too.
            self._bin = shutil.which("piper") or shutil.which("piper.exe")
            if not self._bin:
                raise RuntimeError(f"piper Python package and piper binary both unavailable: {exc}") from exc
            LOG.info("Using piper executable at %s", self._bin)
        else:
            self._bin = None
        self.name = f"piper:{self.voice}"

    def speak(self, text: str) -> None:
        wav_path = self._synthesize(text)
        try:
            play_wav(wav_path, self.output_device)
        finally:
            wav_path.unlink(missing_ok=True)

    def _synthesize(self, text: str) -> Path:
        fd, raw = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        path = Path(raw)
        if self._piper is not None:
            with path.open("wb") as out:
                # piper-tts 1.x uses synthesize or synthesize_wav depending on version
                if hasattr(self._piper, "synthesize_wav"):
                    self._piper.synthesize_wav(text, out)
                else:
                    import wave

                    with wave.open(out, "wb") as wf:
                        self._piper.synthesize(text, wf)
            return path
        cmd = [self._bin, "--model", str(self.onnx), "--output_file", str(path)]
        if self.config.is_file():
            cmd.extend(["--config", str(self.config)])
        subprocess.run(cmd, input=text.encode("utf-8"), check=True, timeout=60)
        return path


class SapiSpeaker:
    name = "sapi"

    def __init__(self, output_device: str = "") -> None:
        import pyttsx3  # type: ignore[import-not-found]

        self._engine = pyttsx3.init()
        if output_device:
            try:
                for dev in self._engine.getProperty("voices") or []:
                    pass
                del dev
            except Exception:  # noqa: BLE001
                pass
        self.output_device = output_device
        self.name = "sapi:pyttsx3"

    def speak(self, text: str) -> None:
        self._engine.say(text)
        self._engine.runAndWait()


def play_wav(path: Path, output_device: str = "") -> None:
    """Play a WAV through sounddevice (preferred) or winsound on Windows."""
    try:
        import sounddevice  # type: ignore[import-not-found]
        import soundfile  # type: ignore[import-not-found]

        data, rate = soundfile.read(str(path), dtype="float32")
        kwargs = {}
        if output_device:
            try:
                kwargs["device"] = int(output_device)
            except ValueError:
                kwargs["device"] = output_device
        sounddevice.play(data, rate, **kwargs)
        sounddevice.wait()
        return
    except Exception as exc:  # noqa: BLE001
        LOG.debug("sounddevice playback unavailable: %s", exc)
    try:
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME)
        return
    except Exception as exc:  # noqa: BLE001
        LOG.debug("winsound playback unavailable: %s", exc)
    # Last resort: aplay / afplay on non-Windows test boxes.
    for bin_name, args in (("aplay", ["-q"]), ("afplay", []), ("paplay", [])):
        exe = shutil.which(bin_name)
        if exe:
            subprocess.run([exe, *args, str(path)], check=False, timeout=60)
            return
    LOG.warning("No audio playback device available; would have spoken from %s", path)


def build_speaker(engine: str, voice: str, voices_dir: Path, fallback: str, output_device: str = "") -> Speaker:
    engine = (engine or "piper").lower()
    fallback = (fallback or "sapi").lower()
    errors: list[str] = []
    if engine in {"piper", "auto"}:
        try:
            return PiperSpeaker(voice=voice, voices_dir=voices_dir, output_device=output_device)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Piper unavailable: {exc}")
            LOG.warning("%s", errors[-1])
    if engine in {"sapi", "pyttsx3"} or fallback in {"sapi", "pyttsx3"}:
        try:
            return SapiSpeaker(output_device=output_device)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"SAPI/pyttsx3 unavailable: {exc}")
            LOG.warning("%s", errors[-1])
    LOG.warning("No TTS engine available (%s); replies will not be spoken", "; ".join(errors) or engine)
    return NullSpeaker()


def download_piper_voice(voice: str, voices_dir: Path) -> Path:
    """Fetch a known Piper voice into tts_voices_dir (used by the install script)."""
    from urllib.request import urlopen

    voice = voice or "en_US-lessac-medium"
    urls = PIPER_VOICE_FILES.get(voice)
    if not urls:
        raise ValueError(f"Unknown Piper voice {voice}; add it to PIPER_VOICE_FILES or download manually")
    voices_dir.mkdir(parents=True, exist_ok=True)
    onnx = voices_dir / f"{voice}.onnx"
    cfg = voices_dir / f"{voice}.onnx.json"
    for dest, url in ((onnx, urls[0]), (cfg, urls[1])):
        if dest.is_file() and dest.stat().st_size > 0:
            continue
        LOG.info("Downloading %s", url)
        with urlopen(url, timeout=120) as resp:  # noqa: S310 — pinned official Hugging Face URLs
            dest.write_bytes(resp.read())
    return onnx
