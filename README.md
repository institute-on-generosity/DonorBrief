# DonorBrief

Weekly AI-generated philanthropic intelligence briefing for the Institute on Generosity (IoG) and the field.

Every Monday a GitHub Action gathers the past week's items from public sources, has an LLM screen and summarize them, and writes a draft brief for IoG staff to review. Nothing is sent to readers without human review.

## How it works

1. **Collect.** Fetch every source in [`config/sources.yaml`](config/sources.yaml): RSS feeds, the Federal Register API (IRS rules and notices), and the Congress.gov API (recently updated bills). Keep the last 7 days and remove duplicates. If one source fails, the run continues and reports which source failed.
2. **Triage.** A fast, low-cost model (Claude Haiku) scores each item 0–10 for relevance, assigns a section, and writes a short factual summary.
3. **Compose.** A stronger model (Claude Opus) writes the brief from the top-scoring items: a subject line, an intro, and entries grouped by section.
4. **Render.** The run writes `drafts/<date>/` with `brief.html` (email-ready), `brief.md`, `brief.json`, and `sources.json` (everything fetched, plus each source's health).

Links in the brief always come from the source data, never from the model: each entry cites an item id, and the code fills in that item's URL.

## Running it

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env            # then fill in the keys
set -a; source .env; set +a
.venv/bin/python -m donorbrief --dry-run   # fetch sources only, no LLM calls
.venv/bin/python -m donorbrief             # full draft
```

In GitHub, add `ANTHROPIC_API_KEY` and `CONGRESS_API_KEY` under **Settings → Secrets and variables → Actions**. The workflow runs every Monday, or on demand from the **Actions** tab (**Weekly brief → Run workflow**). Each run shows the draft and source health on its summary page and attaches the files as a download.

## Managing sources

Edit [`config/sources.yaml`](config/sources.yaml). Add a feed with `type: rss` and its URL. Pause a source with `enabled: false`. For broad sources, add `keywords:` so only matching items are kept. To add Google Alerts, set each alert's **Deliver to** to **RSS feed** and add its feed URL.

## Checking quality

`eval/` holds a frozen week of items (`items-2026-10-12.json`) and hand labels for which ones belong in the brief (`labels-2026-10-12.yaml`). After changing prompts, models, or the relevance threshold, run:

```bash
.venv/bin/python -m donorbrief.evaluate
```

It runs the triage model on that week (well under a cent), saves the run in `eval/runs/`, and reports how many must-include items were kept, whether they landed in the right section, and how much noise got through. Re-score a saved run with `--replay eval/runs/<file>.json`. The labels are a first pass by Claude; IoG staff should review them.

## Design

The email, edit page, and archive follow IoG's brand: see [`docs/STYLE.md`](docs/STYLE.md) for colors, type, and spacing.

## Changing the LLM

Models are set in `config/sources.yaml` under `llm:`. Provider code lives in [`donorbrief/llm.py`](donorbrief/llm.py). To use another provider, add a class with the same `structured()` method and register it in `PROVIDERS`.

## Status

- [x] Source collection, triage, composition, HTML/Markdown draft
- [x] Weekly GitHub Action with source-health report
- [ ] Deliver the draft to IoG staff by email
- [ ] Editing page and one-click send to subscribers
- [ ] Public archive of past briefs
