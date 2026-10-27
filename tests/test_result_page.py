"""Headless-browser tests for the MCP App result page (skipped without Playwright)."""

import json
from pathlib import Path

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

PAGE = Path(__file__).parent.parent / "src/analyst/adapters/mcp/result.html"


@pytest.fixture(scope="module")
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browser binary not installed
            pytest.skip(f"chromium unavailable: {e}")
        yield b
        b.close()


def finished(columns, rows, **kw):
    return {"status": "finished", "answer": "ans", "sql": "SELECT 1", "columns": columns,
            "rows": rows, "rows_truncated": False, **kw}


def open_in_host(browser, tmp_path, structured, theme="light", tool_reply=None):
    if tool_reply is None:
        tool_reply_js = '{jsonrpc:"2.0", id:m.id, error:{code:-32601, message:"no tools"}}'
    else:
        body = json.dumps(tool_reply).replace("</", "<\\/")
        tool_reply_js = '{jsonrpc:"2.0", id:m.id, result:{content:[{type:"text",text:"x"}], structuredContent:' + body + '}}'
    host = f"""<!doctype html><body><iframe id=f src="{PAGE.as_uri()}" style="width:700px;height:520px"></iframe>
<script>
const f = document.getElementById("f"); window.calls = [];
window.addEventListener("message", e => {{
  const m = e.data;
  if (m.method === "ui/initialize")
    f.contentWindow.postMessage({{jsonrpc:"2.0", id:m.id, result:{{protocolVersion:"2026-01-26",
      hostCapabilities:{{}}, hostInfo:{{name:"fake-host",version:"0"}}, hostContext:{{theme:"{theme}"}}}}}}, "*");
  if (m.method === "tools/call") {{
    window.calls.push(m.params);
    f.contentWindow.postMessage({tool_reply_js}, "*");
  }}
  if (m.method === "ui/notifications/initialized")
    f.contentWindow.postMessage({{jsonrpc:"2.0", method:"ui/notifications/tool-result",
      params:{{content:[{{type:"text",text:"x"}}], structuredContent:{json.dumps(structured).replace("</", "<\\/")}}}}}, "*");
}});
</script></body>"""
    host_file = tmp_path / "host.html"
    host_file.write_text(host)
    page = browser.new_page(viewport={"width": 760, "height": 560})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(host_file.as_uri())
    frame = page.frame_locator("#f")
    frame.locator("#status").filter(has_text="host:").wait_for(timeout=5000)
    page.errors = errors
    return page, frame


def first_cells(frame):
    return frame.locator("tbody tr").first.locator("td").all_inner_texts()


def test_table_renders_all_rows_and_scrolls(browser, tmp_path):
    rows = [[f"o{i}", i * 1.5] for i in range(1, 501)]
    page, f = open_in_host(browser, tmp_path, finished(["id", "total"], rows))
    f.locator("tbody tr").first.wait_for()
    assert f.locator("tbody tr").count() == 500
    assert "500 rows" in f.locator("#count").inner_text()
    sh, ch = f.locator("#wrap").evaluate("e => [e.scrollHeight, e.clientHeight]")
    assert sh > ch
    f.locator("#wrap").evaluate("e => e.scrollTop = e.scrollHeight")
    assert f.locator("tbody tr").last.locator("td").first.inner_text() == "o500"
    assert page.errors == []


def test_click_header_sorts_text_and_numbers_both_directions(browser, tmp_path):
    rows = [["b", 10], ["a", 9], ["c", 100], [None, 5]]
    page, f = open_in_host(browser, tmp_path, finished(["name", "n"], rows))
    f.locator("tbody tr").first.wait_for()
    f.locator("th", has_text="n").nth(1).click()  # numeric asc: 5, 9, 10, 100
    assert [r[1] for r in [first_cells(f)]] == ["5"]
    col = lambda: f.locator("tbody tr td:nth-child(2)").all_inner_texts()
    assert col() == ["5", "9", "10", "100"]
    f.locator("th", has_text="n").nth(1).click()
    assert col() == ["100", "10", "9", "5"]
    f.locator("th", has_text="name").click()  # text asc, NULL last
    assert f.locator("tbody tr td:nth-child(1)").all_inner_texts() == ["a", "b", "c", ""]


def test_empty_result_shows_zero_rows(browser, tmp_path):
    page, f = open_in_host(browser, tmp_path, finished(["a"], []))
    f.locator("#count").filter(has_text="0 rows").wait_for()
    assert f.locator("tbody tr").count() == 0


def test_null_cells_render_empty(browser, tmp_path):
    page, f = open_in_host(browser, tmp_path, finished(["a", "b"], [[None, 1]]))
    f.locator("tbody tr").first.wait_for()
    assert first_cells(f) == ["", "1"]


def test_html_in_cells_is_escaped(browser, tmp_path):
    evil = "<script>x</script>&\"'"
    page, f = open_in_host(
        browser, tmp_path,
        finished(["<b>col</b>"], [[evil]], answer="<img src=x onerror=1>", sql="<i>"),
    )
    f.locator("tbody tr").first.wait_for()
    assert first_cells(f) == [evil]
    assert f.locator("th").first.inner_text().startswith("<b>col</b>")
    assert f.locator("script").count() == 1  # only the page's own script
    assert f.locator("img").count() == 0
    assert f.locator("#answer").inner_text() == "<img src=x onerror=1>"
    assert page.errors == []


