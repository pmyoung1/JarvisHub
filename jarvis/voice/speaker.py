"""Speaker verification: SpeechBrain ECAPA primary, Resemblyzer fallback.

Enrollment embeddings are stored under data/voice/enrollments/ as JSON.
Heavy libraries are imported only when an encoder is constructed.
"""

from __future__ import annotations

import json
import logging
import math
import tempfile
from pathlib import Path
from typing import Any, Protocol

from jarvis.voice.pcm import SAMPLE_RATE, write_wav

LOG = logging.getLogger("jarvis.voice.speaker")
PROFILE_NAME = "profile.json"


class SpeakerEncoder(Protocol):
    name: str

    def embed(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> list[float]:
        ...


class EnrollmentStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / PROFILE_NAME

    def load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOG.warning("Could not read enrollment profile: %s", exc)
            return None

    def save(self, profile: dict[str, Any]) -> Path:
        self.path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
        return self.path

    def clear(self) -> None:
        if self.path.is_file():
            self.path.unlink()

    @property
    def centroid(self) -> list[float] | None:
        profile = self.load()
        if not profile:
            return None
        return profile.get("centroid") or None


def mean_l2(vectors: list[list[float]]) -> list[float]:
    if not vectors:
        raise ValueError("no enrollment embeddings")
    dim = len(vectors[0])
    acc = [0.0] * dim
    for vec in vectors:
        if len(vec) != dim:
            raise ValueError("embedding length mismatch")
        for i, value in enumerate(vec):
            acc[i] += float(value)
    n = float(len(vectors))
    mean = [v / n for v in acc]
    return l2_normalize(mean)


def l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True))


class EcapaEncoder:
    name = "ecapa"

    def __init__(self, savedir: Path | None = None) -> None:
        from speechbrain.inference.speaker import EncoderClassifier  # type: ignore[import-not-found]

        kwargs: dict[str, Any] = {
            "source": "speechbrain/spkrec-ecapa-voxceleb",
        }
        if savedir is not None:
            savedir.mkdir(parents=True, exist_ok=True)
            kwargs["savedir"] = str(savedir)
            kwargs["run_opts"] = {"device": "cpu"}
        self._encoder = EncoderClassifier.from_hparams(**kwargs)

    def embed(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> list[float]:
        import torch

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            write_wav(tmp_path, pcm, sample_rate)
            waveform = self._encoder.load_audio(str(tmp_path))
            if waveform.dim() == 1:
                waveform = waveform.unsqueeze(0)
            with torch.no_grad():
                emb = self._encoder.encode_batch(waveform)
            vec = emb.squeeze().cpu().tolist()
            if isinstance(vec, float):
                vec = [vec]
            return l2_normalize([float(x) for x in vec])
        finally:
            tmp_path.unlink(missing_ok=True)


class ResemblyzerEncoder:
    name = "resemblyzer"

    def __init__(self) -> None:
        from resemblyzer import VoiceEncoder  # type: ignore[import-not-found]

        self._encoder = VoiceEncoder()

    def embed(self, pcm: bytes, sample_rate: int = SAMPLE_RATE) -> list[float]:
        from resemblyzer import preprocess_wav  # type: ignore[import-not-found]
        import numpy as np

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        try:
            write_wav(tmp_path, pcm, sample_rate)
            wav = preprocess_wav(str(tmp_path))
            if hasattr(wav, "astype"):
                wav = np.asarray(wav)
            vec = self._encoder.embed_utterance(wav)
            return l2_normalize([float(x) for x in list(vec)])
        finally:
            tmp_path.unlink(missing_ok=True)


def build_speaker_encoder(engine: str, savedir: Path | None = None) -> SpeakerEncoder | None:
    """Return an encoder, falling back from ECAPA to Resemblyzer when asked."""
    engine = (engine or "ecapa").lower()
    if engine in {"none", "off", "disabled"}:
        return None
    errors: list[str] = []
    if engine in {"ecapa", "auto", "speechbrain"}:
        try:
            return EcapaEncoder(savedir=savedir)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"ECAPA unavailable: {exc}")
            LOG.warning("%s", errors[-1])
            if engine != "auto" and engine != "ecapa":
                return None
            # Design: Resemblyzer is the fallback if SpeechBrain is too heavy.
    if engine in {"ecapa", "auto", "resemblyzer"}:
        try:
            return ResemblyzerEncoder()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"Resemblyzer unavailable: {exc}")
            LOG.warning("%s", errors[-1])
    if engine == "resemblyzer":
        try:
            return ResemblyzerEncoder()
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Resemblyzer unavailable: %s", exc)
            return None
    return None


def verify_pcm(
    encoder: SpeakerEncoder,
    store: EnrollmentStore,
    pcm: bytes,
    threshold: float,
    sample_rate: int = SAMPLE_RATE,
) -> dict[str, Any]:
    centroid = store.centroid
    if centroid is None:
        return {
            "ok": True,
            "skipped": True,
            "reason": "no enrollment profile; speaker check skipped until you enroll",
            "score": None,
        }
    embedding = encoder.embed(pcm, sample_rate=sample_rate)
    score = cosine(l2_normalize(embedding), l2_normalize(centroid))
    accepted = score >= float(threshold)
    return {
        "ok": accepted,
        "skipped": False,
        "score": round(float(score), 4),
        "threshold": float(threshold),
        "engine": encoder.name,
    }
