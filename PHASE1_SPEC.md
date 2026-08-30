# Job Search Pipeline — Phase 1 Spec

## Objective

A daily automated job **discovery and scoring** pipeline. Every morning it pulls fresh
postings from a curated list of target companies, filters and scores them against my
profile, and writes a ranked shortlist to a Google Sheet.

**Definition of done:** at 8am I open one sheet and see 5–20 new roles, each scored 1–10
with a sentence of reasoning, and I have not visited a job board.

## Non-goals (do NOT build these in Phase 1)

- No resume or cover letter generation
- No browser automation or form filling
- No email sending or drafting
- No LinkedIn or Indeed scraping (blocked, ToS-prohibited, stale data)
- No web UI — the Google Sheet is the entire interface

If a task feels like it belongs to a later phase, stop and ask.

## Stack

- **Python 3.11+**
- **SQLite** — source of truth for all postings and scores
- **Google Sheets API** — read-only human view, written by the pipeline
- **Anthropic API** — scoring (see Model Usage below)
- **Firecrawl** — long-tail career pages only, NOT the primary fetch path
- **cron** — daily trigger

This is a deterministic script, not an agent. It must run identically at 6am every day
with no human watching. Prefer boring, explicit code over clever abstractions.

## Repo layout

```
jobpipe/
  config/
    targets.csv          # company target list
    filters.yaml         # Stage A filter rules
  jobpipe/
    fetchers/
      greenhouse.py
      lever.py
      ashby.py
      smartrecruiters.py
      firecrawl_generic.py
    normalize.py         # -> Posting dataclass
    dedupe.py
    filters.py           # Stage A
    scoring.py           # Stage B
    sheets.py
    db.py
    cli.py
  tests/
  run_daily.py
  .env.example
```

## Data model

```sql
CREATE TABLE postings (
    id                INTEGER PRIMARY KEY,
    ats_type          TEXT NOT NULL,
    source_id         TEXT NOT NULL,
    company           TEXT NOT NULL,
    title             TEXT NOT NULL,
    normalized_title  TEXT NOT NULL,
    location          TEXT,
    remote_flag       INTEGER,
    description       TEXT,
    url               TEXT NOT NULL,
    posted_at         TEXT,
    first_seen_at     TEXT NOT NULL,
    last_seen_at      TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    stage_a_passed    INTEGER,
    stage_a_reason    TEXT,
    UNIQUE (ats_type, source_id)
);

CREATE INDEX idx_repost ON postings (content_hash);

CREATE TABLE scores (
    posting_id        INTEGER PRIMARY KEY REFERENCES postings(id),
    skills_match      INTEGER,
    seniority_fit     INTEGER,
    domain_relevance  INTEGER,
    overall           INTEGER,
    dealbreakers      TEXT,          -- JSON array
    reasoning         TEXT,
    model             TEXT,
    scored_at         TEXT
);

CREATE TABLE runs (
    id            INTEGER PRIMARY KEY,
    started_at    TEXT,
    finished_at   TEXT,
    fetched       INTEGER,
    new_postings  INTEGER,
    stage_a_pass  INTEGER,
    scored        INTEGER,
    input_tokens  INTEGER,
    output_tokens INTEGER,
    errors        TEXT
);
```

Store **every** posting ever seen. Only new ones proceed to scoring.

## Step 1 — Target list

`config/targets.csv`:

```csv
company,ats_type,board_token,tier,notes
Example Co,greenhouse,examplecoinc,1,dream role
```

- `tier`: 1 = would accept today, 2 = strong yes, 3 = would consider
- `board_token` is the company slug in its job board URL
- Target 150–250 rows eventually. **Start with 20.**

Finding tokens is manual. Shortcut: many career pages iframe their ATS — view source
and grep for `greenhouse.io`, `lever.co`, `ashbyhq.com`, `smartrecruiters.com`.

## Step 2 — Fetchers

These are **public JSON syndication endpoints** — the same ones that power embedded job
widgets on company career pages. No scraping, no HTML parsing, no blocking.

| ATS | Endpoint |
|---|---|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true` |
| Lever | `https://api.lever.co/v0/postings/{token}?mode=json` |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{token}` |
| SmartRecruiters | `https://api.smartrecruiters.com/v1/companies/{token}/postings` |

**Before writing any parser: curl each endpoint once and confirm the response shape.**
Greenhouse and Lever are stable and well-documented. Ashby and SmartRecruiters have
changed shape before — verify, don't assume. Do not include Workable in v1; its public
endpoint is the least stable of the set.

Requirements:
- Sequential requests with a polite delay; do not hammer
- Retry with exponential backoff on 5xx; a single company failing must not abort the run
- Log per-company fetch counts so silent failures are visible
- Greenhouse `content` field is HTML-escaped — unescape and strip tags before storing

**Firecrawl** handles companies with a custom career page and no standard ATS. Keep it
in a completely separate code path. Expect ~15% of the target list and ~60% of the bugs.
**Do not touch Firecrawl until the JSON fetchers work end to end.**

## Step 3 — Normalize

All fetchers return a common `Posting` dataclass:

```python
@dataclass
class Posting:
    ats_type: str
    source_id: str
    company: str
    title: str
    location: str | None
    remote_flag: bool
    description: str        # plain text, tags stripped
    url: str
    posted_at: datetime | None
```

