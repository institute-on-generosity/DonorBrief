"""Who did the latest sent DonorBrief go to? Prints counts and statuses by row number of the
CONTACTS_CSV secret, never addresses (CI logs are public).

    CONTACTS_CSV="$(cat contacts.csv)" python -m donorbrief.inspect_sent
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import sys
from pathlib import Path

import httpx
import yaml

from .mailchimp import MailchimpError, api

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    key = os.environ["MAILCHIMP_API_KEY"].strip()
    cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())["mailchimp"]
    audience, tag = cfg["audience_id"], cfg["send_to_tag"]
    rows = [r for r in csv.DictReader(io.StringIO(os.environ.get("CONTACTS_CSV", ""))) if r.get("Email Address")]
    with httpx.Client() as client:
        call = api(client, key)
        sent = call("GET", "/campaigns?status=sent&count=20&sort_field=send_time&sort_dir=DESC"
                           "&fields=campaigns.id,campaigns.send_time,campaigns.settings.title,campaigns.emails_sent,"
                           "campaigns.recipients.segment_text,campaigns.recipients.recipient_count")
        c = next(x for x in sent["campaigns"] if x["settings"]["title"].startswith("DonorBrief"))
        print(f"Latest sent: {c['settings']['title']} at {c['send_time']}; emails_sent={c.get('emails_sent')}; "
              f"recipient_count={c['recipients'].get('recipient_count')}")
        print("Segment:", (c["recipients"].get("segment_text") or "").replace("<strong>", "").replace("</strong>", ""))
        report = call("GET", f"/reports/{c['id']}/sent-to?count=200&fields=sent_to.email_address,sent_to.status")
        sent_to = {s["email_address"].lower(): s["status"] for s in report.get("sent_to", [])}
        print(f"Report lists {len(sent_to)} recipients")
        for n, r in enumerate(rows, 1):
            email = r["Email Address"].strip().lower()
            try:
                m = call("GET", f"/lists/{audience}/members/{hashlib.md5(email.encode()).hexdigest()}?fields=status,tags")
                status, tagged = m.get("status"), any(t["name"].lower() == tag.lower() for t in m.get("tags", []))
            except MailchimpError:
                status, tagged = "not in audience", False
            print(f"row {n}: member={status} tagged={tagged} sent={sent_to.get(email, 'NOT SENT')}")
        others = len([e for e in sent_to if e not in {r['Email Address'].strip().lower() for r in rows}])
        print(f"Recipients not on the contact list: {others}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
