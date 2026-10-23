from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import httpx
from mcp import ClientSession

DEFAULT_MODEL = "claude-sonnet-5-5"

SYSTEM_PROMPT = (
    "You write Kubernetes manifests. Always check your draft with the validate_manifest tool "
    "before answering. If it reports ERROR issues, fix them and validate again. When the "
    "manifest is valid, reply with only the final YAML and nothing else."
)


def to_anthropic_tools(mcp_tools: Any) -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description or "", "input_schema": t.inputSchema}
        for t in mcp_tools.tools
    ]


def _tool_result_text(result: Any) -> str:
    return "\n".join(c.text for c in result.content if getattr(c, "text", None))


async def run_agent(
    task: str,
    session: ClientSession,
    client: Any,
    model: str = DEFAULT_MODEL,
    max_rounds: int = 8,
) -> str:
    """Let the model call MCP tools until it produces a final answer."""
    tools = to_anthropic_tools(await session.list_tools())
    messages: list[dict[str, Any]] = [{"role": "user", "content": task}]

    for _ in range(max_rounds):
        response = await client.messages.create(
            model=model, max_tokens=2048, system=SYSTEM_PROMPT, tools=tools, messages=messages
        )
        messages.append({"role": "assistant", "content": response.content})

        calls = [b for b in response.content if b.type == "tool_use"]
        if not calls:
            return "".join(b.text for b in response.content if b.type == "text")

        results = []
        for call in calls:
            result = await session.call_tool(call.name, call.input)
            results.append({
                "type": "tool_result",
                "tool_use_id": call.id,
                "content": _tool_result_text(result) or json.dumps(result.structuredContent),
                "is_error": bool(result.isError),
            })
        messages.append({"role": "user", "content": results})

    raise RuntimeError(f"Agent did not finish within {max_rounds} rounds")


# --- OpenAI-compatible backend (OpenAI, Gemini, Ollama, vLLM) ----------------------------------

RETRYABLE = {429, 500, 502, 503, 504}
MAX_WAIT = 90.0  # a longer wait means a daily quota, not a rate spike: fail instead of hanging
_UNSUPPORTED_SCHEMA_KEYS = {"title", "additionalProperties", "$schema"}


def _clean_schema(node: Any) -> Any:
    """Drop JSON Schema keys that some OpenAI-compatible servers (notably Gemini's) reject."""
    if isinstance(node, dict):
        return {k: _clean_schema(v) for k, v in node.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(node, list):
        return [_clean_schema(v) for v in node]
    return node


def to_openai_tools(mcp_tools: Any) -> list[dict[str, Any]]:
    return [
        {"type": "function", "function": {
            "name": t.name, "description": t.description or "",
            "parameters": _clean_schema(t.inputSchema)}}
        for t in mcp_tools.tools
    ]


class OpenAICompatChat:
    """One chat-completions call with tools, retrying 429/5xx and surfacing error bodies."""

    def __init__(self, model: str, base_url: str = "https://api.openai.com/v1",
                 api_key: str | None = None, temperature: float = 0.0,
                 client: httpx.AsyncClient | None = None, timeout: float = 120.0,
                 retries: int = 5) -> None:
        self.model, self.temperature, self.retries = model, temperature, retries
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = client or httpx.AsyncClient(timeout=timeout)

    async def complete(self, messages: list[dict[str, Any]],
                       tools: list[dict[str, Any]]) -> dict[str, Any]:
        body = {"model": self.model, "messages": messages, "tools": tools,
                "temperature": self.temperature}
        resp = await self.client.post(self.url, headers=self.headers, json=body)
        for attempt in range(self.retries):
            if resp.status_code not in RETRYABLE:
                break
            hinted = resp.headers.get("retry-after") or (
                m[1] if (m := re.search(r'"retryDelay":\s*"([\d.]+)s"', resp.text)) else None)
            try:
                delay = float(hinted)
            except (TypeError, ValueError):
                delay = min(5 * 2 ** attempt, MAX_WAIT)
            if delay > MAX_WAIT:
                break
            await asyncio.sleep(delay)
            resp = await self.client.post(self.url, headers=self.headers, json=body)
        if resp.is_error:
            raise httpx.HTTPStatusError(f"{resp.status_code} from {self.url}: {resp.text[:300]}",
                                        request=resp.request, response=resp)
        return resp.json()["choices"][0]["message"]


async def run_agent_openai(
    task: str,
    session: ClientSession,
    chat: Any,
    max_rounds: int = 8,
    system: str = SYSTEM_PROMPT,
) -> str:
    """Same loop as run_agent, over an OpenAI-style tool-calling API.

    The assistant message is sent back exactly as received: some providers attach opaque
    per-call metadata (for example Gemini's thought signatures) that must be echoed.
    """
    tools = to_openai_tools(await session.list_tools())
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": task},
    ]
    for _ in range(max_rounds):
        message = await chat.complete(messages, tools)
        messages.append(message)
        calls = message.get("tool_calls") or []
        if not calls:
            return message.get("content") or ""
        for call in calls:
            try:
                args = json.loads(call["function"].get("arguments") or "{}")
                result = await session.call_tool(call["function"]["name"], args)
                content = _tool_result_text(result) or json.dumps(result.structuredContent)
            except (ValueError, KeyError) as exc:
                content = f"Invalid tool call: {exc}"
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": content})
    raise RuntimeError(f"Agent did not finish within {max_rounds} rounds")
