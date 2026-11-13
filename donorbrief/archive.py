"""Build the public archive of sent briefs as a static site.

    python -m donorbrief.archive [--archive archive] [--out site]

Each sent brief lives in archive/<YYYY-MM-DD>/: email.html and meta.json, saved by the weekly run
from Mailchimp once staff send it (see donorbrief/sent.py), so the archive shows exactly what went
out. Folders holding a rendered.json instead are rendered with the archive's own page template. The site is plain HTML (an index plus one page per brief), so it can be hosted
anywhere: Vercel, GitHub Pages, or IoG's own website.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent


NAV = ('<div style="background:#000;padding:12px 16px;font-family:\'DM Sans\',Arial,sans-serif;font-size:13px;">'
       '<a href="../" style="color:#C7E263;text-decoration:none;font-weight:700;">&larr; All DonorBrief issues</a></div>')


def load_briefs(archive: Path) -> list[dict]:
    briefs = []
    for folder in sorted((p for p in archive.iterdir() if p.is_dir()), reverse=True) if archive.exists() else []:
        slug = folder.name
        date.fromisoformat(slug[:10])  # folder names start with the date; fail loudly otherwise
        if (folder / "meta.json").exists() and (folder / "email.html").exists():
            meta = json.loads((folder / "meta.json").read_text())
            sent_on = date.fromisoformat(meta["sent_at"][:10])
            briefs.append({"slug": slug, "subject": meta["subject"], "intro": meta.get("preview_text", ""),
                           "week_of": f"{sent_on:%B} {sent_on.day}, {sent_on.year}", "email": folder / "email.html",
                           "count": len(meta.get("stories", []))})
        elif (folder / "rendered.json").exists():
            data = json.loads((folder / "rendered.json").read_text())
            data["slug"] = slug
            data["count"] = sum(len(s["entries"]) for s in data["sections"])
            briefs.append(data)
    return briefs


def with_nav(email_html: str) -> str:
    """The sent email as a web page, with a link back to the archive index."""
    m = re.search(r"<body[^>]*>", email_html)
    return email_html[:m.end()] + NAV + email_html[m.end():] if m else NAV + email_html


def build(archive: Path, out: Path) -> int:
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
    briefs = load_briefs(archive)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(env.get_template("archive_index.html.j2").render(briefs=briefs))
    page = env.get_template("archive_brief.html.j2")
    for i, b in enumerate(briefs):
        (out / b["slug"]).mkdir(exist_ok=True)
        if "email" in b:
            html = with_nav(b["email"].read_text())
        else:
            newer = briefs[i - 1] if i > 0 else None
            older = briefs[i + 1] if i + 1 < len(briefs) else None
            html = page.render(brief=b, newer=newer, older=older)
        (out / b["slug"] / "index.html").write_text(html)
    return len(briefs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--archive", type=Path, default=ROOT / "archive")
    parser.add_argument("--out", type=Path, default=ROOT / "site")
    args = parser.parse_args()
    n = build(args.archive, args.out)
    print(f"Built {n} briefs into {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
