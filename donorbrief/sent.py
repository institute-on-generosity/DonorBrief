"""Archive the briefs that were actually sent, and correct the no-repeats record to match.

    python -m donorbrief.sent        # needs MAILCHIMP_API_KEY

Staff review each draft in Mailchimp and may edit it, cut stories, or not send it at all. So the
weekly run starts by asking Mailchimp which DonorBrief campaigns were sent, and for each new one:

1. saves exactly what went out to archive/<send date>/ (email.html plus meta.json) for the public
   archive, minus Mailchimp's unsubscribe lines; and
2. updates that week's history so only stories that were really sent count as covered: a story
   staff cut can still run another week.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import httpx

from . import history
from .mailchimp import MailchimpError, api
from .sources import item_id

ROOT = Path(__file__).resolve().parent.parent
ARCHIVE = ROOT / "archive"
log = logging.getLogger("donorbrief.sent")

CAMPAIGN_FIELDS = ("campaigns.id,campaigns.send_time,campaigns.settings.title,campaigns.settings.subject_line,"
                   "campaigns.settings.preview_text,campaigns.long_archive_url")
NEWS = re.compile(r"<!--\s*donorbrief:news\s*-->(.*?)<!--\s*/donorbrief:news\s*-->", re.S)
FOOTER = re.compile(r"<!--\s*donorbrief:mailchimp-footer\s*-->.*?<!--\s*/donorbrief:mailchimp-footer\s*-->", re.S)
HREF = re.compile(r'href="(https?://[^"]+)"')
NOT_STORIES = ("fonts.googleapis.com", "mailchimp.com", "list-manage.com", "instituteongenerosity.org")


def story_links(html: str) -> list[str]:
    """Links to stories in the sent news section (all story-looking links if the markers were lost)."""
    m = NEWS.search(html)
    links = HREF.findall(m.group(1) if m else html)
    return list(dict.fromkeys(u.replace("&amp;", "&") for u in links if not any(h in u for h in NOT_STORIES)))


def public_copy(html: str) -> str:
    """The sent email without Mailchimp's per-subscriber lines (unsubscribe link, merge tags)."""
    html = FOOTER.sub("", html)
    return re.sub(r"[^<>\n]*\*\|[A-Z_:]+\|\*[^<>\n]*", "", html)


def reconcile(history_dir: Path, sent_on: date, sent_ids: set[str]) -> Optional[str]:
    """Mark exactly the sent stories as covered in the week the draft came from.

    The draft's week is the recent history file sharing the most stories with the email (usually the
    week it was sent, but staff might send a week late).
    """
    best, overlap = None, 0
    for weeks_back in range(0, 3):
        label = history.week_label(date.fromordinal(sent_on.toordinal() - 7 * weeks_back))
        path = history_dir / f"{label}.json"
        if path.exists():
            n = sum(i["id"] in sent_ids for i in json.loads(path.read_text())["items"])
            if n > overlap:
                best, overlap = path, n
    if best is None:
        return None
    week = json.loads(best.read_text())
    for i in week["items"]:
        i["in_brief"] = i["id"] in sent_ids
    best.write_text(json.dumps(week, indent=1))
    return week["week"]


def sync(key: str, client: httpx.Client, archive_dir: Path = ARCHIVE, history_dir: Path = history.HISTORY) -> list[dict]:
    """Archive every sent DonorBrief campaign not archived yet. Returns what was archived."""
    call = api(client, key)
    sent = call("GET", f"/campaigns?status=sent&count=100&sort_field=send_time&sort_dir=DESC&fields={CAMPAIGN_FIELDS}")
    done = {json.loads(m.read_text())["campaign_id"] for m in archive_dir.glob("*/meta.json")}
    archived = []
    for c in sorted(sent.get("campaigns", []), key=lambda c: c["send_time"]):
        settings = c.get("settings", {})
        if not settings.get("title", "").startswith("DonorBrief") or c["id"] in done:
            continue
        html = call("GET", f"/campaigns/{c['id']}/content").get("html", "")
        sent_on = date.fromisoformat(c["send_time"][:10])
        folder = archive_dir / sent_on.isoformat()
        n = 2
        while folder.exists():  # two briefs sent the same day
            folder = archive_dir / f"{sent_on.isoformat()}-{n}"
            n += 1
        folder.mkdir(parents=True)
        links = story_links(html)
        (folder / "email.html").write_text(public_copy(html))
        week = reconcile(history_dir, sent_on, {item_id(u) for u in links})
        meta = {"campaign_id": c["id"], "subject": settings.get("subject_line", ""),
                "preview_text": settings.get("preview_text", ""), "sent_at": c["send_time"],
                "mailchimp_archive_url": c.get("long_archive_url"), "stories": links, "history_week": week}
        (folder / "meta.json").write_text(json.dumps(meta, indent=2))
        log.info("Archived sent brief %r (%s, %d stories; history week %s)", meta["subject"], sent_on, len(links), week)
        archived.append(meta)
    return archived


def sync_if_configured(archive_dir: Path = ARCHIVE, history_dir: Path = history.HISTORY) -> list[dict]:
    """The weekly run's entry point: never fails the run, just logs what happened."""
    key = os.environ.get("MAILCHIMP_API_KEY", "").strip()
    if not key:
        log.info("Sent-brief sync skipped: MAILCHIMP_API_KEY is not set")
        return []
    try:
        with httpx.Client() as client:
            return sync(key, client, archive_dir, history_dir)
    except MailchimpError as e:
        log.warning("Sent-brief sync failed: %s", str(e).replace(key, "REDACTED"))
        return []


def main() -> int:
    argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter).parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    archived = sync_if_configured()
    print(f"Archived {len(archived)} sent brief(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
