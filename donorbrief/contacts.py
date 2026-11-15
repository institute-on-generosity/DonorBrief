"""Add contacts to the Mailchimp audience with a tag (e.g. the DonorBrief demo group).

    CONTACTS_CSV="$(cat contacts.csv)" python -m donorbrief.contacts

Reads a CSV (Email Address, First Name, Last Name) from the CONTACTS_CSV environment variable, so
the list can be passed as a secret. This runs on public CI: it never prints names or addresses,
only counts and row numbers.
"""
from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import sys
from pathlib import Path

import httpx
import yaml

from .mailchimp import MailchimpError, api

ROOT = Path(__file__).resolve().parent.parent
EMAIL = re.compile(r"[^\s@'\"<>]+@[^\s@'\"<>]+")


def tag_contacts(rows: list[dict], audience: str, tag: str, call) -> dict:
    result = {"total": len(rows), "tagged": 0, "not_subscribed": 0, "failed_rows": []}
    for n, row in enumerate(rows, 1):
        email = (row.get("Email Address") or "").strip().lower()
        member = f"/lists/{audience}/members/{hashlib.md5(email.encode()).hexdigest()}"
        names = {k: v for k, v in (("FNAME", (row.get("First Name") or "").strip()),
                                   ("LNAME", (row.get("Last Name") or "").strip())) if v}
        try:
            body = {"email_address": email, "status_if_new": "subscribed"}
            try:
                got = call("PUT", member, {**body, "merge_fields": names} if names else body)
            except MailchimpError:
                got = call("PUT", member, body)  # e.g. the audience has no name fields
            call("POST", member + "/tags", {"tags": [{"name": tag, "status": "active"}]})
            result["tagged"] += 1
            if got.get("status") != "subscribed":
                result["not_subscribed"] += 1  # tagged, but won't receive email (unsubscribed or pending)
        except MailchimpError as e:
            result["failed_rows"].append(f"row {n}: {EMAIL.sub('[email]', str(e))[:160]}")
    return result


def main() -> int:
    key = os.environ.get("MAILCHIMP_API_KEY", "").strip()
    data = os.environ.get("CONTACTS_CSV", "").strip()
    if not key or not data:
        print("Need MAILCHIMP_API_KEY and CONTACTS_CSV")
        return 1
    cfg = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text())["mailchimp"]
    tag = cfg["send_to_tag"]
    rows = [r for r in csv.DictReader(io.StringIO(data)) if (r.get("Email Address") or "").strip()]
    with httpx.Client() as client:
        call = api(client, key)
        result = tag_contacts(rows, cfg["audience_id"], tag, call)
        segs = call("GET", f"/lists/{cfg['audience_id']}/segments?type=static&count=1000&fields=segments.name,segments.member_count")
    count = next((s["member_count"] for s in segs.get("segments", []) if s["name"].lower() == tag.lower()), 0)
    print(f"Tagged {result['tagged']} of {result['total']} contacts with {tag!r}; "
          f"{result['not_subscribed']} of them are not subscribed.")
    print(f"Mailchimp now shows {count} subscribed contacts with that tag.")
    for f in result["failed_rows"]:
        print("FAILED", f.replace(key, "REDACTED"))
    return 1 if result["failed_rows"] else 0


if __name__ == "__main__":
    sys.exit(main())
