"""Run the weekly pipeline: python -m donorbrief [--dry-run]"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader

from . import llm
from .sources import collect

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("donorbrief")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build this week's DonorBrief draft.")
    parser.add_argument("--config", default=ROOT / "config" / "sources.yaml", type=Path)
    parser.add_argument("--out", default=ROOT / "drafts", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="Fetch sources only; no LLM calls.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    config = yaml.safe_load(args.config.read_text())
    now = datetime.now(timezone.utc)
    week_of = now.strftime("%B %-d, %Y")
    out_dir = args.out / now.strftime("%Y-%m-%d")
    out_dir.mkdir(parents=True, exist_ok=True)

    log.info("Fetching sources...")
    items, results = collect(config, now)
    failed = [r for r in results if not r.ok]
    log.info("%d items from %d/%d sources", len(items), len(results) - len(failed), len(results))
    (out_dir / "sources.json").write_text(json.dumps(
        {"sources": [asdict(r) for r in results],
         "items": [{**asdict(i), "published": i.published.isoformat() if i.published else None} for i in items]},
        indent=2))

    if args.dry_run:
        for i in items:
            log.info("  [%s] %s", i.source_id, i.title[:100])
        return 0
    if not items:
        log.error("No items fetched; nothing to write.")
        return 1

    cfg = config["llm"]
    sections = config["sections"]
    provider = llm.get_provider(cfg["provider"])

    log.info("Triage with %s...", cfg["triage_model"])
    assessments = llm.triage(provider, cfg["triage_model"], items, sections)
    picked = llm.pick_top(items, assessments, cfg["min_relevance"], cfg["max_items_in_brief"])
    log.info("%d of %d items kept", len(picked), len(items))
    if not picked:
        log.error("No items passed triage.")
        return 1

    log.info("Writing brief with %s...", cfg["editor_model"])
    brief = llm.compose(provider, cfg["editor_model"], picked, sections, week_of)

    rendered_sections = render_sections(brief, sections, items)
    write_outputs(out_dir, brief, rendered_sections, week_of, failed)
    log.info("Draft written to %s", out_dir)
    return 0


def render_sections(brief: llm.Brief, sections: list[dict], items) -> list[dict]:
    """Attach real URLs and source names by item id, so links never come from the model."""
    titles = {s["id"]: s["title"] for s in sections}
    order = [s["id"] for s in sections]
    out = []
    for sec in sorted(brief.sections, key=lambda s: order.index(s.section_id) if s.section_id in order else 99):
        entries = []
        for e in sec.entries:
            item = llm.find_item(items, e.id)
            if item is None:
                log.warning("Dropping entry %r: unknown item id %s", e.headline, e.id)
                continue
            entries.append({"headline": e.headline, "body": e.body, "url": item.url, "source": item.source_name})
        if entries:
            out.append({"title": titles.get(sec.section_id, sec.section_id), "entries": entries})
    return out


def write_outputs(out_dir: Path, brief: llm.Brief, sections: list[dict], week_of: str, failed) -> None:
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
    (out_dir / "brief.html").write_text(env.get_template("brief.html.j2").render(brief=brief, sections=sections, week_of=week_of))

    md = [f"# {brief.subject}", f"_Week of {week_of}_", "", brief.intro, ""]
    for sec in sections:
        md += [f"## {sec['title']}", ""]
        for e in sec["entries"]:
            md += [f"**[{e['headline']}]({e['url']})**  ", f"{e['body']} _({e['source']})_", ""]
    if failed:
        md += ["---", "**Sources that failed this week (not shown to readers):**"]
        md += [f"- {r.source_id}: {r.error}" for r in failed]
    (out_dir / "brief.md").write_text("\n".join(md) + "\n")
    (out_dir / "brief.json").write_text(brief.model_dump_json(indent=2))


if __name__ == "__main__":
    sys.exit(main())
