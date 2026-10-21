from types import SimpleNamespace as NS

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from k8s_guardrail.agent import run_agent
from k8s_guardrail.mcp_server import mcp

BAD = "apiVersion: extensions/v1beta1\nkind: Deployment\nmetadata: {name: web}\n"
GOOD = """apiVersion: apps/v1
kind: Deployment
metadata: {name: web}
spec:
  selector: {matchLabels: {app: web}}
  template:
    spec:
      containers:
        - name: web
          image: nginx:1.27
          resources: {requests: {cpu: 100m}, limits: {cpu: 200m}}
          securityContext: {allowPrivilegeEscalation: false}
"""


def open_session():
    # A context manager rather than a fixture: anyio cancel scopes must exit in the entering task.
    return create_connected_server_and_client_session(mcp._mcp_server)


class FakeClient:
    """Scripted stand-in for AsyncAnthropic: yields one canned response per call."""

    def __init__(self, responses):
        self._responses = iter(responses)
        self.calls = []
        self.messages = self

    async def create(self, **kwargs):
        self.calls.append(kwargs["messages"][:])
        return next(self._responses)


def tool_use(manifest, id_):
    block = NS(type="tool_use", id=id_, name="validate_manifest",
               input={"manifest_yaml": manifest})
    return NS(content=[block])


def text(t):
    return NS(content=[NS(type="text", text=t)])


async def test_server_exposes_tools():
    async with open_session() as session:
        names = {t.name for t in (await session.list_tools()).tools}
    assert names == {"validate_manifest", "list_supported_api_versions"}


async def test_validate_tool_flags_bad_manifest():
    async with open_session() as session:
        bad = await session.call_tool("validate_manifest", {"manifest_yaml": BAD})
        good = await session.call_tool("validate_manifest", {"manifest_yaml": GOOD})
    assert bad.structuredContent["is_valid"] is False
    assert good.structuredContent["is_valid"] is True


async def test_agent_self_corrects_via_mcp():
    client = FakeClient([tool_use(BAD, "t1"), tool_use(GOOD, "t2"), text(GOOD)])
    async with open_session() as session:
        answer = await run_agent("nginx deployment", session, client)
    assert answer == GOOD
    # The first tool result fed back to the model reported the failure.
    first_result = client.calls[1][-1]["content"][0]
    assert first_result["tool_use_id"] == "t1"
    assert "INVALID_API_VERSION" in first_result["content"]
    assert "is_valid\": true" in client.calls[2][-1]["content"][0]["content"].lower() or \
        "true" in client.calls[2][-1]["content"][0]["content"].lower()


async def test_agent_gives_up_after_max_rounds():
    client = FakeClient([tool_use(BAD, f"t{i}") for i in range(3)])
    async with open_session() as session:
        with pytest.raises(RuntimeError):
            await run_agent("x", session, client, max_rounds=3)
