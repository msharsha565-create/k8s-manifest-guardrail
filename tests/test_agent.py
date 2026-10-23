import json
from types import SimpleNamespace as NS

import httpx
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from k8s_guardrail import agent as agent_module
from k8s_guardrail.agent import OpenAICompatChat, run_agent, run_agent_openai
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


# --- OpenAI-compatible backend -------------------------------------------------------------

def tool_call_message(manifest, call_id, extra=None):
    call = {"id": call_id, "type": "function",
            "function": {"name": "validate_manifest",
                         "arguments": json.dumps({"manifest_yaml": manifest})}}
    if extra:
        call["extra_content"] = extra
    return {"role": "assistant", "content": None, "tool_calls": [call]}


class ScriptedChat:
    def __init__(self, replies):
        self._replies = iter(replies)
        self.seen = []

    async def complete(self, messages, tools):
        self.seen.append((json.loads(json.dumps(messages)), tools))
        return next(self._replies)


async def test_openai_agent_self_corrects_via_mcp():
    sig = {"google": {"thought_signature": "abc123"}}
    chat = ScriptedChat([
        tool_call_message(BAD, "c1", extra=sig),
        tool_call_message(GOOD, "c2"),
        {"role": "assistant", "content": GOOD},
    ])
    async with open_session() as session:
        answer = await run_agent_openai("nginx deployment", session, chat)
    assert answer == GOOD
    second_turn = chat.seen[1][0]
    # the assistant message is echoed back untouched, including provider metadata
    assert second_turn[2]["tool_calls"][0]["extra_content"] == sig
    tool_msg = second_turn[3]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1"
    assert "INVALID_API_VERSION" in tool_msg["content"]
    assert '"is_valid": true' in chat.seen[2][0][-1]["content"]
    assert chat.seen[0][0][0]["role"] == "system"


async def test_openai_agent_tools_use_function_schema_without_unsupported_keys():
    chat = ScriptedChat([{"role": "assistant", "content": "done"}])
    async with open_session() as session:
        await run_agent_openai("x", session, chat)
    tools = chat.seen[0][1]
    assert {t["function"]["name"] for t in tools} == {
        "validate_manifest", "list_supported_api_versions"}
    assert all(t["type"] == "function" for t in tools)
    blob = json.dumps(tools)
    assert '"title"' not in blob and "additionalProperties" not in blob


async def test_openai_agent_survives_malformed_tool_arguments():
    bad_args = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "c1", "type": "function",
         "function": {"name": "validate_manifest", "arguments": "{not json"}}]}
    chat = ScriptedChat([bad_args, {"role": "assistant", "content": "recovered"}])
    async with open_session() as session:
        assert await run_agent_openai("x", session, chat) == "recovered"
    assert "Invalid tool call" in chat.seen[1][0][-1]["content"]


async def test_openai_agent_gives_up_after_max_rounds():
    chat = ScriptedChat([tool_call_message(BAD, f"c{i}") for i in range(3)])
    async with open_session() as session:
        with pytest.raises(RuntimeError):
            await run_agent_openai("x", session, chat, max_rounds=3)


def make_chat(handler, **kwargs):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OpenAICompatChat("m1", "http://localhost:11434/v1/", "k", client=client, **kwargs)


OK_BODY = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}


async def test_openai_chat_sends_tools_and_auth():
    seen = {}

    def handler(request):
        seen.update(url=str(request.url), auth=request.headers["authorization"],
                    body=json.loads(request.content))
        return httpx.Response(200, json=OK_BODY)

    msg = await make_chat(handler).complete([{"role": "user", "content": "q"}], [{"type": "function"}])
    assert msg["content"] == "hi"
    assert seen["url"] == "http://localhost:11434/v1/chat/completions"
    assert seen["auth"] == "Bearer k"
    assert seen["body"]["tools"] == [{"type": "function"}] and seen["body"]["temperature"] == 0.0


async def test_openai_chat_retries_rate_limits_then_succeeds(monkeypatch):
    sleeps, calls = [], []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(agent_module.asyncio, "sleep", fake_sleep)

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"retry-after": "2"}, text="slow down")
        return httpx.Response(200, json=OK_BODY)

    assert (await make_chat(handler).complete([], []))["content"] == "hi"
    assert len(calls) == 3 and sleeps == [2.0, 2.0]


async def test_openai_chat_fails_fast_on_daily_quota_and_shows_body(monkeypatch):
    async def fake_sleep(seconds):
        raise AssertionError("must not wait out a daily quota")

    monkeypatch.setattr(agent_module.asyncio, "sleep", fake_sleep)
    body = '{"error": {"details": [{"retryDelay": "3600s"}], "message": "quota exceeded"}}'

    def handler(request):
        return httpx.Response(429, text=body)

    with pytest.raises(httpx.HTTPStatusError, match="quota exceeded"):
        await make_chat(handler).complete([], [])


async def test_openai_chat_error_includes_response_body():
    def handler(request):
        return httpx.Response(400, text="seed is not supported")

    with pytest.raises(httpx.HTTPStatusError, match="seed is not supported"):
        await make_chat(handler).complete([], [])


def test_cli_provider_openai_uses_named_key_and_requires_model(monkeypatch, capsys):
    from k8s_guardrail import agent_cli

    calls = []

    async def fake_run(task, model, provider, base_url, api_key):
        calls.append((task, model, provider, base_url, api_key))
        return GOOD

    monkeypatch.setattr(agent_cli, "_run", fake_run)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gem-key")
    argv = ["nginx", "--provider", "openai", "--model", "gem", "--base-url", "http://x/v1",
            "--api-key-env", "GEMINI_API_KEY"]
    assert agent_cli.main(argv) == 0
    assert calls == [("nginx", "gem", "openai", "http://x/v1", "gem-key")]
    assert agent_cli.main(["nginx", "--provider", "openai", "--api-key-env", "GEMINI_API_KEY"]) == 2
    assert agent_cli.main(["nginx", "--provider", "openai"]) == 2  # OPENAI_API_KEY unset
    assert "is not set" in capsys.readouterr().err


def test_agent_cli_reports_the_root_cause_not_an_exception_group(monkeypatch, capsys):
    from k8s_guardrail import agent_cli

    async def failing_run(*args, **kwargs):
        group = Exception("unhandled errors in a TaskGroup")
        group.exceptions = [ValueError("429 from api")]  # shape of an (Base)ExceptionGroup
        raise group

    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setattr(agent_cli, "_run", failing_run)
    argv = ["nginx", "--provider", "openai", "--api-key-env", "GEMINI_API_KEY", "--model", "m"]
    assert agent_cli.main(argv) == 1
    assert capsys.readouterr().err.strip() == "error: 429 from api"
