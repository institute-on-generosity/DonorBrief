"""Fetch items from each configured source and normalize them."""
from __future__ import annotations

import calendar
import hashlib
import html
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import feedparser
import httpx

log = logging.getLogger(__name__)

USER_AGENT = "DonorBrief/0.1 (Institute on Generosity; +https://github.com/institute-on-generosity/DonorBrief)"
TIMEOUT = httpx.Timeout(30.0)
SUMMARY_CHARS = 1200


@dataclass
class Item:
    source_id: str
    source_name: str
    title: str
    url: str
    published: Optional[datetime]
    summary: str = ""
    id: str = field(init=False)

    def __post_init__(self) -> None:
        self.id = hashlib.sha1(normalize_url(self.url).encode()).hexdigest()[:10]


@dataclass
class SourceResult:
    source_id: str
    ok: bool
    count: int = 0
    error: str = ""


def normalize_url(url: str) -> str:
    url = re.sub(r"[?#].*$", "", url.strip())
    return url.rstrip("/").lower().replace("http://", "https://")


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:SUMMARY_CHARS]


def matches_keywords(item: Item, keywords: list[str]) -> bool:
    haystack = f"{item.title} {item.summary}".lower()
    return any(k.lower() in haystack for k in keywords)


def fetch_rss(src: dict, client: httpx.Client) -> list[Item]:
    resp = client.get(src["url"])
    resp.raise_for_status()
    feed = feedparser.parse(resp.content)
    if not feed.entries and feed.bozo:
        # Usually means we got an HTML page (bot block, login wall) instead of a feed.
        raise ValueError(f"not a valid feed ({type(feed.bozo_exception).__name__})")
    items = []
    for e in feed.entries:
        t = e.get("published_parsed") or e.get("updated_parsed")
        published = datetime.fromtimestamp(calendar.timegm(t), tz=timezone.utc) if t else None
        summary = e.get("summary") or (e.get("content") or [{}])[0].get("value", "")
        items.append(Item(src["id"], src["name"], clean_text(e.get("title", "")), e.get("link", ""), published, clean_text(summary)))
    return items


def fetch_federal_register(src: dict, client: httpx.Client, since: datetime) -> list[Item]:
    params = {
        "conditions[agencies][]": src["agency"],
        "conditions[term]": src["term"],
        "conditions[publication_date][gte]": since.date().isoformat(),
        "order": "newest",
        "per_page": 100,
        "fields[]": ["title", "html_url", "publication_date", "abstract", "type"],
    }
    resp = client.get("https://www.federalregister.gov/api/v1/documents.json", params=params)
    resp.raise_for_status()
    items = []
    for r in resp.json().get("results", []):
        published = datetime.fromisoformat(r["publication_date"]).replace(tzinfo=timezone.utc)
        title = f"{r['type']}: {r['title']}"
        items.append(Item(src["id"], src["name"], title, r["html_url"], published, clean_text(r.get("abstract") or "")))
    return items


def fetch_congress(src: dict, client: httpx.Client, since: datetime) -> list[Item]:
    key = os.environ.get("CONGRESS_API_KEY")
    if not key:
        raise ValueError("CONGRESS_API_KEY not set (free key: https://api.congress.gov/sign-up/)")
    items: list[Item] = []
    url: Optional[str] = "https://api.congress.gov/v3/bill"
    params: Optional[dict] = {
        "format": "json",
        "limit": 250,
        "sort": "updateDate desc",
        "fromDateTime": since.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    while url:
        resp = client.get(url, params={**(params or {}), "api_key": key})
        resp.raise_for_status()
        data = resp.json()
        for b in data.get("bills", []):
            latest = b.get("latestAction") or {}
            summary = f"Latest action ({latest.get('actionDate', '?')}): {latest.get('text', '')}"
            bill_url = f"https://www.congress.gov/bill/{b['congress']}th-congress/{_bill_path(b['type'])}/{b['number']}"
            updated = datetime.fromisoformat(b["updateDate"][:10]).replace(tzinfo=timezone.utc)
            items.append(Item(src["id"], src["name"], f"{b['type']} {b['number']}: {b['title']}", bill_url, updated, clean_text(summary)))
        url, params = (data.get("pagination") or {}).get("next"), None  # "next" already carries the query
    return items


def _bill_path(bill_type: str) -> str:
    return {
        "HR": "house-bill", "S": "senate-bill", "HRES": "house-resolution", "SRES": "senate-resolution",
        "HJRES": "house-joint-resolution", "SJRES": "senate-joint-resolution",
        "HCONRES": "house-concurrent-resolution", "SCONRES": "senate-concurrent-resolution",
    }.get(bill_type.upper(), bill_type.lower())


def collect(config: dict, now: Optional[datetime] = None) -> tuple[list[Item], list[SourceResult]]:
    """Fetch every source; one broken source never stops the run."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=config.get("window_days", 7))
    all_items: list[Item] = []
    results: list[SourceResult] = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        for src in config["sources"]:
            if src.get("enabled") is False:
                continue
            try:
                kind = src["type"]
                if kind == "rss":
                    items = fetch_rss(src, client)
                elif kind == "federal_register":
                    items = fetch_federal_register(src, client, since)
                elif kind == "congress":
                    items = fetch_congress(src, client, since)
                else:
                    raise ValueError(f"unknown source type {kind!r}")
                items = [i for i in items if i.title and i.url and (i.published is None or i.published >= since)]
                if src.get("keywords"):
                    items = [i for i in items if matches_keywords(i, src["keywords"])]
                all_items.extend(items)
                results.append(SourceResult(src["id"], True, len(items)))
                log.info("%-22s %3d items", src["id"], len(items))
            except Exception as e:  # noqa: BLE001 - report and keep going
                results.append(SourceResult(src["id"], False, error=str(e)[:200]))
                log.warning("%-22s FAILED: %s", src["id"], e)
    return dedupe(all_items), results


def dedupe(items: list[Item]) -> list[Item]:
    seen_ids, seen_titles, out = set(), set(), []
    for i in items:
        title_key = re.sub(r"\W+", "", i.title.lower())
        if i.id in seen_ids or title_key in seen_titles:
            continue
        seen_ids.add(i.id)
        seen_titles.add(title_key)
        out.append(i)
    return out
