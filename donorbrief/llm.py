"""LLM steps: triage (score + summarize every item) and compose (write the brief).

Provider-specific code lives in one class per provider. To add another provider,
implement `structured()` with the same signature and register it in PROVIDERS.
"""
from __future__ import annotations

import json
from pathlib import Path
import logging
from typing import Literal, Optional, Type, TypeVar

import yaml
from pydantic import BaseModel, Field

from .sources import Item

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

TRIAGE_BATCH = 25


# ---------- Output schemas ----------

class ItemAssessment(BaseModel):
    id: str
    relevance: int = Field(description="0-10. How much IoG's audience needs to know this, this week.")
    section: str = Field(description="One of the section ids, or 'none' if it fits nowhere.")
    summary: str = Field(description="2-3 plain-language sentences on what happened. Facts only, from the item text.")
    why_it_matters: str = Field(description="One sentence on why it matters for donors, nonprofits, or generosity research.")
    topics: list[str] = Field(description="1-3 topic ids from the topic list that fit this item best. Empty only if section is 'none'.")
    emerging_topic: str = Field(description="If no listed topic fits a relevant item well, a short name for the theme it represents; otherwise empty.")


class TriageResult(BaseModel):
    items: list[ItemAssessment]


class BriefEntry(BaseModel):
    id: str = Field(description="The id of the source item this entry is based on.")
    headline: str
    body: str = Field(description="Stories behind a takeaway: 2-3 sentences. All others: one sentence.")
    follow_up: bool = Field(default=False, description="True only if this continues a story from a previous brief and adds real news.")


class BriefSection(BaseModel):
    section_id: str
    entries: list[BriefEntry]


class Takeaway(BaseModel):
    lead: str = Field(description="The point in one short, bold-able line, under 90 characters. A claim, not a topic.")
    detail: str = Field(description="One sentence on why it matters to IoG's readers.")
    item_ids: list[str] = Field(description="Ids of this week's news entries that back this takeaway (1-3).")


class Trend(BaseModel):
    title: str = Field(description="A short name for the pattern, under 60 characters.")
    body: str = Field(description="2-3 sentences: what is building across these stories and why it matters to IoG's readers.")
    item_ids: list[str] = Field(description="Ids of the stories that show the pattern: at least 3, from at least 2 different weeks.")


class Brief(BaseModel):
    subject: str = Field(description="Email subject line, under 70 characters.")
    takeaways: list[Takeaway] = Field(description="3-5 key takeaways from this week, most important first.")
    trends: list[Trend] = Field(description="0-3 patterns building across weeks.")
    sections: list[BriefSection] = Field(description="All of this week's news worth knowing, grouped by section.")


# ---------- Providers ----------

class AnthropicProvider:
    def __init__(self) -> None:
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY

    def structured(self, model: str, system: str, prompt: str, schema: Type[T], effort: str) -> T:
        kwargs = dict(
            model=model,
            max_tokens=16000,
            system=system,
            messages=[{"role": "user", "content": prompt}],
            output_format=schema,
            output_config={"effort": effort},
        )
        if model.startswith("claude-opus") or model.startswith("claude-fable"):
            # Server-side fallback: if a safety classifier declines, the API retries on a suitable model.
            resp = self.client.beta.messages.parse(**kwargs, betas=["server-side-fallback-2026-07-01"], fallbacks="default")
        else:
            resp = self.client.messages.parse(**kwargs)
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"{model} declined the request: {getattr(resp, 'stop_details', None)}")
        if resp.stop_reason == "max_tokens":
            raise RuntimeError(f"{model} hit max_tokens; output truncated")
        log.info("%s: %s in / %s out tokens", model, resp.usage.input_tokens, resp.usage.output_tokens)
        return resp.parsed_output


PROVIDERS = {"anthropic": AnthropicProvider}


def get_provider(name: str):
    if name not in PROVIDERS:
        raise ValueError(f"Unknown LLM provider {name!r}; available: {', '.join(PROVIDERS)}")
    return PROVIDERS[name]()


# ---------- Prompts ----------

AUDIENCE = (
    "You work for the Institute on Generosity (IoG), which studies and promotes generosity. "
    "Its weekly briefing goes to IoG staff, board members, funders, and people across philanthropy "
    "who want to know what changed this week in giving, nonprofits, and generosity research."
)


def _sections_text(sections: list[dict]) -> str:
    return "\n".join(f"- {s['id']}: {s['title']}. {s['description']}" for s in sections)


def load_topics(path: Path) -> list[dict]:
    return yaml.safe_load(path.read_text())["topics"] if path.exists() else []


