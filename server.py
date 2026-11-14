"""An MCP server that runs your agent's research plan the CaMeL way and shows the results to you.

The agent writes a short plan as code. The interpreter runs it: plain code downloads pages, the
scribe reads them, and whatever the plan shows opens in your browser from a page that works once.
The agent gets back only whether the plan ran, or the line where it stopped.
"""

from __future__ import annotations

import html
import http.client
import http.server
import json
import os
import secrets
import sys
import threading
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

import llm
import pages
from camel import USER, Interpreter, PlanError, PolicyError, Tagged, ToolError

MAX_PAGES = 5
WEB = frozenset({"web"})
DESCRIPTION = """\
Read web pages for the user by writing a short plan. A separate model with no tools reads the
pages, and whatever the plan shows opens in the user's browser. You never see the pages or the
answers: you get back only whether the plan ran, or the line where it stopped and why. Use this
instead of fetching web pages yourself, and don't ask the user to paste the answer back.

The plan is a small subset of Python. Statements: assignment to one name, and bare calls.
Expressions: string constants, names, list literals and calls. Nothing else exists: no imports,
if, loops, operators, f-strings, attributes, indexing or keyword arguments.

Functions:
  fetch(urls)                  the text of up to 5 pages, from an address or a list of addresses
                               written in the plan
  ask_scribe(pages, question)  the scribe's answer to the question, from those pages
  show(text)                   puts text on the user's page

There is no search: write addresses the user gave, or ones you already know. For example:

pages = fetch("https://simonwillison.net/2023/Apr/25/dual-llm-pattern/")
show(ask_scribe(pages, "What is the Dual LLM pattern?"))"""
INSTRUCTIONS = (
    "Whenever the user asks what a web page says, asks you to summarise one, or wants anything "
    "looked up on one, use the research tool, not any other way of reading the web. You won't "
    "see the page or the answer: it opens in the user's browser."
)
TOOL = {
    "name": "research",
    "description": DESCRIPTION,
    "inputSchema": {
        "type": "object",
        "properties": {"plan": {"type": "string", "description": "The plan, as code."}},
        "required": ["plan"],
    },
    "annotations": {"readOnlyHint": True, "openWorldHint": True},
}
STYLE = """
body { font: 17px/1.6 system-ui, sans-serif; max-width: 44rem; margin: 3rem auto; padding: 0 1rem;
       color: #1f2328; background: #fff; }
.answer { white-space: pre-wrap; }
.answer + .answer { margin-top: 1.5rem; }
.note { color: #59636e; font-size: 14px; border-top: 1px solid #d1d9e0; padding-top: 1rem; }
a { color: #0969da; word-break: break-all; }
@media (prefers-color-scheme: dark) {
  body { color: #e6edf3; background: #0d1117; }
  .note { color: #9198a1; border-color: #3d444d; }
  a { color: #4493f8; }
}
"""


def written_in_the_plan(urls: Tagged) -> None:
    # A page, or the scribe's answer about one, can name any address.
    if not urls.sources <= USER:
        raise PolicyError("fetch only takes addresses written in the plan")


POLICIES = {"fetch": [written_in_the_plan]}


def flat(value: object) -> list:
    """A value as one flat list, so a plan can pass an item, a list or a list of lists."""
    if isinstance(value, list):
        return [item for part in value for item in flat(part)]
    return [value]


def fetch_text(url: str) -> str | None:
    try:
        return pages.html_to_text(pages.download(url))
    except (OSError, http.client.HTTPException, PolicyError, ValueError):
        return None


def research(
    plan: str,
    answer: Callable[[str, list[str]], dict],
    show: Callable[[list[str], list[str], list[str]], None],
) -> str:
    """Run one plan and return the only text the agent gets: never page text."""
    questions: list[str] = []
    texts: list[str] = []
    sources: list[str] = []

    def fetch(urls: Tagged) -> Tagged:
        wanted = [url for url in dict.fromkeys(flat(urls.value)) if pages.is_web_address(url)]
        wanted = wanted[:MAX_PAGES]
        with ThreadPoolExecutor(MAX_PAGES) as pool:
            read = [(url, text) for url, text in zip(wanted, pool.map(fetch_text, wanted)) if text]
        if not read:
            raise ToolError("no page could be read")
        sources.extend(url for url, _ in read if url not in sources)
        return Tagged([text for _, text in read], WEB)

    def ask_scribe(data: Tagged, question: Tagged) -> Tagged:
        questions.append(str(question.value))
        try:
            result = answer(str(question.value), [str(text) for text in flat(data.value)])
        except RuntimeError as failed:
            texts.append(f"The scribe failed: {failed}")
            raise ToolError("the scribe failed") from None
        if result.get("answered") is not True:
            texts.append(str(result.get("answer", "")))
            raise ToolError("the scribe could not answer from these pages")
        return Tagged(str(result.get("answer", "")), data.sources | question.sources)

    def show_text(text: Tagged) -> Tagged:
        texts.append("\n\n".join(map(str, flat(text.value))))
        return Tagged(None, USER)

    tools = {"fetch": fetch, "ask_scribe": ask_scribe, "show": show_text}
    try:
        Interpreter(tools, POLICIES).run(plan)
        told = "The plan ran."
    except SyntaxError as wrong:
        told = f"The plan stopped at line {wrong.lineno}: {wrong.msg}."
    except (PlanError, PolicyError, ToolError) as stopped:
        told = f"The plan stopped at {stopped}."
    if not texts:
        return f"{told} Nothing was shown to the user."
    show(questions, texts, sources)
    return f"{told} A page for the user is open in their browser; you cannot see it."


