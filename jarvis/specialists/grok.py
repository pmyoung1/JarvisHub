"""Grok API specialist (phase 2).

Enabled only when the API key environment variable is set (default
XAI_API_KEY). When the key is absent the specialist stays disabled so
existing tests and offline hubs keep working. HTTP is done with
stdlib urllib and is injectable for tests — never call the live xAI
API from unit tests.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from collections import defaultdict
from typing import Any, Callable

from jarvis.specialists.base import Specialist

LOG = logging.getLogger("jarvis.grok")

HttpPost = Callable[[str, dict[str, str], bytes, float], tuple[int, dict[str, str], bytes]]

SYSTEM_PROMPT = (
    "You are Jarvis, a household AI hub running on a local Windows PC. "
    "Answer briefly and helpfully. Use tools when the user wants to play or "
    "search the local movie library, list titles, or hear hub status. "
    "You cannot control lights or other smart-home devices yet. "
    "Do not claim to have powered the TV on or off — that is a later phase."
)

TOOL_SPECS = [
    {
        "type": "function",
        "name": "play_media",
        "description": "Resolve a movie title in the local library and start (or prepare) playback on the TV.",
        "parameters": {
            "type": "object",
            "properties": {"title": {"type": "string", "description": "Movie title to play"}},
            "required": ["title"],
        },
    },
    {
        "type": "function",
        "name": "search_media",
        "description": "Search the local movie library by title.",
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "type": "function",
        "name": "list_library",
        "description": "List titles in the local movie library.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "hub_status",
        "description": "Return local hub status (uptime, library size, voice, TV controller).",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "type": "function",
        "name": "start_transfer",
        "description": "Explain how to send a file from a phone to the TV through the hub.",
        "parameters": {
            "type": "object",
            "properties": {"filename": {"type": "string"}},
        },
    },
]


def default_http_post(url: str, headers: dict[str, str], body: bytes, timeout: float) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, {k.lower(): v for k, v in resp.headers.items()}, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, {k.lower(): v for k, v in (exc.headers.items() if exc.headers else [])}, exc.read()


class GrokSpecialist(Specialist):
    name = "grok"
    priority = 900

    def __init__(
        self,
        *,
        api_key: str | None = None,
        api_key_env: str = "XAI_API_KEY",
        base_url: str = "https://api.x.ai/v1",
        model: str = "grok-4.7",
        timeout_seconds: float = 30,
        history_turns: int = 8,
        provider_tools: bool = False,
        enabled_flag: bool = True,
        http_post: HttpPost | None = None,
        media_index: Any | None = None,
        context_tools: Callable[[], dict[str, Any]] | None = None,
        play_handler: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
    ) -> None:
        self.api_key_env = api_key_env or "XAI_API_KEY"
        self._api_key_override = api_key
        self.base_url = (base_url or "https://api.x.ai/v1").rstrip("/")
        self.model = model or "grok-4.7"
        self.timeout_seconds = float(timeout_seconds or 30)
        self.history_turns = max(0, int(history_turns or 0))
        self.provider_tools = bool(provider_tools)
        self._enabled_flag = bool(enabled_flag)
        self.http_post = http_post or default_http_post
        self.media_index = media_index
        self.context_tools = context_tools
        self.play_handler = play_handler
        self._history: dict[str, list[dict[str, str]]] = defaultdict(list)

    @classmethod
    def from_config(
        cls,
        grok_cfg: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> "GrokSpecialist":
        cfg = dict(grok_cfg or {})
        return cls(
            api_key_env=str(cfg.get("api_key_env") or "XAI_API_KEY"),
            base_url=str(cfg.get("base_url") or "https://api.x.ai/v1"),
            model=str(cfg.get("model") or "grok-4.7"),
            timeout_seconds=float(cfg.get("timeout_seconds") or 30),
            history_turns=int(cfg.get("history_turns") or 8),
            provider_tools=bool(cfg.get("provider_tools")),
            enabled_flag=bool(cfg.get("enabled", True)),
            **kwargs,
        )

    @property
    def api_key(self) -> str:
        if self._api_key_override:
            return self._api_key_override
        return (os.environ.get(self.api_key_env) or "").strip()

    @property
    def enabled(self) -> bool:
        return bool(self._enabled_flag and self.api_key)

    def can_handle(self, text: str, context: dict[str, Any]) -> bool:
        del context
        return self.enabled and bool((text or "").strip())

    def handle(self, text: str, context: dict[str, Any]) -> dict[str, Any]:
        if not self.enabled:
            return {
                "reply": "The Grok specialist is not enabled (no API key configured).",
                "intent": "grok.disabled",
                "data": {"enabled": False},
            }
        client_id = str((context or {}).get("client_id") or "default")
        try:
            reply, data = self._complete(text, client_id, context or {})
        except Exception as exc:  # noqa: BLE001 — never take down the hub
            LOG.warning("Grok request failed; falling back offline: %s", exc)
            return {
                "reply": "I couldn't reach Grok just now, so I'll stay with local replies. Try again, or use a library command like list movies.",
                "intent": "grok.offline",
                "data": {"error": str(exc), "fallback": True},
            }
        self._remember(client_id, "user", text)
        self._remember(client_id, "assistant", reply)
        return {"reply": reply, "intent": "grok.chat", "data": data}

    def _remember(self, client_id: str, role: str, content: str) -> None:
        if self.history_turns <= 0:
            return
        hist = self._history[client_id]
        hist.append({"role": role, "content": content})
        # user+assistant pairs; keep the last N turns
        max_items = self.history_turns * 2
        if len(hist) > max_items:
            del hist[:-max_items]

    def _complete(self, text: str, client_id: str, context: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages.extend(self._history[client_id])
        messages.append({"role": "user", "content": text})
        payload = {
            "model": self.model,
            "input": messages,
            "tools": TOOL_SPECS,
        }
        if not self.provider_tools:
            payload["tool_choice"] = "auto"
        status, _headers, body = self._post("/responses", payload)
        if status in {404, 405}:
            # Fall back to the chat-completions shape if /responses is unavailable.
            chat_payload = {
                "model": self.model,
                "messages": messages,
                "tools": [_chat_tool(t) for t in TOOL_SPECS],
            }
            status, _headers, body = self._post("/chat/completions", chat_payload)
        if status >= 400:
            raise RuntimeError(f"Grok API HTTP {status}")
        parsed = json.loads(body.decode("utf-8") or "{}")
        tool_calls = _extract_tool_calls(parsed)
        text_out = _extract_text(parsed)
        tool_results = []
        if tool_calls:
            for call in tool_calls:
                result = self._run_tool(call.get("name") or "", call.get("arguments") or {}, context)
                tool_results.append({"name": call.get("name"), "result": result})
            # One follow-up so the model can phrase the tool result.
            follow = {
                "model": self.model,
                "input": messages
                + [
                    {"role": "assistant", "content": text_out or "", "tool_calls": tool_calls},
                    {"role": "user", "content": "Tool results: " + json.dumps(tool_results, default=str)},
                ],
            }
            try:
                status, _headers, body = self._post("/responses", follow)
                if status < 400:
                    parsed = json.loads(body.decode("utf-8") or "{}")
                    text_out = _extract_text(parsed) or text_out
            except Exception as exc:  # noqa: BLE001
                LOG.debug("Grok follow-up skipped: %s", exc)
            if not text_out:
                text_out = _format_tool_results(tool_results)
        if not (text_out or "").strip():
            raise RuntimeError("Grok API returned an empty reply")
        return text_out.strip(), {"model": self.model, "tools": tool_results}

    def _post(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
        url = self.base_url + path
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        timeout = min(max(self.timeout_seconds, 5.0), 60.0)
        return self.http_post(url, headers, body, timeout)

    def _run_tool(self, name: str, args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
        if name == "search_media":
            idx = self.media_index
            if idx is None:
                return {"ok": False, "error": "media index unavailable"}
            query = str(args.get("query") or "")
            items = idx.search(query=query, limit=8, offset=0)
            return {"ok": True, "items": [_brief(i) for i in items], "query": query}
        if name == "list_library":
            idx = self.media_index
            if idx is None:
                return {"ok": False, "error": "media index unavailable"}
            total = idx.count()
            items = idx.search(query="", limit=12, offset=0)
            return {"ok": True, "total": total, "items": [_brief(i) for i in items]}
        if name == "play_media":
            title = str(args.get("title") or "")
            if self.play_handler:
                return self.play_handler(title, context)
            idx = self.media_index
            if idx is None:
                return {"ok": False, "error": "media index unavailable"}
            match = idx.resolve_title(title) if hasattr(idx, "resolve_title") else None
            if not match:
                return {"ok": False, "error": f'no library match for "{title}"'}
            return {
                "ok": True,
                "action": "would_play",
                "title": match.get("title"),
                "path": match.get("path"),
                "message": "Resolved a library title. Playback uses the TV controller when configured.",
            }
        if name == "hub_status":
            extra = self.context_tools() if self.context_tools else {}
            return {"ok": True, **{k: extra.get(k) for k in ("hub_name", "media_count", "scanning", "sse_clients", "uptime_seconds") if k in extra or True}}
        if name == "start_transfer":
            return {
                "ok": True,
                "message": (
                    "Open the phone dashboard Library tab, drop a file on the transfer "
                    "area, then open the incoming-files row on the TV to play it. "
                    "The phone never talks to the TV directly."
                ),
                "filename": args.get("filename"),
            }
        return {"ok": False, "error": f"unknown tool {name}"}


def _brief(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "title": item.get("title"),
        "year": item.get("year"),
        "has_poster": item.get("has_poster"),
    }


def _chat_tool(spec: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": spec["name"],
            "description": spec.get("description") or "",
            "parameters": spec.get("parameters") or {"type": "object", "properties": {}},
        },
    }


def _extract_text(parsed: dict[str, Any]) -> str:
    if not isinstance(parsed, dict):
        return ""
    if isinstance(parsed.get("output_text"), str) and parsed["output_text"].strip():
        return parsed["output_text"]
    choices = parsed.get("choices")
    if isinstance(choices, list) and choices:
        msg = (choices[0] or {}).get("message") or {}
        content = msg.get("content")
        if isinstance(content, str):
            return content
    output = parsed.get("output")
    if isinstance(output, list):
        parts: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "message":
                content = item.get("content")
                if isinstance(content, str):
                    parts.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("text"):
                            parts.append(str(block["text"]))
                        elif isinstance(block, str):
                            parts.append(block)
        return "\n".join(p for p in parts if p).strip()
    if isinstance(parsed.get("text"), str):
        return parsed["text"]
    return ""


def _extract_tool_calls(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    if not isinstance(parsed, dict):
        return calls
    choices = parsed.get("choices")
    if isinstance(choices, list) and choices:
        msg = (choices[0] or {}).get("message") or {}
        for raw in msg.get("tool_calls") or []:
            fn = (raw or {}).get("function") or raw or {}
            args = fn.get("arguments") or raw.get("arguments") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            calls.append({"name": fn.get("name") or raw.get("name"), "arguments": args or {}})
    output = parsed.get("output")
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            if item.get("type") in {"function_call", "tool_call"}:
                args = item.get("arguments") or item.get("input") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {}
                calls.append({"name": item.get("name"), "arguments": args or {}})
    return calls


def _format_tool_results(results: list[dict[str, Any]]) -> str:
    if not results:
        return "Done."
    first = results[0].get("result") or {}
    if first.get("message"):
        return str(first["message"])
    if first.get("title"):
        action = first.get("action") or "resolved"
        return f"{action}: {first.get('title')}"
    if first.get("items"):
        titles = ", ".join(str(i.get("title")) for i in first["items"][:8] if i.get("title"))
        return titles or "No titles found."
    return "Done."
