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
    a = [llm.ItemAssessment(id=items[0].id, relevance=7, section="sector", summary="", why_it_matters="", topics=[], emerging_topic=""),
         llm.ItemAssessment(id=items[1].id, relevance=9, section="policy", summary="", why_it_matters="", topics=[], emerging_topic=""),
         llm.ItemAssessment(id=items[2].id, relevance=9, section="none", summary="", why_it_matters="", topics=[], emerging_topic="")]
    picked = llm.pick_top(items, a, min_relevance=6, limit=5)
    assert [i.title for i, _ in picked] == ["B", "A"]


def brief(**kw):
    return llm.Brief(**{"subject": "S", "takeaways": [], "trends": [], "sections": [], **kw})


def test_render_uses_real_urls_drops_unknown_ids_and_escapes_html(tmp_path):
    items = [item("Real", "https://e.org/real")]
    sections = [{"id": "sector", "title": "Sector News", "description": ""}]
    b = brief(subject="Subject <script>x</script>", sections=[llm.BriefSection(section_id="sector", entries=[
        llm.BriefEntry(id=items[0].id, headline="Real & true", body="B"),
        llm.BriefEntry(id="madeup1234", headline="Invented", body="B"),
    ])])
    rendered = main.render(b, sections, items, [], "2026-10-12", "October 12, 2026")
    assert [e["url"] for e in rendered["sections"][0]["entries"]] == ["https://e.org/real"]
    main.write_outputs(tmp_path, b, rendered, [])
    html = (tmp_path / "brief.html").read_text()
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "Invented" not in html


def test_eval_scoring_counts_misses_sections_and_leaks():
    from donorbrief.evaluate import score
    items = [item("Keep", "https://e.org/k"), item("Unsure", "https://e.org/u"), item("Noise", "https://e.org/n")]
    keep, unsure, noise = (i.id for i in items)
    a = lambda i, rel, sec: llm.ItemAssessment(id=i, relevance=rel, section=sec, summary="", why_it_matters="", topics=[], emerging_topic="")
    result = score(items, [a(keep, 8, "research"), a(unsure, 7, "sector"), a(noise, 9, "sector")],
                   include={keep: ["policy"]}, maybe={unsure}, min_relevance=6, limit=20)
    assert result["include_recall"] == 1.0
    assert result["section_accuracy"] == 0.0
    assert result["exclude_leak_rate"] == 1.0
    assert result["top"] == {"size": 3, "include": 1, "maybe": 1, "exclude": 1}


def test_article_opening_skips_chrome_and_short_lines():
    from donorbrief.articles import opening_text
    page = ("<html><nav><p>" + "Menu item text that is long enough to count as a paragraph here." * 2 + "</p></nav>"
            "<article><p>Photo: credit</p><p>" + "First real paragraph of the story. " * 4 + "</p>"
            "<script>var x = '<p>not text</p>';</script><p>" + "Second paragraph continues the story. " * 4 + "</p></article></html>")
    text = opening_text(page)
    assert text.startswith("First real paragraph")
    assert "Second paragraph" in text and "Menu item" not in text and "Photo" not in text


def test_sponsored_items_detected():
    from donorbrief.articles import is_sponsored
    assert is_sponsored(item("How nonprofits can build", "https://www.philanthropy.com/sponsored/how-nonprofits"))
    assert not is_sponsored(item("Real news", "https://www.philanthropy.com/news/real-news"))


def test_enrich_fills_thin_items_only():
    from donorbrief.articles import enrich
    body = "<p>" + "The article explains what happened in useful detail. " * 3 + "</p>"
    thin, full, gnews = item("Thin", "https://e.org/thin"), item("Full", "https://e.org/full"), item("G", "https://news.google.com/rss/articles/x")
    full.summary = "x" * 400
    with mock_client(lambda req: httpx.Response(200, text=body)) as client:
        assert enrich([thin, full, gnews], client) == 1
    assert "useful detail" in thin.summary and full.summary == "x" * 400 and gnews.summary == ""


def test_archive_builds_index_and_pages_newest_first(tmp_path):
    import json
    from donorbrief.archive import build
    for slug, subject in [("2026-10-05", "Older <b>week</b>"), ("2026-10-12", "Newer week")]:
        (tmp_path / "archive" / slug).mkdir(parents=True)
        (tmp_path / "archive" / slug / "rendered.json").write_text(json.dumps({
            "subject": subject, "intro": "Intro.", "week_of": slug,
            "sections": [{"title": "Sector News", "entries": [{"headline": "H", "body": "B", "url": "https://e.org", "source": "Src"}]}]}))
    assert build(tmp_path / "archive", tmp_path / "site") == 2
    index = (tmp_path / "site" / "index.html").read_text()
    assert index.index("Newer week") < index.index("Older")
    assert "&lt;b&gt;" in index  # escaped
    newer = (tmp_path / "site" / "2026-10-12" / "index.html").read_text()
    assert 'href="../2026-10-05/"' in newer and "https://e.org" in newer


