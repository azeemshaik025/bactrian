"""An MCP server that researches the web for your agent and shows the answer only to you.

The agent writes the search and the question. This process fetches the pages, a model with no tools reads them,
and the answer opens in your browser from a page this process serves once. The agent gets back one line of counts.
"""
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

import llm
import tools
from camel import PolicyViolation

MAX_PAGES = 5
TOOL = {
    "name": "research",
    "description": (
        "Research a question on the web for the user. Pages are read in a separate process by a model with no tools, "
        "and the answer opens in the user's browser. You never see the pages or the answer: you get back only how "
        "many pages were read and whether the question was answered. Use this instead of fetching web pages "
        "yourself. Give `search` as a web search query, `urls` for addresses the user gave you, or both."),
    "inputSchema": {
        "type": "object",
        "properties": {
            "question": {"type": "string", "description": "What the user wants to know."},
            "search": {"type": "string", "description": "A web search query."},
            "urls": {"type": "array", "items": {"type": "string"}, "description": "Addresses of pages to read."},
        },
        "required": ["question"],
    },
}
STYLE = """
body { font: 17px/1.6 system-ui, sans-serif; max-width: 44rem; margin: 3rem auto; padding: 0 1rem;
       color: #1f2328; background: #fff; }
.answer { white-space: pre-wrap; }
.note { color: #59636e; font-size: 14px; border-top: 1px solid #d1d9e0; padding-top: 1rem; }
a { color: #0969da; word-break: break-all; }
@media (prefers-color-scheme: dark) {
  body { color: #e6edf3; background: #0d1117; } .note { color: #9198a1; border-color: #3d444d; } a { color: #4493f8; }
}
"""


def read_page(url):
    try:
        return tools.text_of(tools.download(url))
    except (OSError, http.client.HTTPException, PolicyViolation, ValueError):
        return None


def research(arguments, find, read, show):
    """One research call. What it returns is all the agent learns, so it holds counts and nothing a page wrote."""
    question = str(arguments.get("question", ""))
    urls = arguments.get("urls") or []
    # Agents often pass one address as plain text instead of a list.
    urls = [urls] if isinstance(urls, str) else list(urls)
    try:
        if arguments.get("search"):
            urls += find(str(arguments["search"]))
    except RuntimeError as failed:
        show(question, f"The search failed: {failed}", [])
        return "The search failed; the details are open in the user's browser."
    urls = [url for url in dict.fromkeys(urls) if tools.web_address(url)][:MAX_PAGES]
    with ThreadPoolExecutor(MAX_PAGES) as pool:
        pages = [(url, text) for url, text in zip(urls, pool.map(read_page, urls)) if text]
    if not pages:
        return f"No page could be read ({len(urls)} tried). Nothing was shown to the user."
    sources = [url for url, _ in pages]
    try:
        result = read(question, [text for _, text in pages])
    except RuntimeError as failed:
        show(question, f"The reader failed: {failed}", sources)
        return "The reader failed; the details are open in the user's browser."
    show(question, str(result.get("answer", "")), sources)
    answered = "yes" if result.get("answered") is True else "no"
    return (f"The answer is open in the user's browser; you cannot see it. "
            f"pages read: {len(pages)}, failed: {len(urls) - len(pages)}, answered: {answered}.")


def page(question, answer, sources):
    links = "".join(f'<li><a href="{html.escape(url)}">{html.escape(url)}</a></li>' for url in sources)
    return (f'<!doctype html><meta charset="utf-8"><title>{html.escape(question)}</title><style>{STYLE}</style>'
            f'<h1>{html.escape(question)}</h1><div class="answer">{html.escape(tools.clean(answer))}</div>'
            f'<h2>Sources</h2><ol>{links}</ol>'
            '<p class="note">Written from these pages by a model with no tools. Your agent has not seen this answer. '
            'This page opens once: save it if you want to keep it.</p>')


class Pages(http.server.BaseHTTPRequestHandler):
    waiting = {}

    def do_GET(self):
        body = self.waiting.pop(self.path.lstrip("/"), None)
        self.send_response(200 if body else 404)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        # Nothing on the page may load anything, and the browser keeps no copy on disk.
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body or b"This answer has already been opened once.")

    def log_message(self, *args):
        pass


def serve():
    pages = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Pages)
    threading.Thread(target=pages.serve_forever, daemon=True).start()
    return pages


def publisher(pages, open_page):
    def show(question, answer, sources):
        token = secrets.token_urlsafe(16)
        Pages.waiting[token] = page(question, answer, sources).encode()
        open_page(f"http://127.0.0.1:{pages.server_port}/{token}")

    return show


def handle(message, call):
    """The reply to one JSON-RPC message, or None for a notification."""
    method, number = message.get("method"), message.get("id")
    if number is None:
        return None
    if method == "initialize":
        version = (message.get("params") or {}).get("protocolVersion", "2025-06-18")
        result = {"protocolVersion": version, "capabilities": {"tools": {}},
                  "serverInfo": {"name": "bactrian", "version": "0.1.0"}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [TOOL]}
    elif method == "tools/call":
        params = message.get("params") or {}
        if params.get("name") != "research":
            return {"jsonrpc": "2.0", "id": number, "error": {"code": -32602, "message": "unknown tool"}}
        try:
            result = {"content": [{"type": "text", "text": call(params.get("arguments") or {})}]}
        except Exception as crash:
            # An exception's text can quote a page, and this reply goes to the agent.
            print(f"research crashed: {type(crash).__name__}", file=sys.stderr)
            result = {"content": [{"type": "text", "text": "Research crashed inside the server."}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": number, "error": {"code": -32601, "message": "method not found"}}
    return {"jsonrpc": "2.0", "id": number, "result": result}


def main():
    # The protocol owns stdout. Anything else writing there, such as a browser launcher, would corrupt it.
    protocol = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)
    show = publisher(serve(), webbrowser.open)
    for line in sys.stdin:
        if line.strip():
            reply = handle(json.loads(line), lambda arguments: research(arguments, llm.search, llm.read, show))
            if reply:
                protocol.write(json.dumps(reply) + "\n")
                protocol.flush()


if __name__ == "__main__":
    main()
