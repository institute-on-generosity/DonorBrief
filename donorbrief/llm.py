"""LLM steps: triage (score + summarize every item) and compose (write the brief).

Provider-specific code lives in one class per provider. To add another provider,
implement `structured()` with the same signature and register it in PROVIDERS.
"""
from __future__ import annotations

import json
import logging
from typing import Literal, Optional, Type, TypeVar

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


class TriageResult(BaseModel):
    items: list[ItemAssessment]


class BriefEntry(BaseModel):
    id: str = Field(description="The id of the source item this entry is based on.")
    headline: str
    body: str = Field(description="2-4 sentences: what happened and why it matters.")


class BriefSection(BaseModel):
    section_id: str
    entries: list[BriefEntry]


class Brief(BaseModel):
    subject: str = Field(description="Email subject line, under 70 characters.")
    intro: str = Field(description="2-3 sentence opening naming the week's most important developments.")
    sections: list[BriefSection]


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


def triage(provider, model: str, items: list[Item], sections: list[dict]) -> list[ItemAssessment]:
    system = (
        f"{AUDIENCE}\n\nYou are screening this week's raw items. For every item, judge relevance to that "
        "audience, assign a section, and summarize it using only what the item text says. Don't add facts. "
        "Score low (0-3) for items that only mention a keyword in passing (organ donors, unrelated "
        "'foundation' uses), event listings, ads, and job posts.\n\nSections:\n"
        f"{_sections_text(sections)}\n- none: not relevant"
    )
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
        results.extend(a for a in out.items if a.id in valid)
    return results


def compose(provider, model: str, picked: list[tuple[Item, ItemAssessment]], sections: list[dict], week_of: str) -> Brief:
    system = (
        f"{AUDIENCE}\n\nYou are the editor writing this week's DonorBrief. Write in a clear, warm, "
        "plain-language voice for busy readers; no hype, no jargon. Use only facts from the items provided. "
        "Every entry must cite the id of the item it is based on. Skip a section if no items fit it. "
        "Merge items that cover the same story into one entry citing the best source.\n\nSections, in order:\n"
        f"{_sections_text(sections)}"
    )
    payload = [
        {"id": i.id, "source": i.source_name, "title": i.title, "date": i.published.date().isoformat() if i.published else None,
         "section": a.section, "relevance": a.relevance, "summary": a.summary, "why_it_matters": a.why_it_matters}
        for i, a in picked
    ]
    prompt = f"Week of {week_of}. Write the brief from these screened items:\n\n{json.dumps(payload, indent=1)}"
    return provider.structured(model, system, prompt, Brief, effort="medium")


def pick_top(items: list[Item], assessments: list[ItemAssessment], min_relevance: int, limit: int) -> list[tuple[Item, ItemAssessment]]:
    by_id = {i.id: i for i in items}
    keep = [(by_id[a.id], a) for a in assessments if a.section != "none" and a.relevance >= min_relevance and a.id in by_id]
    keep.sort(key=lambda p: p[1].relevance, reverse=True)
    return keep[:limit]


def find_item(items: list[Item], item_id: str) -> Optional[Item]:
    return next((i for i in items if i.id == item_id), None)
