# DonorBrief

Weekly AI-generated philanthropic intelligence briefing for the Institute on Generosity (IoG) and the field.

Every Monday a GitHub Action gathers the past week's items from public sources, has an LLM screen and summarize them, and writes a draft brief for IoG staff to review. Nothing is sent to readers without human review.

## How it works

1. **Collect.** Fetch every source in [`config/sources.yaml`](config/sources.yaml): RSS feeds, the Federal Register API (IRS rules and notices), and the Congress.gov API (recently updated bills). Keep the last 7 days, remove duplicates, drop sponsored content, and for items with little text, add the article's opening paragraphs (Google News links can't be followed, so those stay headline-only). If one source fails, the run continues and reports which source failed.
2. **Triage.** A fast, low-cost model (Claude Haiku) scores each item 0–10 for relevance, assigns a section, and writes a short factual summary.
3. **Compose.** A stronger model (Claude Opus) writes the brief from the top-scoring items, with the last 8 weeks of history in view. The brief has two halves:
   - **Top: the point in 30 seconds.** *Key takeaways*: 3–5 numbered claims, each with one sentence on why it matters and links to the stories behind it (takeaways that don't point to a real story in the brief are dropped). Then *What's building*: up to 3 patterns across weeks, each resting on at least 3 real stories from at least 2 different weeks; the code checks this, computes the counts ("9 stories over 4 weeks"), and links the evidence.
   - **Bottom: the news**, by section, each story once. Stories behind a takeaway get 2–3 sentences and a "Takeaway N" tag; the rest get one line. Stories already covered in an earlier brief come back only with real news, marked "Update".
4. **Render.** The run writes `drafts/<date>/` with `brief.html` (email-ready), `brief.md`, `brief.json`, and `sources.json` (everything fetched, plus each source's health).
5. **Hand off to Mailchimp.** The draft becomes a Mailchimp draft campaign addressed to IoG's audience, and reviewers get a preview copy by email. Staff edit and send it in Mailchimp. DonorBrief never sends it.

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

## Mailchimp

Each Monday's draft is created in IoG's Mailchimp account as a draft campaign (**Campaigns → Drafts**). Reviewers listed in `test_emails` get a preview copy. A person checks it and presses **Send** in Mailchimp.

To connect it:
1. In Mailchimp, create an API key (**Profile → Extras → API keys**) and add it as the `MAILCHIMP_API_KEY` repository secret.
2. In [`config/sources.yaml`](config/sources.yaml), under `mailchimp:`, fill in `audience_id` (**Audience → Settings → Audience name and defaults**), `reply_to` (an address verified in Mailchimp) and `test_emails`.

Without the secret, this step is skipped and the draft stays on the run page as before. Once Mailchimp holds the draft, the public run page shows only a pointer to it, not the draft itself.

To push an existing draft by hand: `.venv/bin/python -m donorbrief.mailchimp drafts/<date>`.

## Week-to-week memory

Each run saves the week's screened items to `history/<Monday>.json` (source titles, links and scores, plus whether each made the brief; never the drafted wording, since the repo is public). The workflow commits it. The editor reads the last 8 weeks to find trends and avoid repeating stories.

Each saved story also carries 1–3 **topic tags** from [`config/topics.yaml`](config/topics.yaml) (a fixed list, so themes can be counted the same way month after month), plus an "emerging topic" name when nothing on the list fits. Impact investing and volunteering are tracked as secondary interests. The list includes themes IoG champions (generosity as common ground, strategic giving, democratizing giving, women in philanthropy, philanthropy education). After editing the list, run the workflow with **retag** checked to re-tag all history with it (scores are kept).

To score older weeks into history, put their `sources.json` files in `history/backfill/<YYYY-MM-DD>/` and run the **Weekly brief** workflow with **backfill** checked.

## Managing sources

Edit [`config/sources.yaml`](config/sources.yaml). Add a feed with `type: rss` and its URL. Pause a source with `enabled: false`. For broad sources, add `keywords:` so only matching items are kept. To add Google Alerts, set each alert's **Deliver to** to **RSS feed** and add its feed URL.

## Public archive

When a brief is sent, its `rendered.json` is copied to `archive/<YYYY-MM-DD>/`. Build the public archive (an index page plus one page per brief, in IoG's brand style) with:

```bash
.venv/bin/python -m donorbrief.archive   # writes site/
```

`site/` is plain HTML and can be hosted on Vercel, GitHub Pages, or IoG's website.

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
- [x] Mailchimp draft campaign with a preview copy to reviewers (waiting on IoG's Mailchimp key and audience)
- [ ] Copy each sent brief into `archive/` for the public archive
- [x] Public archive pages (hosting to be decided)
