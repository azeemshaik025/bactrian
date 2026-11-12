import unicodedata
import urllib.request
from html.parser import HTMLParser

from camel import USER, PolicyViolation, Tagged

WEB = frozenset({"web"})
# ponytail: reads the first 1 MB of a page and keeps 30,000 characters of its text. Raise both if long articles get cut.
MAX_BYTES = 1_000_000
MAX_TEXT = 30_000
INLINE = {"a", "abbr", "b", "cite", "code", "em", "i", "kbd", "mark", "q", "s", "small", "span", "strong", "sub", "sup",
          "time", "u", "var"}
HIDDEN = {"script", "style", "noscript", "template"}


class PageText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        self.hidden += tag in HIDDEN
        if tag not in INLINE:
            self.parts.append(" ")

    def handle_endtag(self, tag):
        self.hidden = max(0, self.hidden - (tag in HIDDEN))
        if tag not in INLINE:
            self.parts.append(" ")

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def text_of(html):
    parser = PageText()
    parser.feed(html)
    parser.close()
    return " ".join("".join(parser.parts).split())[:MAX_TEXT]


def web_address(url):
    return isinstance(url, str) and url.lower().startswith(("https://", "http://"))


class WebRedirects(urllib.request.HTTPRedirectHandler):
    # ponytail: a site can still redirect to any http(s) address, local network included. Check the host if that matters.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not web_address(newurl):
            raise PolicyViolation("a site redirected to an address that is not http or https")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


OPENER = urllib.request.build_opener(WebRedirects)


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; bactrian)"})
    with OPENER.open(request, timeout=30) as response:
        # ponytail: always UTF-8, so a page in another encoding shows replacement characters. Most of the web is UTF-8.
        return response.read(MAX_BYTES).decode("utf-8", "replace")


def clean(text):
    # Control characters reach terminal escape codes. Format characters include the bidi overrides,
    # which make a terminal show text in a different order than it is stored.
    return "".join(c for c in str(text) if c in "\n\t" or unicodedata.category(c) not in ("Cc", "Cf"))


def fetch(url):
    return Tagged(text_of(download(url.value)), WEB)


def show(text):
    value = text.value
    print(clean("\n\n".join(map(str, value)) if isinstance(value, list) else value))
    return Tagged(None, USER)


def written_in_the_plan(url):
    # The plan comes from your request alone, so a page, or the model's answer about one, never carries USER only.
    if url.sources != USER or not web_address(url.value):
        raise PolicyViolation("fetch only takes an http or https address written in the plan")


def build(llm):
    """The plan's functions and their policies."""
    def ask_quarantined(data, question):
        return Tagged(llm(data.value, question.value), data.sources | question.sources)

    return {"fetch": fetch, "ask_quarantined": ask_quarantined, "show": show}, {"fetch": [written_in_the_plan]}
