"""Score the triage step against hand labels.

    python -m donorbrief.evaluate                 # run triage on the eval set, then score it
    python -m donorbrief.evaluate --replay FILE   # re-score a saved run (no API calls)

Labels live in eval/labels-*.yaml next to the frozen items they describe (eval/items-*.json).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import llm
from .sources import Item

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "eval"


def load_items(path: Path) -> list[Item]:
    items = []
    for d in json.loads(path.read_text())["items"]:
        published = datetime.fromisoformat(d["published"]) if d["published"] else None
        item = Item(d["source_id"], d["source_name"], d["title"], d["url"], published, d["summary"])
        assert item.id == d["id"], f"item id changed for {d['url']}"
        items.append(item)
    return items


def load_labels(path: Path, items: list[Item]) -> tuple[dict[str, list[str]], set[str]]:
    data = yaml.safe_load(path.read_text())
    include = {k: v for k, v in (data.get("include") or {}).items()}
    maybe = set(data.get("maybe") or [])
    known = {i.id for i in items}
    unknown = (set(include) | maybe) - known
    if unknown:
        raise SystemExit(f"Labels reference unknown item ids: {sorted(unknown)}")
    return include, maybe


def score(items: list[Item], assessments: list[llm.ItemAssessment], include: dict[str, list[str]],
          maybe: set[str], min_relevance: int, limit: int) -> dict:
    by_id = {a.id: a for a in assessments}
    title = {i.id: i.title for i in items}
    kept = {a.id for a in assessments if a.section != "none" and a.relevance >= min_relevance}
    exclude = {i.id for i in items} - set(include) - maybe

    missed = [i for i in include if i not in kept]
    wrong_section = [i for i in include if i in kept and by_id[i].section not in include[i]]
    leaked = sorted((i for i in exclude if i in kept), key=lambda i: -by_id[i].relevance)
    unscored = [i.id for i in items if i.id not in by_id]

    top = llm.pick_top(items, assessments, min_relevance, limit)
    top_ids = [i.id for i, _ in top]
    return {
        "items": len(items),
        "assessed": len(by_id),
        "include_recall": round(1 - len(missed) / len(include), 3) if include else None,
        "section_accuracy": round(1 - len(wrong_section) / max(1, len(include) - len(missed)), 3),
        "exclude_leak_rate": round(len(leaked) / len(exclude), 3) if exclude else None,
        "top": {
            "size": len(top_ids),
            "include": sum(i in include for i in top_ids),
            "maybe": sum(i in maybe for i in top_ids),
            "exclude": sum(i in exclude for i in top_ids),
        },
        "missed": [f"{i} (rel {by_id[i].relevance if i in by_id else '-'}, {by_id[i].section if i in by_id else '-'}) {title[i]}" for i in missed],
        "wrong_section": [f"{i} got {by_id[i].section}, want {include[i]}: {title[i]}" for i in wrong_section],
        "leaked": [f"{i} (rel {by_id[i].relevance}, {by_id[i].section}) {title[i]}" for i in leaked],
        "unscored": unscored,
    }


def report(result: dict) -> str:
    t = result["top"]
    lines = [
        f"Items: {result['items']} (assessed {result['assessed']})",
        f"Must-include items kept:    {result['include_recall']:.0%}",
        f"  ...in an acceptable section: {result['section_accuracy']:.0%}",
        f"Should-exclude items kept:  {result['exclude_leak_rate']:.0%}",
        f"Top {t['size']} sent to the editor: {t['include']} include, {t['maybe']} maybe, {t['exclude']} exclude",
    ]
    for key, heading in [("missed", "Missed must-include items"), ("wrong_section", "Wrong section"),
                         ("leaked", "Kept but should be excluded"), ("unscored", "Not returned by the model")]:
        if result[key]:
            lines += ["", f"{heading}:"] + [f"  - {x}" for x in result[key]]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--items", type=Path, default=EVAL / "items-2026-10-12.json")
    parser.add_argument("--labels", type=Path, default=EVAL / "labels-2026-10-12.yaml")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "sources.yaml")
    parser.add_argument("--replay", type=Path, help="Score a saved run instead of calling the model.")
    args = parser.parse_args()

    config = yaml.safe_load(args.config.read_text())
    cfg = config["llm"]
    items = load_items(args.items)
    include, maybe = load_labels(args.labels, items)

    if args.replay:
        saved = json.loads(args.replay.read_text())
        assessments = [llm.ItemAssessment(**a) for a in saved["assessments"]]
    else:
        provider = llm.get_provider(cfg["provider"])
        assessments = llm.triage(provider, cfg["triage_model"], items, config["sections"])
        runs = EVAL / "runs"
        runs.mkdir(exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        out = runs / f"{stamp}-{cfg['triage_model']}.json"
        out.write_text(json.dumps({"model": cfg["triage_model"], "items": args.items.name,
                                   "assessments": [a.model_dump() for a in assessments]}, indent=1))
        print(f"Saved run to {out.relative_to(ROOT)}\n")

    result = score(items, assessments, include, maybe, cfg["min_relevance"], cfg["max_items_in_brief"])
    print(report(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
