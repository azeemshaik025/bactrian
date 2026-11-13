"""An MCP server that researches the web for your agent and shows the answer only to you.

The agent writes the question and a link or a search. This process downloads the pages, the
scribe reads them, and the answer opens in your browser from a page that works once. The agent
gets back one line of counts.
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
from camel import PolicyError

MAX_PAGES = 5
TOOL = {
    "name": "research",
    "description": (
        "Research a question on the web for the user. Pages are read in a separate process by a "
        "model with no tools, and the answer opens in the user's browser. You never see the pages "
        "or the answer: you get back only how many pages were read and whether the question was "
        "answered. Use this instead of fetching web pages yourself. Give `urls` when the user "
        "gave page addresses; otherwise give `search` as a web search query. With `urls`, no "
        "search runs."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "What the user wants to know."},
            "search": {"type": "string", "description": "A web search query."},
            "urls": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Addresses of pages to read.",
            },
        },
        "required": ["question"],
    },
    # Read-only is true, and it lets Codex run the tool without asking each time.
    "annotations": {"readOnlyHint": True, "openWorldHint": True},
}
STYLE = """
body { font: 17px/1.6 system-ui, sans-serif; max-width: 44rem; margin: 3rem auto; padding: 0 1rem;
       color: #1f2328; background: #fff; }
.answer { white-space: pre-wrap; }
.note { color: #59636e; font-size: 14px; border-top: 1px solid #d1d9e0; padding-top: 1rem; }
a { color: #0969da; word-break: break-all; }
@media (prefers-color-scheme: dark) {
  body { color: #e6edf3; background: #0d1117; }
  .note { color: #9198a1; border-color: #3d444d; }
  a { color: #4493f8; }
}
"""


def fetch_text(url: str) -> str | None:
    try:
        return pages.html_to_text(pages.download(url))
    except (OSError, http.client.HTTPException, PolicyError, ValueError):
        return None


def research(
    arguments: dict,
    search: Callable[[str], list[str]],
    answer: Callable[[str, list[str]], dict],
    show: Callable[[str, str, list[str]], None],
) -> str:
    """Run one research call and return the only text the agent gets: counts, never page text."""
    question = str(arguments.get("question", ""))
    urls = arguments.get("urls") or []
    # Agents often pass one address as plain text instead of a list.
    urls = [urls] if isinstance(urls, str) else list(urls)
    try:
        if arguments.get("search") and not urls:
            urls += search(str(arguments["search"]))
    except RuntimeError as failed:
        show(question, f"The search failed: {failed}", [])
        return "The search failed; the details are open in the user's browser."
    urls = [url for url in dict.fromkeys(urls) if pages.is_web_address(url)][:MAX_PAGES]
    with ThreadPoolExecutor(MAX_PAGES) as pool:
        fetched = [(url, text) for url, text in zip(urls, pool.map(fetch_text, urls)) if text]
    if not fetched:
        return f"No page could be read ({len(urls)} tried). Nothing was shown to the user."
    sources = [url for url, _ in fetched]
    try:
        result = answer(question, [text for _, text in fetched])
    except RuntimeError as failed:
        show(question, f"The scribe failed: {failed}", sources)
        return "The scribe failed; the details are open in the user's browser."
    show(question, str(result.get("answer", "")), sources)
    answered = "yes" if result.get("answered") is True else "no"
    return (
        "The answer is open in the user's browser; you cannot see it. "
        f"pages read: {len(fetched)}, failed: {len(urls) - len(fetched)}, answered: {answered}."
    )


def answer_page(question: str, answer: str, sources: list[str]) -> str:
    links = "".join(
        f'<li><a href="{html.escape(url)}">{html.escape(url)}</a></li>' for url in sources
    )
    return (
        f'<!doctype html><meta charset="utf-8"><title>{html.escape(question)}</title>'
        f"<style>{STYLE}</style><h1>{html.escape(question)}</h1>"
        f'<div class="answer">{html.escape(pages.clean(answer))}</div>'
        f"<h2>Sources</h2><ol>{links}</ol>"
        '<p class="note">Written from these pages by a model with no tools. Your agent has not '
        "seen this answer. This page opens once: save it if you want to keep it.</p>"
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
) -> Callable[[str, str, list[str]], None]:
    def show(question: str, answer: str, sources: list[str]) -> None:
        token = secrets.token_urlsafe(16)
        AnswerPage.unopened[token] = answer_page(question, answer, sources).encode()
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
        return llm.isolation_problem() or research(arguments, llm.search, llm.answer, show)

    for line in sys.stdin:
        if line.strip():
            reply = handle(json.loads(line), call)
            if reply:
                protocol.write(json.dumps(reply) + "\n")
                protocol.flush()


if __name__ == "__main__":
    main()
