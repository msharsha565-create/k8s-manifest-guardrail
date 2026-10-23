from __future__ import annotations

import argparse
import asyncio
import os
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from k8s_guardrail.agent import DEFAULT_MODEL, OpenAICompatChat, run_agent, run_agent_openai


async def _run(task: str, model: str, provider: str = "anthropic", base_url: str | None = None,
               api_key: str | None = None) -> str:
    # Pass the environment through: the MCP client otherwise drops proxy/CA variables.
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "k8s_guardrail.mcp_server"], env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        if provider == "openai":
            chat = OpenAICompatChat(model, base_url or "https://api.openai.com/v1", api_key)
            return await run_agent_openai(task, session, chat)
        from anthropic import AsyncAnthropic

        return await run_agent(task, session, AsyncAnthropic(), model=model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="k8s-guardrail-agent", description=__doc__)
    parser.add_argument("task", help='e.g. "A 3-replica nginx Deployment with a ClusterIP Service"')
    parser.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic",
                        help="'openai' = any OpenAI-compatible API (OpenAI, Gemini, Ollama, vLLM)")
    parser.add_argument("--model", help=f"default for anthropic: {DEFAULT_MODEL}")
    parser.add_argument("--base-url", help="OpenAI-compatible base URL (with --provider openai)")
    parser.add_argument("--api-key-env",
                        help="env var holding the API key (default: ANTHROPIC_API_KEY, or "
                             "OPENAI_API_KEY with --provider openai)")
    args = parser.parse_args(argv)
    key_env = args.api_key_env or ("OPENAI_API_KEY" if args.provider == "openai"
                                   else "ANTHROPIC_API_KEY")
    api_key = os.environ.get(key_env)
    if not api_key:
        print(f"{key_env} is not set", file=sys.stderr)
        return 2
    if args.provider == "openai" and not args.model:
        print("--model is required with --provider openai", file=sys.stderr)
        return 2
    model = args.model or DEFAULT_MODEL
    print(asyncio.run(_run(args.task, model, args.provider, args.base_url, api_key)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
