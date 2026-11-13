"""Run the weekly pipeline: python -m donorbrief [--dry-run]"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml
from jinja2 import Environment, FileSystemLoader

from . import history, llm, mailchimp
from .sources import collect

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("donorbrief")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build this week's DonorBrief draft.")
    parser.add_argument("--config", default=ROOT / "config" / "sources.yaml", type=Path)
    parser.add_argument("--out", default=ROOT / "drafts", type=Path)
    parser.add_argument("--history", default=history.HISTORY, type=Path, help="Week-to-week memory (history/).")
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
    topics = llm.load_topics(args.config.parent / "topics.yaml")
    assessments = llm.triage(provider, cfg["triage_model"], items, sections, topics)
    picked = llm.pick_top(items, assessments, cfg["min_relevance"], cfg["max_items_in_brief"])
    log.info("%d of %d items kept", len(picked), len(items))
    if not picked:
        log.error("No items passed triage.")
        return 1

    week = history.week_label(now.date())
    past_weeks = history.load_recent(args.history, before=week, weeks=cfg.get("trend_weeks", 8))
    log.info("Writing brief with %s (%d earlier weeks for trends)...", cfg["editor_model"], len(past_weeks))
    brief = llm.compose(provider, cfg["editor_model"], picked, sections, week_of, past_weeks)

    rendered = render(brief, sections, items, past_weeks, week, week_of)
    write_outputs(out_dir, brief, rendered, failed)
    in_brief = {e["id"] for s in rendered["sections"] for e in s["entries"]}
    history.record(args.history, week, items, assessments, in_brief)
    log.info("Draft written to %s (%d takeaways, %d trends, %d stories)", out_dir, len(rendered["takeaways"]),
             len(rendered["trends"]), len(in_brief))

    delivery = mailchimp.deliver(out_dir, config)
    if delivery["status"] == "draft_created":
        log.info("Mailchimp draft created: %s", delivery["edit_url"] or delivery["campaign_id"])
    elif delivery["status"] == "skipped":
        log.info("Mailchimp skipped: %s", delivery["reason"])
    else:
        log.error("Mailchimp draft failed: %s", delivery["error"])
        return 1  # fail the run so GitHub emails the owner; the draft is still saved
    return 0


MIN_TREND_STORIES = 3
MIN_TREND_WEEKS = 2


def render(brief: llm.Brief, sections: list[dict], items, past_weeks: list[dict], week: str, week_of: str) -> dict:
    """Everything a reader sees, with links filled in from source data, never from the model.

    Entries citing unknown ids are dropped. Trends must be backed by enough real stories across
    enough weeks, and their counts are computed here rather than taken from the model.
    """
    titles = {s["id"]: s for s in sections}
    order = [s["id"] for s in sections]
    current = {i.id: i for i in items}

    # The news: each story once, in section order, with links from source data.
    rendered_sections, placed = [], set()
    for sec in sorted(brief.sections, key=lambda s: order.index(s.section_id) if s.section_id in order else 99):
        entries = []
        for e in sec.entries:
            item = current.get(e.id)
            if item is None:
                log.warning("Dropping entry %r: unknown item id %s", e.headline, e.id)
                continue
            if e.id in placed:
                continue
            placed.add(e.id)
            entries.append({"id": e.id, "anchor": f"story-{len(placed)}", "headline": e.headline, "body": e.body,
                            "url": item.url, "source": item.source_name, "follow_up": e.follow_up, "takeaways": []})
        if entries:
            meta = titles.get(sec.section_id, {})
            rendered_sections.append({"id": sec.section_id, "title": meta.get("title", sec.section_id),
                                      "description": meta.get("description", ""), "entries": entries})
    news = {e["id"]: e for sec in rendered_sections for e in sec["entries"]}

    # Takeaways must point at stories that are actually in the news below.
    takeaways = []
    for t in brief.takeaways:
        stories = [news[i] for i in dict.fromkeys(t.item_ids) if i in news]
        if not stories:
            log.warning("Dropping takeaway %r: none of its stories are in the brief", t.lead)
            continue
        n = len(takeaways) + 1
        for st in stories:
            st["takeaways"].append(n)
        takeaways.append({"n": n, "lead": t.lead, "detail": t.detail,
                          "stories": [{"anchor": st["anchor"], "headline": st["headline"], "url": st["url"],
                                       "source": st["source"]} for st in stories]})

    # Every story the editor could cite for a trend: this week plus earlier weeks.
    known = {i.id: {"title": i.title, "url": i.url, "source": i.source_name, "week": week} for i in items}
    for w in past_weeks:
        for i in w["items"]:
            known.setdefault(i["id"], {"title": i["title"], "url": i["url"], "source": i["source"], "week": w["week"]})
    trends = []
    for t in brief.trends:
        stories = [known[i] for i in dict.fromkeys(t.item_ids) if i in known]
        weeks = {s["week"] for s in stories}
        if len(stories) < MIN_TREND_STORIES or len(weeks) < MIN_TREND_WEEKS:
            log.warning("Dropping trend %r: %d stories over %d weeks", t.title, len(stories), len(weeks))
            continue
        stories.sort(key=lambda s: s["week"], reverse=True)
        trends.append({"title": t.title, "body": t.body, "stories": len(stories), "weeks": len(weeks),
                       "since": min(weeks), "evidence": stories[:4]})

    # `intro` (the takeaways as plain sentences) feeds the email preview text and the archive index.
    intro = " ".join(f"{t['lead'].rstrip('.')}." for t in takeaways)
    return {"subject": brief.subject, "intro": intro, "week_of": week_of,
            "takeaways": takeaways, "trends": trends, "sections": rendered_sections}


def write_outputs(out_dir: Path, brief: llm.Brief, rendered: dict, failed) -> None:
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=True)
    (out_dir / "brief.html").write_text(env.get_template("brief.html.j2").render(
        brief=rendered, sections=rendered["sections"], week_of=rendered["week_of"]))
    (out_dir / "brief.md").write_text(to_markdown(rendered, failed))
    (out_dir / "brief.json").write_text(brief.model_dump_json(indent=2))
    # The input for Mailchimp, the archive, and the edit page.
    (out_dir / "rendered.json").write_text(json.dumps(rendered, indent=2))


def to_markdown(r: dict, failed=()) -> str:
    md = [f"# {r['subject']}", f"_Week of {r['week_of']}_", ""]
    if r["takeaways"]:
        md += ["## Key takeaways", ""]
        for t in r["takeaways"]:
            refs = ", ".join(f"[{s['source']}]({s['url']})" for s in t["stories"])
            md += [f"{t['n']}. **{t['lead']}** {t['detail']} ({refs})"]
        md += [""]
    if r["trends"]:
        md += ["## What's building", ""]
        for t in r["trends"]:
            md += [f"**{t['title']}** _({t['stories']} stories over {t['weeks']} weeks)_  ", t["body"], ""]
            md += [f"- [{s['title']}]({s['url']}) _({s['source']}, week of {s['week']})_" for s in t["evidence"]] + [""]
    if r["sections"]:
        md += ["---", "", "# The news", ""]
    for sec in r["sections"]:
        md += [f"## {sec['title']}", ""]
        for e in sec["entries"]:
            tags = ("Update: " if e["follow_up"] else "") + "".join(f"[Takeaway {n}] " for n in e["takeaways"])
            md += [f"- {tags}**[{e['headline']}]({e['url']})** {e['body']} _({e['source']})_"]
        md += [""]
    if failed:
        md += ["---", "**Sources that failed this week (not shown to readers):**"]
        md += [f"- {f.source_id}: {f.error}" for f in failed]
    return "\n".join(md) + "\n"


if __name__ == "__main__":
    sys.exit(main())
