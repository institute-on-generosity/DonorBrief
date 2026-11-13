import json

import httpx

from donorbrief import archive, history, llm, mailchimp, sent
from donorbrief.sources import Item

KEY = "0123456789abcdef-us22"


def story(title, url):
    return Item("src", "Chronicle of Philanthropy", title, url, None, "")


def sent_email(stories, trend_link):
    """A brief rendered by the real template, as Mailchimp would hold it after staff edits."""
    rendered = {
        "subject": "Ford narrows its focus", "intro": "Ford narrows.", "week_of": "November 16, 2026",
        "takeaways": [{"n": 1, "lead": "Ford narrows", "detail": "d",
                       "stories": [{"anchor": "story-1", "headline": "h", "url": stories[0].url, "source": "S"}]}],
        "trends": [{"title": "T", "body": "b", "stories": 3, "weeks": 2, "since": "2026-11-02",
                    "evidence": [{"title": "Older", "url": trend_link, "source": "S", "week": "2026-11-02"}]}],
        "sections": [{"id": "sector", "title": "Sector", "description": "", "entries": [
            {"id": s.id, "anchor": f"story-{n}", "headline": s.title, "body": "b", "body_parts": None,
             "why_it_matters": "", "url": s.url + "?utm_source=x", "source": "S", "takeaways": []}
            for n, s in enumerate(stories, 1)]}],
    }
    return mailchimp.render_email(rendered)


def fake_mailchimp(campaigns, contents, calls):
    def handler(req):
        calls.append((req.method, req.url.path))
        assert req.method == "GET"  # syncing only ever reads
        if req.url.path.endswith("/campaigns"):
            assert req.url.params["status"] == "sent"
            return httpx.Response(200, json={"campaigns": campaigns})
        cid = req.url.path.split("/")[-2]
        return httpx.Response(200, json={"html": contents[cid]})
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_sync_archives_what_was_sent_and_corrects_history(tmp_path):
    kept, cut, also = story("Kept", "https://e.org/kept"), story("Cut by staff", "https://e.org/cut"), story("Also", "https://e.org/also")
    a = lambda i: llm.ItemAssessment(id=i.id, relevance=8, section="sector", summary="s", why_it_matters="w",
                                     topics=[], emerging_topic="")
    hist = tmp_path / "history"
    history.record(hist, "2026-11-16", [kept, cut, also], [a(kept), a(cut), a(also)], {kept.id, cut.id, also.id})

    html = sent_email([kept, also], trend_link="https://e.org/older-trend-story")   # staff removed "Cut by staff"
    campaigns = [
        {"id": "c1", "send_time": "2026-11-17T14:00:00+00:00", "long_archive_url": "https://mailchi.mp/x",
         "settings": {"title": "DonorBrief: November 16, 2026", "subject_line": "Ford narrows its focus", "preview_text": "Ford narrows."}},
        {"id": "c2", "send_time": "2026-11-18T14:00:00+00:00", "settings": {"title": "Gala invite", "subject_line": "Join us"}},
    ]
    calls = []
    with fake_mailchimp(campaigns, {"c1": html}, calls) as client:
        done = sent.sync(KEY, client, tmp_path / "archive", hist)

    assert [d["subject"] for d in done] == ["Ford narrows its focus"]           # other campaigns ignored
    folder = tmp_path / "archive" / "2026-11-17"
    meta = json.loads((folder / "meta.json").read_text())
    assert meta["stories"] == ["https://e.org/kept?utm_source=x", "https://e.org/also?utm_source=x"]  # not the trend link
    assert meta["history_week"] == "2026-11-16"
    public = (folder / "email.html").read_text()
    assert "*|UNSUB|*" not in public and "Unsubscribe" not in public and "Kept" in public

    covered = {i["title"]: i["in_brief"] for i in json.loads((hist / "2026-11-16.json").read_text())["items"]}
    assert covered == {"Kept": True, "Cut by staff": False, "Also": True}

    # A second run finds nothing new and doesn't call for content again.
    calls.clear()
    with fake_mailchimp(campaigns, {"c1": html}, calls) as client:
        assert sent.sync(KEY, client, tmp_path / "archive", hist) == []
    assert calls == [("GET", "/3.0/campaigns")]

    # The public archive publishes the sent email itself, with a way back to the index.
    assert archive.build(tmp_path / "archive", tmp_path / "site") == 1
    index = (tmp_path / "site" / "index.html").read_text()
    assert "Ford narrows its focus" in index and "November 17, 2026" in index
    page = (tmp_path / "site" / "2026-11-17" / "index.html").read_text()
    assert "All DonorBrief issues" in page and "Kept" in page


def test_sync_without_key_does_nothing(monkeypatch, tmp_path):
    monkeypatch.delenv("MAILCHIMP_API_KEY", raising=False)
    assert sent.sync_if_configured(tmp_path / "archive", tmp_path / "history") == []
    assert not (tmp_path / "archive").exists()


def test_sync_errors_never_fail_the_run_or_leak_the_key(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("MAILCHIMP_API_KEY", KEY)
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(
        lambda req: httpx.Response(401, json={"detail": f"bad key {KEY}"}))))
    assert sent.sync_if_configured(tmp_path / "archive", tmp_path / "history") == []
    assert "Sent-brief sync failed" in caplog.text and KEY not in caplog.text
