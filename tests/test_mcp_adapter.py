"""MCP adapter protocol tests: in-memory SDK client against build_server."""

import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from analyst.adapters.mcp.server import build_server, to_call_tool_result
from analyst.service import Failure, Finished, NeedsApproval, NeedsInput

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def finished(rows=None, **kw):
    rows = rows if rows is not None else [[1, "a"], [2, "b"]]
    base = dict(session_id="s1", answer="Two rows.", sql="SELECT 1", columns=["n", "t"],
                rows=rows, rows_truncated=False, stop_reason="answered", steps=2,
                prompt_tokens=10, completion_tokens=5, trace_id="tr1", write=None)
    base.update(kw)
    return Finished(**base)


class FakeService:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def ask(self, question, session_id=None):
        self.calls.append(("ask", question, session_id))
        return self.result

    def reply(self, run_id, text):
        self.calls.append(("reply", run_id, text))
        return self.result

    def decide_write(self, run_id, approve):
        self.calls.append(("approve", run_id, approve))
        return self.result


async def session(result):
    svc = FakeService(result)
    return svc, create_connected_server_and_client_session(build_server(svc)._mcp_server)


async def test_tools_declare_ui_resource_and_readonly_flags():
    svc, ctx = await session(finished())
    async with ctx as c:
        tools = {t.name: t for t in (await c.list_tools()).tools}
    assert set(tools) == {"ask_analyst", "reply", "approve_write"}
    for name in ("ask_analyst", "reply"):
        assert tools[name].meta["ui"]["resourceUri"] == "ui://analyst/result"
        assert tools[name].annotations.readOnlyHint is True
    # only a click in the page may approve: hidden from the model
    assert tools["approve_write"].meta["ui"]["visibility"] == ["app"]
    assert tools["approve_write"].annotations.readOnlyHint is False


async def test_resource_is_html_with_mcp_app_mime():
    svc, ctx = await session(finished())
    async with ctx as c:
        res = await c.read_resource("ui://analyst/result")
    assert res.contents[0].mimeType == "text/html;profile=mcp-app"
    assert "<html" in res.contents[0].text


async def test_finished_result_has_short_text_and_structured_rows():
    svc, ctx = await session(finished())
    async with ctx as c:
        out = await c.call_tool("ask_analyst", {"question": "q", "session_id": "s0"})
    assert svc.calls == [("ask", "q", "s0")]
    assert out.isError is False
    sc = out.structuredContent
    assert sc["status"] == "finished" and sc["rows"] == [[1, "a"], [2, "b"]]
    assert sc["columns"] == ["n", "t"] and sc["session_id"] == "s1"
    text = out.content[0].text
    assert "Two rows." in text and "SELECT 1" in text and "session_id" in text and "s1" in text


def test_text_preview_bounded_for_10000_rows():
    rows = [[i, "x" * 100] for i in range(10_000)]
    r = to_call_tool_result(finished(rows=rows))
    assert len(r.content[0].text) <= 1500
    assert "10000 rows" in r.content[0].text
    assert len(r.structuredContent["rows"]) == 10_000


async def test_needs_input_text_tells_model_to_call_reply():
    svc, ctx = await session(NeedsInput("s1", "r9", "Which year?"))
    async with ctx as c:
        out = await c.call_tool("ask_analyst", {"question": "q"})
    text = out.content[0].text
    assert "Which year?" in text and "reply" in text and "r9" in text
    assert out.structuredContent["status"] == "needs_input"
    assert out.structuredContent["run_id"] == "r9"


async def test_needs_approval_contains_exact_sql():
    sql = "UPDATE t SET a = 'x' WHERE id = 1"
    svc, ctx = await session(NeedsApproval("s1", "r9", sql))
    async with ctx as c:
        out = await c.call_tool("reply", {"run_id": "r1", "text": "ok"})
    assert sql in out.content[0].text
    text = out.content[0].text
    assert "cannot approve" in text and "Approve" in text
    assert "approve_write" not in text
    assert out.structuredContent["status"] == "needs_approval"
    assert out.structuredContent["sql"] == sql


async def test_failure_maps_to_error_status_not_exception():
    svc, ctx = await session(Failure("Unknown run_id 'x'."))
    async with ctx as c:
        out = await c.call_tool("approve_write", {"run_id": "x", "approve": True})
    assert svc.calls == [("approve", "x", True)]
    assert out.structuredContent == {"status": "error", "message": "Unknown run_id 'x'."}
    assert "Unknown run_id" in out.content[0].text


async def test_instructions_first_512_chars_mention_session_id():
    svc, ctx = await session(finished())
    async with ctx as c:
        pass
    server = build_server(svc)
    assert "session_id" in server.instructions[:512]
    assert "approve_write" not in server.instructions
    assert "click" in server.instructions
