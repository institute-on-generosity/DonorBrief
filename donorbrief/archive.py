"""Build the public archive of sent briefs as a static site.

    python -m donorbrief.archive [--archive archive] [--out site]

Each sent brief lives in archive/<YYYY-MM-DD>/rendered.json (copied there when a brief is
sent). The site is plain HTML (an index plus one page per brief), so it can be hosted
anywhere: Vercel, GitHub Pages, or IoG's own website.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent


def load_briefs(archive: Path) -> list[dict]:
    briefs = []
    for f in sorted(archive.glob("*/rendered.json"), reverse=True):
        slug = f.parent.name
        date.fromisoformat(slug)  # folder names must be dates; fail loudly otherwise
        data = json.loads(f.read_text())
        data["slug"] = slug
        data["count"] = sum(len(s["entries"]) for s in data["sections"])
        briefs.append(data)
    return briefs


def build(archive: Path, out: Path) -> int:
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
    briefs = load_briefs(archive)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(env.get_template("archive_index.html.j2").render(briefs=briefs))
    page = env.get_template("archive_brief.html.j2")
    for i, b in enumerate(briefs):
        newer = briefs[i - 1] if i > 0 else None
        older = briefs[i + 1] if i + 1 < len(briefs) else None
        (out / b["slug"]).mkdir(exist_ok=True)
        (out / b["slug"] / "index.html").write_text(page.render(brief=b, newer=newer, older=older))
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