def test_brief_layout_takeaways_then_trends_then_news(tmp_path):
    items = [item("A", "https://e.org/a"), item("B", "https://e.org/b"), item("C", "https://e.org/c")]
    sections = [{"id": "policy", "title": "Policy & Regulation", "description": "Laws and rules."},
                {"id": "sector", "title": "Sector News", "description": ""}]
    past = [{"week": "2026-10-05", "items": [{"id": "old1", "title": "Earlier DAF story", "url": "https://e.org/old1", "source": "Old"}]}]
    a, b_, c = (i.id for i in items)
    b = brief(
        takeaways=[
            llm.Takeaway(lead="Ford is narrowing its focus", detail="Grantees will feel it.", item_ids=[a, "madeup"]),
            llm.Takeaway(lead="Invented", detail="x", item_ids=["madeup"]),            # no real story: dropped
            llm.Takeaway(lead="Two stories, one point", detail="y", item_ids=[a, c]),
        ],
        trends=[llm.Trend(title="DAFs under scrutiny", body="Building.", item_ids=[b_, c, "old1"])],
        sections=[
            llm.BriefSection(section_id="sector", entries=[llm.BriefEntry(id=c, headline="Third", body="One line.")]),
            llm.BriefSection(section_id="policy", entries=[llm.BriefEntry(id=a, headline="Big one", body="Longer.", follow_up=True),
                                                           llm.BriefEntry(id=b_, headline="Second?", body="One line."),
                                                           llm.BriefEntry(id=a, headline="Big one again", body="dup")]),
        ])
    r = main.render(b, sections, items, past, "2026-10-12", "October 12, 2026")
    assert [(t["n"], t["lead"]) for t in r["takeaways"]] == [(1, "Ford is narrowing its focus"), (2, "Two stories, one point")]
    assert [s["source"] for s in r["takeaways"][1]["stories"]] == ["src", "src"]
    assert [s["id"] for s in r["sections"]] == ["policy", "sector"]
    policy = r["sections"][0]["entries"]
    assert [e["headline"] for e in policy] == ["Big one", "Second?"]           # each story once
    assert policy[0]["takeaways"] == [1, 2] and policy[1]["takeaways"] == []
    assert r["intro"] == "Ford is narrowing its focus. Two stories, one point."
    assert (r["trends"][0]["stories"], r["trends"][0]["weeks"]) == (3, 2)
    main.write_outputs(tmp_path, b, r, [])
    html = (tmp_path / "brief.html").read_text()
    assert html.index("Key takeaways") < html.index("s building") < html.index("The news")
    assert "Takeaway 2" in html and "Update" in html and "Second?." not in html and "Invented" not in html
    md = (tmp_path / "brief.md").read_text()
    assert md.index("## Key takeaways") < md.index("## What's building") < md.index("# The news")


def test_trends_need_three_stories_over_two_weeks():
    items = [item("A", "https://e.org/a"), item("B", "https://e.org/b"), item("C", "https://e.org/c")]
    ids = [i.id for i in items]
    past = [{"week": "2026-10-05", "items": [{"id": "old1", "title": "T", "url": "https://e.org/o", "source": "S"}]}]
    b = brief(trends=[
        llm.Trend(title="One week only", body="x", item_ids=ids),                 # 3 stories, 1 week
        llm.Trend(title="Too few", body="x", item_ids=[ids[0], "old1"]),          # 2 stories
        llm.Trend(title="Invented ids", body="x", item_ids=["nope1", "nope2", ids[0]]),
        llm.Trend(title="Real", body="x", item_ids=[ids[0], ids[1], "old1", ids[1]]),  # duplicate id counted once
    ])
    r = main.render(b, [], items, past, "2026-10-12", "October 12, 2026")
    assert [(t["title"], t["stories"], t["weeks"]) for t in r["trends"]] == [("Real", 3, 2)]


def test_history_records_and_dedupes_across_weeks(tmp_path):
    from donorbrief import history
    a, b = item("Story A", "https://e.org/a"), item("Story B", "https://e.org/b")
    asmt = lambda i, rel: llm.ItemAssessment(id=i.id, relevance=rel, section="sector", summary="s", why_it_matters="w", topics=[], emerging_topic="")
    history.record(tmp_path, "2026-10-05", [a, b], [asmt(a, 8), asmt(b, 2)], {a.id})
    history.record(tmp_path, "2026-10-12", [a, b], [asmt(a, 8), asmt(b, 7)])
    history.record(tmp_path, "2026-10-19", [b], [asmt(b, 9)])
    weeks = history.load_recent(tmp_path, before="2026-10-19")
    assert [w["week"] for w in weeks] == ["2026-10-12", "2026-10-05"]
    assert [i["title"] for i in weeks[1]["items"]] == ["Story A"]          # low score dropped
    assert weeks[1]["items"][0]["in_brief"] is True and "headline" not in weeks[1]["items"][0]
    assert [i["title"] for i in weeks[0]["items"]] == ["Story B"]          # A already seen earlier


