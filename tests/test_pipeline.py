from datetime import datetime, timedelta, timezone

import httpx

from donorbrief import __main__ as main, llm
from donorbrief.sources import Item, collect, dedupe, fetch_rss, matches_keywords, redact

NOW = datetime(2026, 10, 12, 10, tzinfo=timezone.utc)

RSS = b"""<?xml version="1.0"?>
<rss version="2.0"><channel><title>Test</title>
<item><title>Fresh story</title><link>https://example.org/a</link>
  <pubDate>Fri, 09 Oct 2026 12:00:00 +0000</pubDate><description>&lt;p&gt;Big &amp;amp; new&lt;/p&gt;</description></item>
<item><title>Old story</title><link>https://example.org/b</link>
  <pubDate>Mon, 01 Sep 2026 12:00:00 +0000</pubDate><description>old</description></item>
<item><title>Pledge news - The Daily</title><link>https://news.example/c</link>
  <pubDate>Sat, 10 Oct 2026 12:00:00 +0000</pubDate><source url="https://daily.example">The Daily</source></item>
</channel></rss>"""


def item(title, url="https://example.org/x", source="src"):
    return Item(source, source, title, url, NOW)


_RealClient = httpx.Client


def mock_client(handler):
    return _RealClient(transport=httpx.MockTransport(handler))


def test_fetch_rss_parses_cleans_and_credits_publisher():
    with mock_client(lambda req: httpx.Response(200, content=RSS)) as client:
        items = fetch_rss({"id": "t", "name": "Test", "url": "https://feed.example/rss"}, client)
    assert [i.title for i in items] == ["Fresh story", "Old story", "Pledge news"]
    assert items[0].summary == "Big & new"
    assert items[2].source_name == "The Daily"


def test_fetch_rss_rejects_html_pages():
    page = b"<html><head><title>Attention Required! | Cloudflare</title></head><body>blocked</body></html>"
    with mock_client(lambda req: httpx.Response(200, content=page)) as client:
        try:
            fetch_rss({"id": "t", "name": "Test", "url": "https://feed.example/rss"}, client)
        except ValueError as e:
            assert "not a valid feed" in str(e)
        else:
            raise AssertionError("expected ValueError")


def test_collect_keeps_window_and_survives_failing_source(monkeypatch):
    def handler(req):
        return httpx.Response(200, content=RSS) if "good" in str(req.url) else httpx.Response(403)

    monkeypatch.setattr(httpx, "Client", lambda **kw: mock_client(handler))
    config = {"window_days": 7, "sources": [
        {"id": "good", "name": "Good", "type": "rss", "url": "https://good.example/rss"},
        {"id": "bad", "name": "Bad", "type": "rss", "url": "https://bad.example/rss"},
        {"id": "off", "name": "Off", "type": "rss", "url": "https://bad.example/x", "enabled": False},
    ]}
    items, results = collect(config, NOW)
    assert {i.title for i in items} == {"Fresh story", "Pledge news"}  # old item dropped
    assert [(r.source_id, r.ok) for r in results] == [("good", True), ("bad", False)]


def test_keywords_match_title_or_summary():
    assert matches_keywords(item("New Charitable deduction rules"), ["charit"])
    assert not matches_keywords(item("Highway bill"), ["charit"])


def test_dedupe_by_url_and_title():
    a = item("Same Story!", "https://example.org/a?utm_source=x")
    b = item("Other", "https://example.org/a")  # same url once tracking params are dropped
    c = item("same story", "https://elsewhere.org/z")  # same title
    assert dedupe([a, b, c]) == [a]


def test_redact_hides_api_keys(monkeypatch):
    monkeypatch.setenv("CONGRESS_API_KEY", "SECRET123")
    msg = "Client error '403' for url 'https://api.congress.gov/v3/bill?api_key=SECRET123&offset=0'"
    assert "SECRET123" not in redact(msg)
    assert "SECRET123" not in redact("key SECRET123 in a different place")


def test_pick_top_filters_and_sorts():
    items = [item("A", "https://e.org/a"), item("B", "https://e.org/b"), item("C", "https://e.org/c")]
    a = [llm.ItemAssessment(id=items[0].id, relevance=7, section="sector", summary="", why_it_matters=""),
         llm.ItemAssessment(id=items[1].id, relevance=9, section="policy", summary="", why_it_matters=""),
         llm.ItemAssessment(id=items[2].id, relevance=9, section="none", summary="", why_it_matters="")]
    picked = llm.pick_top(items, a, min_relevance=6, limit=5)
    assert [i.title for i, _ in picked] == ["B", "A"]


def test_render_uses_real_urls_drops_unknown_ids_and_escapes_html(tmp_path):
    items = [item("Real", "https://e.org/real")]
    sections = [{"id": "sector", "title": "Sector News", "description": ""}]
    brief = llm.Brief(subject="S", intro="Intro <script>x</script>", sections=[llm.BriefSection(section_id="sector", entries=[
        llm.BriefEntry(id=items[0].id, headline="Real & true", body="B"),
        llm.BriefEntry(id="madeup1234", headline="Invented", body="B"),
    ])])
    rendered = main.render_sections(brief, sections, items)
    assert [e["url"] for e in rendered[0]["entries"]] == ["https://e.org/real"]
    main.write_outputs(tmp_path, brief, rendered, "October 12, 2026", [])
    html = (tmp_path / "brief.html").read_text()
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "Invented" not in html
