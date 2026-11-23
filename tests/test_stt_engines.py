"""STT backends with a fake faster_whisper module (found by running the real wake -> STT -> hub path)."""

from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.voice import stt


class FakeSegment:
    def __init__(self, text: str) -> None:
        self.text = text


def install_fake(created: list, fail_cuda: bool = False):
    mod = types.ModuleType("faster_whisper")

    class WhisperModel:
        def __init__(self, model, device="cpu", compute_type="default"):
            if fail_cuda and device == "cuda":
                raise RuntimeError("no CUDA")
            created.append((model, device, compute_type))
            self.audio = None

        def transcribe(self, audio, **kw):
            self.audio = audio
            return iter([FakeSegment(" hello "), FakeSegment("world")]), None

    mod.WhisperModel = WhisperModel
    saved = sys.modules.get("faster_whisper")
    sys.modules["faster_whisper"] = mod
    return saved


def restore(saved) -> None:
    if saved is None:
        sys.modules.pop("faster_whisper", None)
    else:
        sys.modules["faster_whisper"] = saved


class FasterWhisperTests(unittest.TestCase):
    def test_cpu_never_gets_a_float16_compute_type(self) -> None:
        created: list = []
        saved = install_fake(created)
        try:
            engine = stt.FasterWhisperSTT("tiny.en", "cpu", "int8_float16")
        finally:
            restore(saved)
        self.assertEqual(created, [("tiny.en", "cpu", "int8")])
        self.assertIn("cpu", engine.name)

    def test_cuda_failure_falls_back_to_cpu_int8(self) -> None:
        created: list = []
        saved = install_fake(created, fail_cuda=True)
        try:
            engine = stt.FasterWhisperSTT("small", "cuda", "int8_float16")
        finally:
            restore(saved)
        self.assertEqual(created, [("small", "cpu", "int8")])
        self.assertIn("cpu", engine.name)

    @unittest.skipUnless(__import__("importlib").util.find_spec("numpy"), "numpy not installed")
    def test_transcribe_passes_a_float32_array_not_a_file(self) -> None:
        import numpy as np

        created: list = []
        saved = install_fake(created)
        try:
            engine = stt.FasterWhisperSTT("tiny.en", "cpu", "int8")
        finally:
            restore(saved)
        pcm = (np.array([0, 16384, -16384, 32767], dtype=np.int16)).tobytes()
        self.assertEqual(engine.transcribe(pcm), "hello world")
        audio = engine._model.audio
        self.assertEqual(audio.dtype, np.float32)
        self.assertAlmostEqual(float(audio[1]), 0.5, places=3)
        self.assertLessEqual(float(audio.max()), 1.0)


    @unittest.skipUnless(__import__("importlib").util.find_spec("numpy"), "numpy not installed")
    def test_missing_cuda_runtime_at_inference_time_falls_back_to_cpu(self) -> None:
        import numpy as np

        created: list = []
        mod = types.ModuleType("faster_whisper")

        class WhisperModel:
            def __init__(self, model, device="cpu", compute_type="default"):
                created.append((device, compute_type))
                self.device = device

            def transcribe(self, audio, **kw):
                if self.device == "cuda":
                    raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                return iter([FakeSegment("ok now")]), None

        mod.WhisperModel = WhisperModel
        saved = sys.modules.get("faster_whisper")
        sys.modules["faster_whisper"] = mod
        try:
            engine = stt.FasterWhisperSTT("small", "cuda", "int8_float16")
            self.assertEqual(engine.transcribe(np.zeros(1600, dtype=np.int16).tobytes()), "ok now")
        finally:
            restore(saved)
        self.assertEqual(created, [("cuda", "int8_float16"), ("cpu", "int8")])
        self.assertTrue(engine.name.endswith(":cpu"))


class BuildTranscriberTests(unittest.TestCase):
    def test_unavailable_reason_names_the_real_failures(self) -> None:
        saved = sys.modules.get("faster_whisper")
        sys.modules["faster_whisper"] = None  # import raises ImportError
        saved_vosk = sys.modules.get("vosk")
        sys.modules["vosk"] = None
        try:
            result = stt.build_transcriber("faster-whisper", "small", "cpu", "int8")
        finally:
            restore(saved)
            if saved_vosk is None:
                sys.modules.pop("vosk", None)
            else:
                sys.modules["vosk"] = saved_vosk
        self.assertIsInstance(result, stt.UnavailableSTT)
        self.assertIn("faster-whisper", result.reason)
        self.assertIn("vosk", result.reason)


if __name__ == "__main__":
    unittest.main()
