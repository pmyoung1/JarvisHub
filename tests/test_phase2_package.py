"""Guards that the shipped files match what the README, install guide and config example promise."""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.config import Config


class PackageTests(unittest.TestCase):
    def test_example_config_has_phase2_keys_and_stays_disabled(self) -> None:
        raw = json.loads((ROOT / "config.example.json").read_text(encoding="utf-8"))
        cfg = Config(raw)
        self.assertEqual(cfg.tv["controller"], "null")
        for key in ("ip", "mac", "token_file", "port"):
            self.assertIn(key, raw["tv"])
        self.assertIn("tts_model_path", raw["voice"])
        self.assertIn("tts", raw)
        self.assertEqual(raw["voice"]["tts_engine"], "piper")
        self.assertEqual(raw["voice"]["tts_fallback"], "sapi")
        self.assertEqual(cfg.tv["port"], 8002)

    def test_readme_phase2_table_points_at_real_files(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        section = readme[readme.index("## Phase 2 contents") : readme.index("### Which listener")]
        paths = set(re.findall(r"`((?:scripts|jarvis|web|docs)/[A-Za-z0-9_./-]+\.(?:py|ps1|js|css|html|md))`", section))
        paths |= {m.replace("\\", "/") for m in re.findall(r"`((?:scripts|jarvis|web|docs)\\[A-Za-z0-9_.\\-]+)`", section)}
        self.assertGreater(len(paths), 10)
        for rel in sorted(paths):
            self.assertTrue((ROOT / rel).is_file(), f"README mentions {rel} but it is not in the tree")
        for rel in re.findall(r"\]\((docs/[A-Za-z0-9_./-]+\.md)\)", readme):
            self.assertTrue((ROOT / rel).is_file(), rel)

    def test_install_guide_names_real_scripts_and_task(self) -> None:
        guide = (ROOT / "docs" / "INSTALL_WINDOWS.md").read_text(encoding="utf-8")
        for name in re.findall(r"\.\\scripts\\([A-Za-z_]+\.(?:ps1|py))", guide):
            self.assertTrue((ROOT / "scripts" / name).is_file(), name)
        service = (ROOT / "scripts" / "install_service.ps1").read_text(encoding="utf-8")
        task = re.search(r'\[string\]\$TaskName = "([^"]+)"', service).group(1)
        self.assertIn(f"Start-ScheduledTask -TaskName {task}", guide)
        voice = (ROOT / "scripts" / "install_voice_agent.ps1").read_text(encoding="utf-8")
        voice_task = re.search(r'\[string\]\$TaskName = "([^"]+)"', voice).group(1)
        self.assertIn(voice_task, guide)
        for req in re.findall(r"-r (requirements[a-z-]*\.txt)", guide):
            self.assertTrue((ROOT / req).is_file(), req)

    def test_powershell_scripts_are_ascii_for_windows_powershell_5(self) -> None:
        for path in (ROOT / "scripts").glob("*.ps1"):
            data = path.read_bytes()
            self.assertTrue(all(b < 128 for b in data), f"{path.name} has non-ASCII bytes (PowerShell 5.1 reads BOM-less files as ANSI)")

    def test_core_requirements_has_no_active_packages(self) -> None:
        active = [l for l in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines() if l.strip() and not l.lstrip().startswith("#")]
        self.assertEqual(active, [], "the hub core must stay standard-library only")
        for name, needles in (
            ("requirements-wakeword.txt", ("openwakeword", "piper-tts", "faster-whisper", "sounddevice")),
            ("requirements-tv.txt", ("samsungtvws",)),
            ("requirements-voice.txt", ("-r requirements-wakeword.txt", "speechbrain")),
        ):
            text = (ROOT / name).read_text(encoding="utf-8")
            for needle in needles:
                self.assertIn(needle, text, f"{name} should list {needle}")
        self.assertNotIn("edge-tts\n", (ROOT / "requirements-wakeword.txt").read_text(encoding="utf-8").replace("# (edge-tts", ""))

    def test_hub_core_imports_without_optional_packages(self) -> None:
        import subprocess

        code = (
            "import sys\n"
            "for m in ('openwakeword','piper','samsungtvws','faster_whisper','sounddevice','pyttsx3','vosk','speechbrain','torch'):\n"
            "    sys.modules[m] = None\n"
            "import jarvis.server, jarvis.tts_service, jarvis.tv.samsung, jarvis.tv.wol, jarvis.voice.listener, jarvis.voice.capture\n"
            "from jarvis.tv import build_tv_controller\n"
            "assert build_tv_controller('samsungtvws', {}, {'ip': ''}).status()['library_installed'] is False\n"
            "print('ok')\n"
        )
        out = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("ok", out.stdout)


if __name__ == "__main__":
    unittest.main()
