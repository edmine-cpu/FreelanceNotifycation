"""Remove presentation-only HTML; ambiguous markup/code stays byte-for-byte."""
import re
from html.parser import HTMLParser

_BLOCKS = {"p", "div", "ul", "ol", "li", "h1", "h2", "h3", "h4", "h5", "h6"}
_INLINE = {"b", "strong", "i", "em", "u", "span", "a"}


class _ProjectHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.links = []
        self.safe = True

    def handle_starttag(self, tag, attrs):
        if tag not in _BLOCKS | _INLINE | {"br"}:
            self.safe = False  # code, tables, images and unknown markup keep the original
        if tag in _BLOCKS or tag == "br":
            self.parts.append("\n")
        if tag == "li":
            self.parts.append("- ")
        if tag == "a":
            self.links.append(dict(attrs).get("href", ""))

    def handle_endtag(self, tag):
        if tag in _BLOCKS:
            self.parts.append("\n")
        if tag == "a" and self.links:
            url = self.links.pop()
            if url:
                self.parts.append(f" ({url})")

    def handle_data(self, data):
        self.parts.append(data)

    def handle_comment(self, data):
        self.safe = False


def normalize_project_text(text: str) -> str:
    value = text.strip()
    if not re.match(r"^<(?:p|div|ul|ol|h[1-6])(?:\s[^>]*|)>" , value, re.IGNORECASE):
        return value
    parser = _ProjectHTML()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return value
    if not parser.safe:
        return value
    return re.sub(r"\n{3,}", "\n\n", "".join(parser.parts)).strip()
