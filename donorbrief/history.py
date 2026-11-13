"""Week-to-week memory: what each run saw, how it scored it, and what went in the brief.

history/<YYYY-MM-DD>.json is written by every full run and committed by the weekly workflow.
The editor reads recent weeks to spot trends and to avoid repeating stories.

    python -m donorbrief.history backfill FILE...   # triage old sources.json files into history/
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml

from . import llm
from .sources import Item

ROOT = Path(__file__).resolve().parent.parent
HISTORY = ROOT / "history"
KEEP_RELEVANCE = 4   # items scored below this are noise; don't keep them
log = logging.getLogger(__name__)


def week_label(day: date) -> str:
    """History is kept per week, labeled by its Monday, so a rerun replaces that week's file."""
    return (day - timedelta(days=day.weekday())).isoformat()


def record(history_dir: Path, week: str, items: list[Item], assessments: list[llm.ItemAssessment],
           in_brief: Optional[set[str]] = None) -> Path:
    """Save this week's screened items; in_brief is the ids that made the draft.

    The repo is public, so only source facts are kept, never the drafted wording of the brief.
    """
    in_brief = in_brief or set()
    by_id = {i.id: i for i in items}
    kept = []
    for a in sorted(assessments, key=lambda a: -a.relevance):
        item = by_id.get(a.id)
        if item is None or (a.relevance < KEEP_RELEVANCE and a.id not in in_brief):
            continue
        kept.append({
            "id": a.id, "title": item.title, "url": item.url, "source": item.source_name,
            "published": item.published.date().isoformat() if item.published else None,
            "relevance": a.relevance, "section": a.section, "summary": a.summary,
            "topics": a.topics, "emerging_topic": a.emerging_topic,
            "in_brief": a.id in in_brief,
        })
    history_dir.mkdir(parents=True, exist_ok=True)
    path = history_dir / f"{week}.json"
    path.write_text(json.dumps({"week": week, "items": kept}, indent=1))
    return path


def load_recent(history_dir: Path, before: str, weeks: int = 8) -> list[dict]:
    """The `weeks` most recent weeks strictly before `before`, newest first.

    An item that appears in several weeks (feeds overlap) is kept only in the earliest one.
    """
    files = sorted((p for p in history_dir.glob("*.json") if p.stem < before), reverse=True)[:weeks]
    loaded = [json.loads(p.read_text()) for p in files]
    in_brief = {i["id"] for w in loaded for i in w["items"] if i.get("in_brief")}
    seen: set[str] = set()
    for week in reversed(loaded):  # oldest first, so the earliest sighting wins
        week["items"] = [{**i, "in_brief": i["id"] in in_brief} for i in week["items"] if i["id"] not in seen]
        seen.update(i["id"] for i in week["items"])
    return loaded


def covered_ids(past_weeks: list[dict]) -> set[str]:
    """Ids of stories that already ran in an earlier brief."""
    return {i["id"] for w in past_weeks for i in w["items"] if i.get("in_brief")}


def backfill(files: list[Path], config: dict, history_dir: Path, topics: list[dict]) -> None:
    cfg = config["llm"]
    provider = llm.get_provider(cfg["provider"])
    for f in sorted(files):
        week = week_label(date.fromisoformat(f.parent.name))  # expects <YYYY-MM-DD>/sources.json
        data = json.loads(f.read_text())
        items = [Item(d["source_id"], d["source_name"], d["title"], d["url"],
                      datetime.fromisoformat(d["published"]) if d.get("published") else None, d["summary"])
                 for d in data["items"]]
        in_brief = set()
        brief = f.parent / "rendered.json"
        if brief.exists():
            urls = {e["url"] for sec in json.loads(brief.read_text()).get("sections", []) for e in sec["entries"]}
            in_brief = {i.id for i in items if i.url in urls}
        assessments = llm.triage(provider, cfg["triage_model"], items, config["sections"], topics)
        path = record(history_dir, week, items, assessments, in_brief)
        log.info("%s: %d items -> %s", week, len(items), path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)


def retag(history_dir: Path, config: dict, topics: list[dict], everything: bool = False) -> None:
    """Add topic tags to history items that lack them, or to every item (after the topic list changes).

    Keeps each item's original score and summary; only the tags change.
    """
    cfg = config["llm"]
    provider = llm.get_provider(cfg["provider"])
    for path in sorted(history_dir.glob("*.json")):
        week = json.loads(path.read_text())
        todo = week["items"] if everything else [i for i in week["items"] if "topics" not in i]
        if not todo:
            continue
        items = [Item("history", i["source"], i["title"], i["url"], None, i["summary"]) for i in todo]
        tags = {a.id: a for a in llm.triage(provider, cfg["triage_model"], items, config["sections"], topics)}
        for i in todo:
            a = tags.get(i["id"])
            i["topics"] = a.topics if a else []
            i["emerging_topic"] = a.emerging_topic if a else ""
        path.write_text(json.dumps(week, indent=1))
        log.info("%s: tagged %d items", week["week"], len(todo))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="Triage old drafts/<date>/sources.json files into history/")
    b.add_argument("files", nargs="+", type=Path)
    b.add_argument("--config", type=Path, default=ROOT / "config" / "sources.yaml")
    r = sub.add_parser("retag", help="Add topic tags to history weeks that don't have them")
    r.add_argument("--all", action="store_true", help="Re-tag every item, e.g. after editing config/topics.yaml")
    r.add_argument("--config", type=Path, default=ROOT / "config" / "sources.yaml")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    config = yaml.safe_load(args.config.read_text())
    topics = llm.load_topics(args.config.parent / "topics.yaml")
    if args.cmd == "backfill":
        backfill(args.files, config, HISTORY, topics)
    else:
        retag(HISTORY, config, topics, everything=args.all)
    return 0


if __name__ == "__main__":
    sys.exit(main())
