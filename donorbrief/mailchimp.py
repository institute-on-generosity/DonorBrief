"""Hand a finished draft to Mailchimp as a draft campaign for IoG's reviewer.

    python -m donorbrief.mailchimp drafts/2026-10-12

The weekly run calls this after writing the draft. It creates a regular campaign addressed
to the configured audience, sets its HTML, and emails a preview copy to the reviewers. It
never sends the campaign: a person reviews it in Mailchimp and presses Send there.

Needs MAILCHIMP_API_KEY (Mailchimp: Profile -> Extras -> API keys) and the `mailchimp`
settings in config/sources.yaml. Without them it does nothing.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

import httpx
import yaml
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("donorbrief.mailchimp")


class MailchimpError(Exception):
    pass


def server_prefix(key: str) -> str:
    """Mailchimp keys end in their data center, e.g. 'abc123-us21' -> 'us21'."""
    m = re.fullmatch(r"[0-9a-f]+-([a-z]+\d+)", key.strip())
    if not m:
        raise MailchimpError("MAILCHIMP_API_KEY doesn't look like a Mailchimp key (expected '<key>-us<N>').")
    return m.group(1)


def render_email(rendered: dict) -> str:
    """The brief's email HTML, plus the unsubscribe and address lines Mailchimp requires."""
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
    return env.get_template("brief.html.j2").render(
        brief=rendered, sections=rendered["sections"], week_of=rendered.get("week_of", ""), mailchimp=True)


def preview_text(intro: str, limit: int = 140) -> str:
    first = re.split(r"(?<=[.!?])\s", intro.strip(), maxsplit=1)[0]
    return first if len(first) <= limit else first[: limit - 1].rstrip() + "…"


def check_settings(cfg: dict) -> list[str]:
    missing = [k for k in ("audience_id", "from_name", "reply_to") if not str(cfg.get(k) or "").strip()]
    return [f"mailchimp.{k} is empty in config/sources.yaml" for k in missing]


def create_draft(rendered: dict, cfg: dict, key: str, client: httpx.Client) -> dict:
    """Create the draft campaign and send preview copies. Returns ids and the edit link."""
    dc = server_prefix(key)
    base = f"https://{dc}.api.mailchimp.com/3.0"
    auth = ("donorbrief", key)

    def call(method: str, path: str, body: dict | None = None) -> dict:
        try:
            r = client.request(method, base + path, json=body, auth=auth, timeout=30)
        except httpx.HTTPError as e:
            raise MailchimpError(f"{method} {path}: {type(e).__name__}") from None
        if r.status_code >= 400:
            try:
                err = r.json()
                detail = err.get("detail") or err.get("title") or r.text
                if err.get("errors"):
                    detail += " " + "; ".join(f"{x.get('field')}: {x.get('message')}" for x in err["errors"])
            except ValueError:
                detail = r.text[:200]
            raise MailchimpError(f"{method} {path} -> {r.status_code}: {detail}")
        return r.json() if r.content else {}

    title = f"DonorBrief: {rendered.get('week_of') or rendered['subject']}"
    campaign = call("POST", "/campaigns", {
        "type": "regular",
        "recipients": {"list_id": cfg["audience_id"]},
        "settings": {
            "subject_line": rendered["subject"][:150],
            "preview_text": preview_text(rendered["intro"]),
            "title": title,
            "from_name": cfg["from_name"],
            "reply_to": cfg["reply_to"],
        },
    })
    cid, web_id = campaign["id"], campaign.get("web_id")
    call("PUT", f"/campaigns/{cid}/content", {"html": render_email(rendered)})

    tests = [e for e in (cfg.get("test_emails") or []) if e]
    if tests:
        call("POST", f"/campaigns/{cid}/actions/test", {"test_emails": tests, "send_type": "html"})

    return {
        "status": "draft_created",
        "campaign_id": cid,
        "title": title,
        "edit_url": f"https://{dc}.admin.mailchimp.com/campaigns/edit?id={web_id}" if web_id else None,
        "preview_sent_to": len(tests),
    }


def deliver(out_dir: Path, config: dict, client: httpx.Client | None = None) -> dict:
    """Create the Mailchimp draft for one draft folder; writes delivery.json there.

    Returns {"status": "skipped" | "draft_created" | "failed", ...}. Skips quietly when
    Mailchimp isn't set up yet, so the weekly run works before IoG connects it.
    """
    cfg = config.get("mailchimp") or {}
    key = os.environ.get("MAILCHIMP_API_KEY", "").strip()
    if not cfg.get("enabled", True):
        result = {"status": "skipped", "reason": "mailchimp.enabled is false"}
    elif not key:
        result = {"status": "skipped", "reason": "MAILCHIMP_API_KEY is not set"}
    elif problems := check_settings(cfg):
        result = {"status": "failed", "error": "; ".join(problems)}
    else:
        rendered = json.loads((out_dir / "rendered.json").read_text())
        own = client is None
        client = client or httpx.Client()
        try:
            result = create_draft(rendered, cfg, key, client)
        except MailchimpError as e:
            result = {"status": "failed", "error": str(e).replace(key, "REDACTED")}
        finally:
            if own:
                client.close()
    (out_dir / "delivery.json").write_text(json.dumps(result, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("draft", type=Path, help="A draft folder containing rendered.json")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "sources.yaml")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    result = deliver(args.draft, yaml.safe_load(args.config.read_text()))
    print(json.dumps(result, indent=2))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    sys.exit(main())