def triage(provider, model: str, items: list[Item], sections: list[dict],
           topics: Optional[list[dict]] = None) -> list[ItemAssessment]:
    topics = topics or []
    topic_text = "\n".join(f"- {t['id']}: {t['description']}" for t in topics)
    system = (
        f"{AUDIENCE}\n\nYou are screening this week's raw items. For every item, judge relevance to that "
        "audience, assign a section, and summarize it using only what the item text says. Don't add facts. "
        "Score low (0-3) for items that only mention a keyword in passing (organ donors, unrelated "
        "'foundation' uses), event listings, ads, and job posts. Impact investing and volunteering are "
        "secondary interests: score them in the middle (4-6) unless the story is directly about "
        "philanthropy, foundations, or generosity or is unusually significant. Routine corporate ESG "
        "news is not relevant. Practical how-to advice (fundraising tactics, donor tax-planning tips, "
        "year-end giving checklists) is low interest: score it 2-4 unless it reports new data or a "
        "significant change. IoG especially follows generosity as a unifying, nonpartisan force; "
        "strategic giving; democratizing philanthropy; women in philanthropy; philanthropy education; "
        "and research on why people give.\n\nSections:\n"
        f"{_sections_text(sections)}\n- none: not relevant"
    )
    if topics:
        system += (
            "\n\nTag every relevant item with 1-3 topic ids from this list, best fit first. If a relevant "
            "item represents a theme none of these capture well, also give that theme a short name in "
            f"emerging_topic.\n\nTopics:\n{topic_text}"
        )
    valid_topics = {t["id"] for t in topics}
    results: list[ItemAssessment] = []
    for start in range(0, len(items), TRIAGE_BATCH):
        batch = items[start:start + TRIAGE_BATCH]
        payload = [
            {"id": i.id, "source": i.source_name, "title": i.title, "date": i.published.date().isoformat() if i.published else None, "text": i.summary}
            for i in batch
        ]
        prompt = f"Assess every one of these {len(batch)} items. Return one entry per id.\n\n{json.dumps(payload, indent=1)}"
        out = provider.structured(model, system, prompt, TriageResult, effort="low")
        valid = {i.id for i in batch}
        for a in out.items:
            if a.id not in valid:
                continue
            a.topics = [t for t in dict.fromkeys(a.topics) if t in valid_topics][:3]
            a.emerging_topic = a.emerging_topic.strip()
            results.append(a)
    return results


def compose(provider, model: str, picked: list[tuple[Item, ItemAssessment]], sections: list[dict], week_of: str,
            past_weeks: Optional[list[dict]] = None) -> Brief:
    """Write the brief. past_weeks (newest first, from history.load_recent) feeds trends and follow-ups."""
    past_weeks = past_weeks or []
    system = (
        f"{AUDIENCE}\n\nYou are the editor writing this week's DonorBrief. Write in a clear, warm, "
        "plain-language voice for busy readers; no hype, no jargon. IoG is nonpartisan and does not tell people "
        "what, where, or how much to give: report political, policy, and cause-related stories neutrally, "
        "describe what happened and who is affected, and don't take sides or endorse causes. "
        "Use only facts from the items provided, "
        "and cite the id of the item each entry or trend is based on. Merge items that cover the same story "
        "into one entry citing the best source.\n\n"
        "The brief has two halves. The top half gives busy readers the point in 30 seconds; the bottom "
        "half is the news, for anyone who wants detail.\n"
        "1. Key takeaways: 3-5 numbered takeaways, most important first. Each is a short bold claim (what "
        "changed or what it means, not a topic label) plus one sentence on why it matters to IoG's readers, "
        "backed by 1-3 ids of this week's news entries. A takeaway can connect several stories.\n"
        "2. Trends to watch: 0-3 patterns that are building across weeks, a theme that shows up in several "
        "separate stories over at least two different weeks (this week counts). Cite at least 3 supporting "
        "item ids from at least 2 weeks, using this week's items and the earlier weeks listed. A single big "
        "story is not a trend, and neither is a topic that is always in the news. Name what is changing. "
        "If nothing qualifies, return no trends. Don't repeat a takeaway as a trend.\n"
        "3. The news: every story from this week worth knowing, grouped by section, each story once. Stories "
        "behind a takeaway get 2-3 sentences; all others get one sentence. Skip sections with nothing in them.\n\n"
        "Stories marked as already in a previous brief: include them only if there is genuinely new "
        "information, and then set follow_up to true.\n\n"
        f"Sections, in order:\n{_sections_text(sections)}"
    )
    payload = [
        {"id": i.id, "source": i.source_name, "title": i.title, "date": i.published.date().isoformat() if i.published else None,
         "section": a.section, "relevance": a.relevance, "summary": a.summary, "why_it_matters": a.why_it_matters}
        for i, a in picked
    ]
    earlier = [
        {"week": w["week"], "items": [
            {"id": i["id"], "source": i["source"], "title": i["title"], "section": i["section"], "summary": i["summary"],
             **({"already_in_a_previous_brief": True} if i.get("in_brief") else {})}
            for i in w["items"] if i["relevance"] >= 6 or i.get("in_brief")]}
        for w in past_weeks
    ]
    prompt = f"Week of {week_of}. This week's screened items:\n\n{json.dumps(payload, indent=1)}"
    if earlier:
        prompt += f"\n\nEarlier weeks, newest first (for trends and follow-ups only):\n\n{json.dumps(earlier, indent=1)}"
    return provider.structured(model, system, prompt, Brief, effort="medium")


def pick_top(items: list[Item], assessments: list[ItemAssessment], min_relevance: int, limit: int) -> list[tuple[Item, ItemAssessment]]:
    by_id = {i.id: i for i in items}
    keep = [(by_id[a.id], a) for a in assessments if a.section != "none" and a.relevance >= min_relevance and a.id in by_id]
    keep.sort(key=lambda p: p[1].relevance, reverse=True)
    return keep[:limit]


def find_item(items: list[Item], item_id: str) -> Optional[Item]:
    return next((i for i in items if i.id == item_id), None)
