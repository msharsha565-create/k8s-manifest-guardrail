from __future__ import annotations

import argparse
import asyncio
import os
import sys

from anthropic import AsyncAnthropic
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from k8s_guardrail.agent import DEFAULT_MODEL, run_agent


async def _run(task: str, model: str) -> str:
    # Pass the environment through: the MCP client otherwise drops proxy/CA variables.
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "k8s_guardrail.mcp_server"], env=dict(os.environ)
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        return await run_agent(task, session, AsyncAnthropic(), model=model)


def main() -> int:
    parser = argparse.ArgumentParser(prog="k8s-guardrail-agent", description=__doc__)
    parser.add_argument("task", help='e.g. "A 3-replica nginx Deployment with a ClusterIP Service"')
    parser.add_argument("--model", default=DEFAULT_MODEL)
    args = parser.parse_args()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set", file=sys.stderr)
        return 2
    print(asyncio.run(_run(args.task, args.model)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