def test_truncation_notice_shown(browser, tmp_path):
    page, f = open_in_host(
        browser, tmp_path, finished(["a"], [[1]], rows_truncated=True))
    f.locator("tbody tr").first.wait_for()
    assert "capped" in f.locator("#notice").inner_text().lower()


def test_needs_input_state_shows_question(browser, tmp_path):
    page, f = open_in_host(browser, tmp_path, {
        "status": "needs_input", "question": "Which year?", "run_id": "r1", "session_id": "s1"})
    f.locator("#state").filter(has_text="Which year?").wait_for()
    assert f.locator("tbody tr").count() == 0


def test_needs_approval_state_shows_sql(browser, tmp_path):
    page, f = open_in_host(browser, tmp_path, {
        "status": "needs_approval", "sql": "UPDATE t SET a = '<1>'", "run_id": "r1", "session_id": "s1"})
    f.locator("#state").filter(has_text="UPDATE t SET a = '<1>'").wait_for()
    assert "approval" in f.locator("#state").inner_text().lower()


def test_error_state_shows_message(browser, tmp_path):
    page, f = open_in_host(browser, tmp_path, {"status": "error", "message": "Unknown run"})
    f.locator("#state").filter(has_text="Unknown run").wait_for()


def test_standalone_status_outside_host(browser):
    page = browser.new_page()
    page.goto(PAGE.as_uri())
    page.locator("#status").filter(has_text="standalone").wait_for(timeout=3000)


def test_follows_host_theme(browser, tmp_path):
    page, f = open_in_host(browser, tmp_path, finished(["a"], [[1]]), theme="dark")
    assert f.locator("html").get_attribute("data-theme") == "dark"


APPROVAL = {"status": "needs_approval", "sql": "UPDATE t SET a = 1", "run_id": "r1", "session_id": "s1"}


def test_approval_buttons_call_app_only_tool_and_render_result(browser, tmp_path):
    done = finished(["a"], [[1]], write="executed: 1 row(s)")
    page, f = open_in_host(browser, tmp_path, APPROVAL, tool_reply=done)
    f.locator("#approve").click()
    f.locator("#count").filter(has_text="1 row").wait_for()
    assert page.evaluate("window.calls") == [
        {"name": "approve_write", "arguments": {"run_id": "r1", "approve": True}}]
    assert f.locator("#actions").is_hidden()


def test_reject_button_sends_approve_false(browser, tmp_path):
    page, f = open_in_host(browser, tmp_path, APPROVAL, tool_reply=finished([], [], write="rejected"))
    f.locator("#reject").click()
    f.locator("#notice").filter(has_text="rejected").wait_for()
    assert page.evaluate("window.calls")[0]["arguments"]["approve"] is False


def test_approval_buttons_show_error_when_host_refuses_tool_call(browser, tmp_path):
    page, f = open_in_host(browser, tmp_path, APPROVAL)  # fake host answers with an error
    f.locator("#approve").click()
    f.locator("#state").filter(has_text="could not").wait_for()
    assert f.locator("#actions").is_hidden() is False  # user can retry or reject


def test_buttons_only_in_needs_approval_state(browser, tmp_path):
    page, f = open_in_host(browser, tmp_path, finished(["a"], [[1]]))
    f.locator("tbody tr").first.wait_for()
    assert f.locator("#actions").is_hidden()


MD_ANSWER = ("Orders by <total>:\n\n| s | n |\n|---|---|\n| a | 1 |\n\n"
             "```sql\nSELECT 1\n```\n\nThere are **96 orders** in `orders`.")


def test_answer_markdown_is_cleaned_not_shown_raw(browser, tmp_path):
    page, f = open_in_host(browser, tmp_path, finished(["a"], [[1]], answer=MD_ANSWER))
    f.locator("tbody tr").first.wait_for()
    shown = f.locator("#answer").inner_text()
    assert "```" not in shown and "|" not in shown and "**" not in shown and "`" not in shown
    assert "Orders by <total>:" in shown and "There are 96 orders in orders." in shown
    assert f.locator("#answer strong").inner_text() == "96 orders"
    assert f.locator("#answer b, #answer script, #answer total").count() == 0


def test_numbers_get_thousands_separators(browser, tmp_path):
    page, f = open_in_host(browser, tmp_path, finished(["n", "x", "id"], [[96478, 13591643.7, "00123"]]))
    f.locator("tbody tr").first.wait_for()
    assert first_cells(f) == ["96,478", "13,591,643.70", "00123"]


def test_numeric_headers_align_with_numbers(browser, tmp_path):
    page, f = open_in_host(browser, tmp_path, finished(["name", "n"], [["a", 1], [None, 2]]))
    f.locator("tbody tr").first.wait_for()
    align = lambda i: f.locator("th").nth(i).evaluate("e => getComputedStyle(e).textAlign")
    assert align(0) == "left" and align(1) == "right"
