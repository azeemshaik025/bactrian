"""Download web pages as plain text, and clean text before a person sees it."""

from __future__ import annotations

import unicodedata
import urllib.request
from html.parser import HTMLParser

from camel import PolicyError

# Long articles get cut at these limits.
MAX_BYTES = 1_000_000
MAX_CHARS = 30_000
INLINE_TAGS = {
    "a",
    "abbr",
    "b",
    "cite",
    "code",
    "em",
    "i",
    "kbd",
    "mark",
    "q",
    "s",
    "small",
    "span",
    "strong",
    "sub",
    "sup",
    "time",
    "u",
    "var",
}
HIDDEN_TAGS = {"script", "style", "noscript", "template"}


class PageText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []
        self.hidden = 0

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.hidden += tag in HIDDEN_TAGS
        if tag not in INLINE_TAGS:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        self.hidden = max(0, self.hidden - (tag in HIDDEN_TAGS))
        if tag not in INLINE_TAGS:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.hidden:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    parser = PageText()
    try:
        parser.feed(html)
        parser.close()
    except (AssertionError, NotImplementedError):
        # Older Pythons give up on some broken markup, such as a bad "<![" section, and raise.
        # Keep the text read before it.
        pass
    return " ".join("".join(parser.parts).split())[:MAX_CHARS]


def is_web_address(url: object) -> bool:
    return isinstance(url, str) and url.lower().startswith(("https://", "http://"))


class WebOnlyRedirects(urllib.request.HTTPRedirectHandler):
    # A site can still redirect to any http(s) address, including one on your local network.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_web_address(newurl):
            raise PolicyError("a site redirected to an address that is not http or https")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(WebOnlyRedirects)


def download(url: str) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (compatible; bactrian)"}
    )
    with OPENER.open(request, timeout=30) as response:
        # Pages in other encodings show replacement characters. Most of the web is UTF-8.
        return response.read(MAX_BYTES).decode("utf-8", "replace")


def clean(text: object) -> str:
    # Control characters can trigger terminal escape codes, and format characters include the
    # bidi overrides that make text display in a different order than it is stored.
    return "".join(
        c for c in str(text) if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf")
    )
