from types import SimpleNamespace

import pytest

from analyst.config import AgentConfig
from analyst.llm import LLMClient, ModelUnavailable


def make_cfg(**overrides):
    base = dict(
        model="m",
        base_url="http://localhost:9/v1",
        api_key="k",
        max_steps=12,
        max_total_tokens=20000,
        context_window=32000,
        db_path="d.sqlite",
        scratch_path="s.sqlite",
        row_limit=200,
        timeout_s=10.0,
        rag=False,
        sql_checks=False,
        clarify=False,
    )
    base.update(overrides)
    return AgentConfig(**base)


def fake_client(message, prompt_tokens=11, completion_tokens=7,
                finish_reason="stop"):
    completions = SimpleNamespace(
        create=lambda **kw: SimpleNamespace(
            choices=[SimpleNamespace(message=message,
                                     finish_reason=finish_reason)],
            usage=SimpleNamespace(
                prompt_tokens=prompt_tokens, completion_tokens=completion_tokens
            ),
        )
    )
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


def test_chat_parses_tool_call():
    message = SimpleNamespace(
        role="assistant",
        content="let me check",
        reasoning_content="hmm",
        tool_calls=[
            SimpleNamespace(
                id="call_1",
                type="function",
                function=SimpleNamespace(
                    name="run_sql", arguments='{"sql": "SELECT 1"}'
                ),
            )
        ],
    )
    client = LLMClient(make_cfg(), client=fake_client(message, 11, 7))
    reply = client.chat([{"role": "user", "content": "hi"}])
    assert reply.content == "let me check"
    assert reply.tool_calls[0].name == "run_sql"
    assert reply.tool_calls[0].id == "call_1"
    assert reply.tool_calls[0].raw_arguments == '{"sql": "SELECT 1"}'
    assert reply.prompt_tokens == 11
    assert reply.completion_tokens == 7
    assert reply.seconds >= 0.0


def test_chat_without_tools_returns_no_tool_calls():
    message = SimpleNamespace(
        role="assistant", content="42", reasoning_content=None, tool_calls=None
    )
    client = LLMClient(make_cfg(), client=fake_client(message, 3, 2))
    reply = client.chat([{"role": "user", "content": "hi"}])
    assert reply.tool_calls == []
    assert reply.content == "42"


def test_server_down_raises_model_unavailable():
    client = LLMClient(make_cfg(base_url="http://127.0.0.1:1/v1"))
    with pytest.raises(ModelUnavailable):
        client.chat([{"role": "user", "content": "hi"}])


def test_chat_reports_finish_reason():
    message = SimpleNamespace(
        role="assistant", content=None, reasoning_content=None, tool_calls=None
    )
    client = LLMClient(
        make_cfg(), client=fake_client(message, finish_reason="length")
    )
    reply = client.chat([{"role": "user", "content": "hi"}])
    assert reply.finish_reason == "length"
