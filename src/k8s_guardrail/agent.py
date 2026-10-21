from __future__ import annotations

import json
from typing import Any

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