def answer_page(questions: list[str], texts: list[str], sources: list[str]) -> str:
    title = html.escape(pages.clean(" / ".join(questions) or "Research"))
    answers = "".join(
        f'<div class="answer">{html.escape(pages.clean(text))}</div>' for text in texts
    )
    links = "".join(
        f'<li><a href="{html.escape(url)}">{html.escape(url)}</a></li>' for url in sources
    )
    return (
        f'<!doctype html><meta charset="utf-8"><title>{title}</title>'
        f"<style>{STYLE}</style><h1>{title}</h1>{answers}"
        f"<h2>Sources</h2><ol>{links}</ol>"
        '<p class="note">Written from these pages by a model with no tools. Your agent has not '
        "seen this page. It opens once: save it if you want to keep it.</p>"
    )


class AnswerPage(http.server.BaseHTTPRequestHandler):
    unopened: dict[str, bytes] = {}

    def do_GET(self) -> None:
        body = self.unopened.pop(self.path.lstrip("/"), None)
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # Nothing on the page may load anything, and the browser keeps no copy on disk.
        self.send_header(
            "Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'"
        )
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body or b"This answer has already been opened once.")

    def log_message(self, *args) -> None:
        pass


def start_page_server() -> http.server.ThreadingHTTPServer:
    page_server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), AnswerPage)
    threading.Thread(target=page_server.serve_forever, daemon=True).start()
    return page_server


def publisher(
    page_server: http.server.ThreadingHTTPServer, open_browser: Callable[[str], object]
) -> Callable[[list[str], list[str], list[str]], None]:
    def show(questions: list[str], texts: list[str], sources: list[str]) -> None:
        token = secrets.token_urlsafe(16)
        AnswerPage.unopened[token] = answer_page(questions, texts, sources).encode()
        open_browser(f"http://127.0.0.1:{page_server.server_port}/{token}")

    return show


def handle(message: dict, call: Callable[[dict], str]) -> dict | None:
    """The reply to one JSON-RPC message, or None for a notification."""
    method, number = message.get("method"), message.get("id")
    if number is None:
        return None
    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion", "2025-06-18")
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "bactrian", "version": "0.1.0"},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        params = message.get("params") or {}
        if params.get("name") != "research":
            return {
                "jsonrpc": "2.0",
                "id": number,
                "error": {"code": -32602, "message": "unknown tool"},
            }
        try:
            result = {"content": [{"type": "text", "text": call(params.get("arguments") or {})}]}
        except Exception as crash:
            # An exception's text can quote a page, and this reply goes to the agent.
            print(f"research crashed: {type(crash).__name__}", file=sys.stderr)
            text = "Research crashed inside the server."
            result = {"content": [{"type": "text", "text": text}], "isError": True}
    else:
        return {
            "jsonrpc": "2.0",
            "id": number,
            "error": {"code": -32601, "message": "method not found"},
        }
    return {"jsonrpc": "2.0", "id": number, "result": result}


def main() -> None:
    # The protocol owns stdout. Anything else writing there, such as a browser launcher, would
    # corrupt it, so stray output goes to stderr instead.
    protocol = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    show = publisher(start_page_server(), webbrowser.open)

    def call(arguments: dict) -> str:
        # Checked every call: a Claude Code update can change what the models start with.
        plan = str(arguments.get("plan", ""))
        return llm.isolation_problem() or research(plan, llm.answer, show)

    for line in sys.stdin:
        if line.strip():
            reply = handle(json.loads(line), call)
            if reply:
                protocol.write(json.dumps(reply) + "\n")
                protocol.flush()


if __name__ == "__main__":
    main()
