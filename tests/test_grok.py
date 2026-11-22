"""Grok specialist tests against a mocked HTTP endpoint (no live xAI calls)."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jarvis.router import build_default_router
from jarvis.specialists.grok import GrokSpecialist
from tests.test_routing import FakeIndex


class MockHTTP:
    def __init__(self, responses: list[tuple[int, dict]]) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, url: str, headers: dict[str, str], body: bytes, timeout: float):
        self.calls.append({"url": url, "headers": headers, "body": json.loads(body), "timeout": timeout})
        if "Authorization" in headers:
            assert headers["Authorization"].startswith("Bearer ")
            assert "test-key" in headers["Authorization"]
        if not self.responses:
            return 500, {}, b"{}"
        status, payload = self.responses.pop(0)
        return status, {"content-type": "application/json"}, json.dumps(payload).encode("utf-8")


class GrokTests(unittest.TestCase):
    def test_disabled_without_key(self) -> None:
        grok = GrokSpecialist(api_key="", api_key_env="JARVIS_TEST_NO_SUCH_KEY")
        self.assertFalse(grok.enabled)
        self.assertFalse(grok.can_handle("tell me a joke", {}))
        router = build_default_router(media_index=FakeIndex())
        built = next(s for s in router.specialists if s.name == "grok")
        self.assertFalse(built.enabled)

    def test_chat_reply_from_responses_api(self) -> None:
        http = MockHTTP([(200, {"output_text": "Hello from the living room."})])
        grok = GrokSpecialist(api_key="test-key", http_post=http, media_index=FakeIndex())
        self.assertTrue(grok.enabled)
        out = grok.handle("hello there", {"client_id": "c1"})
        self.assertEqual(out["intent"], "grok.chat")
        self.assertIn("living room", out["reply"])
        self.assertTrue(http.calls[0]["url"].endswith("/responses"))
        self.assertEqual(http.calls[0]["body"]["model"], "grok-4.7")
        # Key must not appear in the JSON body.
        self.assertNotIn("test-key", json.dumps(http.calls[0]["body"]))

    def test_tool_call_play_media(self) -> None:
        http = MockHTTP(
            [
                (
                    200,
                    {
                        "output": [
                            {
                                "type": "function_call",
                                "name": "play_media",
                                "arguments": {"title": "Inception"},
                            }
                        ]
                    },
                ),
                (200, {"output_text": "Playing Inception on the TV."}),
            ]
        )
        grok = GrokSpecialist(api_key="test-key", http_post=http, media_index=FakeIndex())
        out = grok.handle("play inception", {"client_id": "c2"})
        self.assertEqual(out["intent"], "grok.chat")
        self.assertIn("Inception", out["reply"])
        self.assertEqual(out["data"]["tools"][0]["name"], "play_media")
        self.assertEqual(out["data"]["tools"][0]["result"]["title"], "Inception")

    def test_http_error_falls_back_offline(self) -> None:
        http = MockHTTP([(503, {"error": "busy"})])
        grok = GrokSpecialist(api_key="test-key", http_post=http)
        out = grok.handle("what's up", {"client_id": "c3"})
        self.assertEqual(out["intent"], "grok.offline")
        self.assertTrue(out["data"]["fallback"])
        self.assertIn("couldn't reach Grok", out["reply"])

    def test_chat_completions_fallback_on_404(self) -> None:
        http = MockHTTP(
            [
                (404, {"error": "not found"}),
                (200, {"choices": [{"message": {"content": "Via chat completions."}}]}),
            ]
        )
        grok = GrokSpecialist(api_key="test-key", http_post=http)
        out = grok.handle("hi", {"client_id": "c4"})
        self.assertEqual(out["reply"], "Via chat completions.")
        self.assertTrue(http.calls[1]["url"].endswith("/chat/completions"))

    def test_history_is_per_client(self) -> None:
        http = MockHTTP(
            [
                (200, {"output_text": "first"}),
                (200, {"output_text": "second"}),
            ]
        )
        grok = GrokSpecialist(api_key="test-key", http_post=http, history_turns=4)
        grok.handle("alpha", {"client_id": "a"})
        grok.handle("beta", {"client_id": "b"})
        second_input = http.calls[1]["body"]["input"]
        roles = [m.get("content") for m in second_input if isinstance(m, dict)]
        self.assertNotIn("alpha", roles)
