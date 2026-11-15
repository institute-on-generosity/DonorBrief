import json

import httpx
import pytest

from donorbrief import mailchimp

KEY = "abc123-us21"  # fake; real keys are longer
CFG = {"mailchimp": {"enabled": True, "audience_id": "abc123", "from_name": "Institute on Generosity",
                     "reply_to": "info@example.org", "test_emails": ["reviewer@example.org"]}}
RENDERED = {
    "subject": "Billion-dollar gifts & the IRS's year ahead",
    "intro": "This week's giving news was dominated by size. Second sentence here.",
    "week_of": "October 12, 2026",
    "sections": [{"title": "Sector News", "entries": [
        {"headline": "A <b>big</b> gift", "body": "Details.", "url": "https://example.org/a", "source": "Chronicle"}]}],
}


@pytest.fixture
def draft(tmp_path):
    (tmp_path / "rendered.json").write_text(json.dumps(RENDERED))
    return tmp_path


def recording_client(calls, fail_on=None):
    def handler(req):
        calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None, req.headers))
        assert req.url.host == "us21.api.mailchimp.com"
        if fail_on and fail_on in req.url.path:
            return httpx.Response(400, json={"title": "Invalid Resource", "detail": f"bad request for key {KEY}",
                                             "errors": [{"field": "recipients.list_id", "message": "not found"}]})
        if req.url.path.endswith("/campaigns"):
            return httpx.Response(200, json={"id": "c1", "web_id": 42})
        return httpx.Response(204)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_server_prefix():
    assert mailchimp.server_prefix(KEY) == "us21"
    with pytest.raises(mailchimp.MailchimpError):
        mailchimp.server_prefix("not-a-key")


def test_creates_draft_sets_content_and_sends_preview_never_sends(draft, monkeypatch):
    monkeypatch.setenv("MAILCHIMP_API_KEY", KEY)
    calls = []
    result = mailchimp.deliver(draft, CFG, recording_client(calls))

    assert result["status"] == "draft_created"
    assert result["edit_url"] == "https://us21.admin.mailchimp.com/campaigns/edit?id=42"
    assert [(m, p) for m, p, _, _ in calls] == [
        ("POST", "/3.0/campaigns"),
        ("PUT", "/3.0/campaigns/c1/content"),
        ("POST", "/3.0/campaigns/c1/actions/test"),
    ]
    create = calls[0][2]
    assert create["recipients"]["list_id"] == "abc123"
    assert create["settings"]["subject_line"] == RENDERED["subject"]
    assert create["settings"]["preview_text"] == "This week's giving news was dominated by size."
    html = calls[1][2]["html"]
    assert "*|UNSUB|*" in html and "*|LIST:ADDRESSLINE|*" in html
    assert "A &lt;b&gt;big&lt;/b&gt; gift" in html  # model text stays escaped
    assert calls[2][2] == {"test_emails": ["reviewer@example.org"], "send_type": "html"}
    assert not any(p.endswith("/actions/send") for _, p, _, _ in calls)
    assert calls[0][3]["authorization"].startswith("Basic ")
    assert json.loads((draft / "delivery.json").read_text())["status"] == "draft_created"


def test_skips_without_key(draft, monkeypatch):
    monkeypatch.delenv("MAILCHIMP_API_KEY", raising=False)
    calls = []
    assert mailchimp.deliver(draft, CFG, recording_client(calls))["status"] == "skipped"
    assert calls == []


def test_missing_settings_fail_before_any_call(draft, monkeypatch):
    monkeypatch.setenv("MAILCHIMP_API_KEY", KEY)
    calls = []
    cfg = {"mailchimp": {**CFG["mailchimp"], "audience_id": ""}}
    result = mailchimp.deliver(draft, cfg, recording_client(calls))
    assert result["status"] == "failed" and "audience_id" in result["error"]
    assert calls == []


def test_api_errors_are_reported_without_the_key(draft, monkeypatch):
    monkeypatch.setenv("MAILCHIMP_API_KEY", KEY)
    result = mailchimp.deliver(draft, CFG, recording_client([], fail_on="/campaigns"))
    assert result["status"] == "failed"
    assert "recipients.list_id: not found" in result["error"]
    assert KEY not in result["error"] and "REDACTED" in result["error"]


def test_regular_email_has_no_mailchimp_tags():
    from jinja2 import Environment, FileSystemLoader
    env = Environment(loader=FileSystemLoader("donorbrief/templates"), autoescape=True)
    html = env.get_template("brief.html.j2").render(brief=RENDERED, sections=RENDERED["sections"], week_of="x")
    assert "*|UNSUB|*" not in html


def tag_client(calls, segments):
    def handler(req):
        calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if "/segments" in req.url.path:
            assert req.url.params["type"] == "static"
            return httpx.Response(200, json={"segments": segments})
        if req.url.path.endswith("/campaigns"):
            return httpx.Response(200, json={"id": "c1", "web_id": 42})
        return httpx.Response(204)
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_tag_limits_recipients_to_tagged_contacts(draft, monkeypatch):
    monkeypatch.setenv("MAILCHIMP_API_KEY", KEY)
    cfg = {"mailchimp": {**CFG["mailchimp"], "send_to_tag": "donorbrief demo"}}
    calls = []
    result = mailchimp.deliver(draft, cfg, tag_client(calls, [{"id": 7, "name": "Board", "member_count": 9},
                                                               {"id": 88, "name": "DonorBrief demo", "member_count": 12}]))
    assert result["status"] == "draft_created"
    create = next(c for c in calls if c[0] == "POST" and c[1].endswith("/campaigns"))
    assert create[2]["recipients"] == {"list_id": "abc123", "segment_opts": {"saved_segment_id": 88}}


@pytest.mark.parametrize("segments", [[{"id": 7, "name": "Board", "member_count": 9}],
                                      [{"id": 88, "name": "DonorBrief demo", "member_count": 0}]])
def test_missing_or_empty_tag_never_falls_back_to_whole_audience(draft, monkeypatch, segments):
    monkeypatch.setenv("MAILCHIMP_API_KEY", KEY)
    cfg = {"mailchimp": {**CFG["mailchimp"], "send_to_tag": "DonorBrief demo"}}
    calls = []
    result = mailchimp.deliver(draft, cfg, tag_client(calls, segments))
    assert result["status"] == "failed" and "DonorBrief demo" in result["error"]
    assert not any(c[0] == "POST" for c in calls)   # no campaign was created at all
