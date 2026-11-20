"""Delete specific unsent DonorBrief drafts in Mailchimp.

    python -m donorbrief.cleanup_drafts 2135 2145 ...   # Mailchimp web ids (the ?id= in the edit link)

Deletes only campaigns that are (1) in the list given, (2) still drafts, and (3) titled "DonorBrief...".
Anything else is left alone and reported.
"""
from __future__ import annotations

import os
import sys

import httpx

from .mailchimp import api


def main() -> int:
    wanted = {int(x) for x in sys.argv[1:]}
    if not wanted:
        print("Give the web ids of the drafts to delete")
        return 1
    key = os.environ["MAILCHIMP_API_KEY"].strip()
    with httpx.Client() as client:
        call = api(client, key)
        found = call("GET", "/campaigns?count=200&fields=campaigns.id,campaigns.web_id,campaigns.status,campaigns.settings.title")
        by_web = {c["web_id"]: c for c in found.get("campaigns", [])}
        for web_id in sorted(wanted):
            c = by_web.get(web_id)
            if c is None:
                print(f"{web_id}: not found, skipped")
            elif c["status"] != "save" or not c["settings"]["title"].startswith("DonorBrief"):
                print(f"{web_id}: status={c['status']} title={c['settings']['title']!r}, NOT a DonorBrief draft, skipped")
            else:
                call("DELETE", f"/campaigns/{c['id']}")
                print(f"{web_id}: deleted draft {c['settings']['title']!r}")
        left = call("GET", "/campaigns?status=save&count=200&fields=campaigns.web_id,campaigns.settings.title")
        print("Drafts remaining:", [(c["web_id"], c["settings"]["title"]) for c in left.get("campaigns", [])])
    return 0


if __name__ == "__main__":
    sys.exit(main())
