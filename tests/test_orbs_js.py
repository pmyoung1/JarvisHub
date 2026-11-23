"""Orb front end: pure helpers under node (if installed) plus page wiring checks."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


class OrbWiringTests(unittest.TestCase):
    def read(self, name: str) -> str:
        return (WEB / name).read_text(encoding="utf-8")

    def test_phone_and_tv_pages_load_orbs(self) -> None:
        for page in ("index.html", "tv.html"):
            html = self.read(page)
            self.assertIn('/css/orbs.css', html, page)
            self.assertIn('/js/orbs.js', html, page)
            self.assertIn('id="orbDock"', html, page)
            self.assertIn('id="talkbackAudio"', html, page)
            self.assertLess(html.index("/js/orbs.js"), html.index("/js/phone.js" if page == "index.html" else "/js/tv.js"))

    def test_page_scripts_init_orbs_and_forward_sse(self) -> None:
        for script, tv in (("js/phone.js", False), ("js/tv.js", True)):
            js = self.read(script)
            self.assertIn("JarvisOrbs.init", js)
            self.assertIn("JarvisOrbs.onVoice", js)
            self.assertIn("JarvisOrbs.onReply", js)
            self.assertEqual("tv: true" in js, tv)

    def test_orbs_use_analysers_gradients_and_sine_easing(self) -> None:
        js = self.read("js/orbs.js")
        for needle in ("createMediaStreamSource", "createMediaElementSource", "createAnalyser", "getByteTimeDomainData", "/api/tts", "isSecureContext", "easeInOutSine"):
            self.assertIn(needle, js)
        css = self.read("css/orbs.css")
        self.assertIn("radial-gradient", css)
        self.assertIn(".orb-blue", css)
        self.assertIn(".orb-orange", css)

    def test_orbs_served_by_hub(self) -> None:
        import tempfile
        import urllib.request

        from tests.support import TestHub

        with TestHub(Path(tempfile.mkdtemp(prefix="jarvis-orbs-"))) as hub:
            for path, needle in (("/js/orbs.js", b"JarvisOrbs"), ("/css/orbs.css", b"radial-gradient"), ("/", b"orbDock"), ("/tv", b"orbDock")):
                with urllib.request.urlopen(hub.base + path, timeout=10) as resp:
                    self.assertEqual(resp.status, 200, path)
                    self.assertIn(needle, resp.read(), path)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class OrbHelperTests(unittest.TestCase):
    def run_node(self, code: str):
        out = subprocess.run(
            ["node", "-e", f"const h=require({json.dumps(str(WEB / 'js' / 'orbs.js'))}); {code}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_sine_easing_endpoints_and_midpoint(self) -> None:
        a, b, c = self.run_node("console.log(JSON.stringify([h.easeInOutSine(0),h.easeInOutSine(0.5),h.easeInOutSine(1)]))")
        self.assertAlmostEqual(a, 0)
        self.assertAlmostEqual(b, 0.5)
        self.assertAlmostEqual(c, 1)

    def test_scale_is_flat_below_threshold_and_grows_with_level(self) -> None:
        quiet, mid, loud = self.run_node("console.log(JSON.stringify([h.scaleForLevel(0.005),h.scaleForLevel(0.12),h.scaleForLevel(0.5)]))")
        self.assertEqual(quiet, 1)
        self.assertGreater(mid, 1.1)
        self.assertGreater(loud, mid)
        self.assertLessEqual(loud, 1.56)

    def test_rms_of_time_domain_bytes(self) -> None:
        silent, full = self.run_node("console.log(JSON.stringify([h.rmsOfBytes(new Uint8Array(64).fill(128)),h.rmsOfBytes(new Uint8Array(64).fill(255))]))")
        self.assertEqual(silent, 0)
        self.assertGreater(full, 0.9)

    def test_script_has_no_syntax_errors(self) -> None:
        for name in ("orbs.js", "phone.js", "tv.js", "common.js"):
            out = subprocess.run(["node", "--check", str(WEB / "js" / name)], capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