def test_history_weeks_are_labeled_by_monday_and_keep_brief_marks(tmp_path):
    from datetime import date
    from donorbrief import history
    assert history.week_label(date(2026, 11, 10)) == "2026-11-09" == history.week_label(date(2026, 11, 9))
    a = item("Story A", "https://e.org/a")
    asmt = llm.ItemAssessment(id=a.id, relevance=8, section="sector", summary="s", why_it_matters="w", topics=[], emerging_topic="")
    history.record(tmp_path, "2026-10-05", [a], [asmt])
    history.record(tmp_path, "2026-10-12", [a], [asmt], {a.id})   # ran in the brief the second week
    weeks = history.load_recent(tmp_path, before="2026-10-19")
    assert weeks[1]["items"][0]["in_brief"] is True and weeks[0]["items"] == []


def test_compose_shows_earlier_weeks_to_the_editor():
    seen = {}

    class Fake:
        def structured(self, model, system, prompt, schema, effort):
            seen["prompt"], seen["system"] = prompt, system
            return brief()

    i = item("Now", "https://e.org/now")
    a = llm.ItemAssessment(id=i.id, relevance=8, section="sector", summary="s", why_it_matters="w", topics=[], emerging_topic="")
    past = [{"week": "2026-10-05", "items": [
        {"id": "old1", "title": "Ran before", "url": "u", "source": "S", "section": "sector", "summary": "x", "relevance": 8, "in_brief": True},
        {"id": "old2", "title": "Low score", "url": "u", "source": "S", "section": "sector", "summary": "x", "relevance": 4, "in_brief": False}]}]
    llm.compose(Fake(), "m", [(i, a)], [], "October 12, 2026", past)
    assert "Ran before" in seen["prompt"] and "already_in_a_previous_brief" in seen["prompt"] and "Low score" not in seen["prompt"]
    assert "Key takeaways" in seen["system"] and "Trends to watch" in seen["system"]


def test_triage_keeps_only_known_topics_and_history_saves_them(tmp_path):
    from donorbrief import history
    i = item("DAF rules", "https://e.org/daf")
    topics = [{"id": "donor-advised-funds", "description": "DAFs"}, {"id": "charitable-tax-policy", "description": "tax"}]
    seen = {}

    class Fake:
        def structured(self, model, system, prompt, schema, effort):
            seen["system"] = system
            return llm.TriageResult(items=[llm.ItemAssessment(
                id=i.id, relevance=8, section="policy", summary="s", why_it_matters="w",
                topics=["donor-advised-funds", "made-up", "donor-advised-funds", "charitable-tax-policy"],
                emerging_topic="  DAF payout rules  ")])

    (a,) = llm.triage(Fake(), "m", [i], [], topics)
    assert a.topics == ["donor-advised-funds", "charitable-tax-policy"] and a.emerging_topic == "DAF payout rules"
    assert "donor-advised-funds: DAFs" in seen["system"] and "secondary interests" in seen["system"]
    history.record(tmp_path, "2026-10-12", [i], [a])
    saved = history.load_recent(tmp_path, before="2026-10-19")[0]["items"][0]
    assert saved["topics"] == ["donor-advised-funds", "charitable-tax-policy"] and saved["emerging_topic"] == "DAF payout rules"


def test_retag_adds_topics_to_old_history_without_changing_scores(tmp_path, monkeypatch):
    import json
    from donorbrief import history
    old = {"week": "2026-10-05", "items": [{"id": item("A", "https://e.org/a").id, "title": "A", "url": "https://e.org/a",
                                           "source": "S", "relevance": 7, "section": "sector", "summary": "s", "in_brief": False}]}
    (tmp_path / "2026-10-05.json").write_text(json.dumps(old))

    class Fake:
        def structured(self, model, system, prompt, schema, effort):
            ids = [d["id"] for d in json.loads(prompt.split("\n\n", 1)[1])]
            return llm.TriageResult(items=[llm.ItemAssessment(id=x, relevance=2, section="none", summary="new", why_it_matters="",
                                                              topics=["giving-data"], emerging_topic="") for x in ids])

    monkeypatch.setattr(llm, "get_provider", lambda name: Fake())
    history.retag(tmp_path, {"llm": {"provider": "x", "triage_model": "m"}, "sections": []}, [{"id": "giving-data", "description": "d"}])
    saved = json.loads((tmp_path / "2026-10-05.json").read_text())["items"][0]
    assert saved["topics"] == ["giving-data"] and saved["relevance"] == 7 and saved["summary"] == "s"


def test_topics_config_is_valid():
    from pathlib import Path
    topics = llm.load_topics(Path(__file__).resolve().parent.parent / "config" / "topics.yaml")
    ids = [t["id"] for t in topics]
    assert len(ids) == len(set(ids)) >= 20 and all(t["description"] for t in topics)
