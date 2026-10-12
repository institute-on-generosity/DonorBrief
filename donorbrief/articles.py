"""Fill in thin items with the opening paragraphs of the article itself."""
from __future__ import annotations

import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from html.parser import HTMLParser
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import httpx

if TYPE_CHECKING:
    from .sources import Item

log = logging.getLogger(__name__)

THIN_SUMMARY = 300        # summaries shorter than this get the article's opening
MAX_ARTICLE_CHARS = 1500
MIN_PARAGRAPH = 80        # skip captions, bylines, "Share this" lines
SKIP_HOSTS = ("news.google.com", "congress.gov", "federalregister.gov")
SPONSORED = re.compile(r"/(sponsored|sponsor-content|paid-post|partner-content)/", re.I)
SKIP_TAGS = {"script", "style", "nav", "header", "footer", "aside", "form", "figure", "noscript"}


class _Paragraphs(HTMLParser):
    """Collects the text of <p> elements outside page chrome."""

    def __init__(self) -> None:
        super().__init__()
        self.skip = 0
        self.in_p = False
        self.buf: list[str] = []
        self.paragraphs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip += 1
        elif tag == "p" and not self.skip:
            self.in_p, self.buf = True, []

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS and self.skip:
            self.skip -= 1
        elif tag == "p" and self.in_p:
            self.in_p = False
            text = re.sub(r"\s+", " ", "".join(self.buf)).strip()
            if len(text) >= MIN_PARAGRAPH:
                self.paragraphs.append(text)

    def handle_data(self, data):
        if self.in_p and not self.skip:
            self.buf.append(data)


def opening_text(page: str) -> str:
    parser = _Paragraphs()
    parser.feed(page)
    out = ""
    for p in parser.paragraphs:
        if len(out) + len(p) > MAX_ARTICLE_CHARS:
            break
        out = f"{out} {p}".strip()
    return out


def is_sponsored(item: "Item") -> bool:
    return bool(SPONSORED.search(item.url)) or item.title.lower().startswith(("sponsored", "paid content"))


def needs_article(item: "Item") -> bool:
    return len(item.summary) < THIN_SUMMARY and not any(h in item.url for h in SKIP_HOSTS)


def enrich(items: list["Item"], client: httpx.Client, workers: int = 4) -> int:
    """Append article openings to thin items in place. Returns how many were filled in."""
    todo = [i for i in items if needs_article(i)]
    misses: list[str] = []

    def fill(item: "Item") -> bool:
        host = urlparse(item.url).netloc.removeprefix("www.")
        try:
            resp = client.get(item.url, timeout=15.0)
            resp.raise_for_status()
            text = opening_text(resp.text)
        except httpx.HTTPStatusError as e:  # a missing article just leaves the summary as is
            misses.append(f"{host} {e.response.status_code}")
            return False
        except Exception as e:  # noqa: BLE001
            misses.append(f"{host} {type(e).__name__}")
            return False
        if len(text) <= len(item.summary):
            misses.append(f"{host} no text")
            return False
        item.summary = f"{item.summary} {text}".strip() if item.summary not in text else text
        return True

    with ThreadPoolExecutor(max_workers=workers) as pool:
        filled = sum(pool.map(fill, todo))
    log.info("Article text added to %d of %d thin items", filled, len(todo))
    if misses:
        log.info("  not added: %s", ", ".join(f"{m} x{n}" for m, n in Counter(misses).most_common()))
    return filled