`normalized_title`: lowercase, strip seniority prefixes, roman numerals, level suffixes
(`II`, `L4`, `Sr.`), and parenthetical location suffixes.

## Step 4 — Dedupe

Two layers:

1. **Primary:** `UNIQUE (ats_type, source_id)` — catches ordinary re-runs. On conflict,
   update `last_seen_at` only.
2. **Secondary:** `content_hash = sha256(company + normalized_title + location)` —
   catches reposts. Companies close and relist the same req constantly. Without this
   you will re-score the same job every three weeks and pay for it every time.

If a `content_hash` was seen in the last 60 days, mark the new row as a repost and skip
scoring; carry the existing score forward.

**Verify by running the pipeline twice in a row. The second run must produce zero new
postings and zero scoring calls.** This is the single most important test in Phase 1.

## Step 5 — Stage A filters (deterministic, no LLM)

Rules live in `config/filters.yaml` so they can be tuned without code changes:

```yaml
title_exclude: [intern, principal, director, vp, head of, staff]
title_require_any: []          # optional allowlist
location_allow: [remote, new york, nyc, newark, jersey city]
max_age_days: 30
description_exclude: [security clearance, ts/sci, on-site 5 days]
```

Record pass/fail and the triggering rule in `stage_a_passed` / `stage_a_reason` so I can
audit what's being thrown away. This should kill roughly 70% of postings.

**Tighten these aggressively.** Every posting killed here is one you don't pay to score.
Getting Stage A from 70% to 90% cuts the recurring API bill by two-thirds for free.

## Step 6 — Stage B scoring (LLM)

Only postings where `stage_a_passed = 1` and no score exists.

**Model usage:**
- Default: `claude-haiku-4-5` for all scoring
- Escalate to `claude-sonnet-5` only when Haiku returns `overall` in 5–7 (borderline)
- Use **prompt caching** on the master profile with a 1-hour cache breakpoint. The
  profile is ~5k tokens and is resent on every call; cache hits cost 10% of base input
  rate. This is the single biggest cost lever.
- `temperature = 0` — the same posting must score the same way twice
- Do NOT use the Batch API in v1. The 24-hour SLA means the sheet isn't reliably ready
  at 8am, and the savings are trivial at this volume.

**Structure:** master profile in the cached system prompt, job description in the user
message. Force reasoning *before* the number.

**Required JSON output:**

```json
{
  "reasoning": "2 sentences",
  "skills_match": 0,
  "seniority_fit": 0,
  "domain_relevance": 0,
  "dealbreakers": ["string"],
  "overall": 0
}
```

`dealbreakers` earns its keep — it surfaces the clearance requirement or the
on-site-in-Austin line buried in paragraph nine.

Validate the JSON; on parse failure retry once, then record the failure and move on.
Never let one bad response kill the run.

**Calibration (required before scaling past 20 companies):**
Build `cli.py calibrate` that scores a fixed set of 20 postings I have hand-scored, and
prints a side-by-side comparison with mean absolute error.

The first prompt will almost certainly cluster everything at 6–8. Fixes: include three
worked examples spanning the range, and state explicitly what a 3 looks like versus a 9.
Re-run monthly.

## Step 7 — Google Sheets sync

Columns, in this order:

```
date_found | company | role | score | reasoning | dealbreakers | url |
status | applied_date | resume_version | contact | followup_due
```

- Phase 1 writes only the first seven. The last five stay empty — but **create them now**
  so there's no schema migration in three weeks.
- Sort by `score` descending, then `date_found` descending
- Only write rows with `overall >= 6`
- Never overwrite the `status` column — I edit that by hand. Sync must be append/update
  by `url` as key, not a full-sheet rewrite.

## Step 8 — Scheduling

`run_daily.py` runs the full pipeline and writes a row to `runs`. Cron at 6am local.
Log to a rotating file. On unhandled exception, still write the `runs` row with the
error so failures are visible the next morning.

## Build order

Do these in sequence. Do not skip ahead.

1. `targets.csv` with **20 companies only**
2. SQLite schema + `db.py`
3. Greenhouse fetcher → DB. **Run twice, confirm zero dupes.**
4. Lever fetcher
5. *(I write the master profile here — blocking, not a code task)*
6. Stage A filters + audit output
7. Stage B scoring + calibration command
8. Sheets sync
9. Cron
10. Ashby + SmartRecruiters fetchers
11. Firecrawl long-tail path
12. Expand `targets.csv` to full size

Steps 1–9 at 20 companies is a weekend. **Do not scale the company list until scoring is
calibrated** — otherwise you're debugging a prompt against 400 rows instead of 20.

## Cost guardrails

- Log token counts per run in the `runs` table
- Add a config cap: if a single run would score more than 200 postings, stop and warn
- Check the first week's actual spend before setting the cron to daily
- Expected steady state: a few dollars per month with tight Stage A filters and caching

## Non-negotiables

- **No LinkedIn or Indeed scraping.** Not a preference — blocked, ToS-prohibited, stale.
- **Never delete rows from `postings`.** History is what makes dedupe work.
- **The pipeline never submits anything anywhere.** Read and score only.
- **Secrets in `.env`**, never committed. Provide `.env.example`.
- **One company failing must never abort the run.**

## Open questions to raise before building

- Confirm Ashby and SmartRecruiters response shapes via curl before writing parsers
- Confirm current Anthropic model IDs and pricing against the docs — these change
- Decide Sheets auth: service account (simpler for cron) vs OAuth
