"""Diagnose the latest DonorBrief draft in Mailchimp without printing its text (CI logs are public).

    python -m donorbrief.inspect_draft
"""
from __future__ import annotations

import os
import re
import sys

import httpx

from .mailchimp import api

MARKERS = ["Key takeaways", "What&#39;s building", "The news", "donorbrief:news", "/donorbrief:news",
           "Takeaway 1", "Unsubscribe", "</h1>", "</body>", "</html>"]


def main() -> int:
    key = os.environ["MAILCHIMP_API_KEY"].strip()
    with httpx.Client() as client:
        call = api(client, key)
        found = call("GET", "/campaigns?status=save&count=50&sort_field=create_time&sort_dir=DESC"
                            "&fields=campaigns.id,campaigns.web_id,campaigns.create_time,campaigns.settings.title,"
                            "campaigns.content_type")
        drafts = [c for c in found.get("campaigns", []) if c["settings"]["title"].startswith("DonorBrief")]
        for c in drafts[:3]:
            content = call("GET", f"/campaigns/{c['id']}/content")
            html, text = content.get("html") or "", content.get("plain_text") or ""
            print(f"== draft web_id={c['web_id']} created={c['create_time']} content_type={c.get('content_type')}")
            print(f"   html bytes={len(html)} plain_text chars={len(text)} links={len(re.findall(r'<a ', html))} "
                  f"tables={html.count('<table')} rows={html.count('<tr')}")
            print("   markers:", {m: html.find(m) for m in MARKERS})
            print("   html keys:", sorted(content.keys()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
