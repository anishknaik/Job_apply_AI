# jobpipe — Phase 1

Daily job discovery and scoring. Pulls postings from a curated list of target
companies' ATS boards, filters and scores them against a master profile, and
writes a ranked shortlist to a Google Sheet.

See `PROJECT.md` for the *why* and `PHASE1_SPEC.md` for the *what*.

## Status

The spec's build order assumed Greenhouse and Lever were the primary sources.
The verified target list is **16 Greenhouse, 20 Ashby, zero Lever**, so the
order below is revised: Ashby takes step 4, and Lever is out of Phase 1
entirely rather than shipping a fetcher that runs every morning and matches
nothing.

| Step | State |
|---|---|
| 0. `verify` command | done — probes every token before any parsing runs |
| 1. `targets.csv` | done — 36 companies, all 36 tokens return 200 with rows |
| 2. SQLite schema + `db.py` | done |
| 3. Greenhouse fetcher | done — second run yields 0 new postings |
| 4. **Ashby fetcher** (was Lever) | done — 20 of 36 companies |
| 5. Master profile | done — 3,355 words, ~6,290 tokens (clears Haiku's cache floor) |
| 6. Stage A filters | done — 93.0% kill rate, audited against live data |
| 7. Stage B scoring + calibration | **done** — awaiting hand-scored calibration set |
| 8. Sheets sync | next |
| 9. Cron | blocked on step 8 |
| 10. SmartRecruiters | pending — N+1 fetcher, no description in list response |
| 11. Firecrawl long tail | not started |
| 12. Expand `targets.csv` | not started — do not scale until scoring is calibrated |

**Lever is dropped from Phase 1.** Its verified shape is recorded under
"Notes carried from verifying the APIs" below, so re-adding it is cheap if a
Lever company ever joins the list.

## Cold start: nothing is scored until you say so

The first run against a fresh database backfills every open req at all 36
companies — thousands of rows. Scoring is therefore **opt-in and capped**:

```bash
JOBPIPE_ENABLE_SCORING=1 python run_daily.py    # only once you've looked
```

Without that variable the pipeline populates `postings`, runs Stage A, and
scores nothing, so you can eyeball what survives before spending anything.
Even once enabled, a queue above `JOBPIPE_MAX_SCORES_PER_RUN` (default 600)
stops the run rather than working through it — a sudden jump means a filter
was loosened or a big board was added, and the right response is to look, not
to pay.

## The `tier` column does something

`tier` is read at sheet-sync time via `targets.tier_thresholds()`:

| tier | meaning | minimum score to reach the sheet |
|---|---|---|
| 1 | would accept today | 6 |
| 2 | strong yes | 6 |
| 3 | would consider | **8** |

So ranking a company as tier 3 makes it surface only when a role is a strong
match. Everything currently sits at tier 2; re-rank whenever you like, now
that the column is wired to a behaviour.

## Setup

```bash
python -m pip install -r requirements.txt
cp .env.example .env        # then fill it in
python -m jobpipe.cli init
python -m jobpipe.cli verify   # confirm every board token before fetching
```

## Daily use

```bash
python run_daily.py                  # what cron runs
```

**Run `verify` after every edit to `targets.csv`.** A wrong `board_token` is
the top silent-failure mode here: Greenhouse and Ashby both return an empty
list rather than an error, so the company just quietly never appears. `verify`
sends one request per token and prints the HTTP status and row count with no
parsing in between, so a parser bug can never be mistaken for a bad token.

## Deduplication

Two layers, and the second one is subtler than it looks.

1. **`UNIQUE (ats_type, source_id)`** — catches ordinary re-runs. On conflict
   only `last_seen_at` is touched.
2. **`content_hash = sha256(company + normalized_title)`** — catches reposts
   *and* multi-location listings.

`content_hash` deliberately **excludes location**. Both Greenhouse and Ashby
return one row per location for a multi-location req: Harvey's "Technology
Enablement Analyst" came back five times — New York, Remote, San Francisco,
Chicago, Dallas — each with its own `source_id`. With location in the key
those five hash differently, survive dedupe, get scored five times and land in
the sheet five times.

The worse failure is quieter: the row the pipeline keeps might be the San
Francisco one, so a req genuinely open in New York is filtered out on location
while its New York twin sits marked as a duplicate and never scored. To
prevent that, a duplicate's location is **merged into the canonical row** and
that row's Stage A verdict is cleared so it is re-evaluated against the full
list. Harvey's five rows now collapse to one carrying
`New York; Remote; San Francisco; Chicago; Dallas`.

### Seniority is canonicalised, not stripped

The spec says `normalized_title` should strip seniority prefixes. That is
wrong in practice: "Senior Software Engineer" and "Software Engineer" are two
different open reqs at one company, not one req written two ways, and
collapsing them deletes one permanently.

The failure that proved it: `Deployed Engineer (Early Career-NYC)`, posted 9
days ago in New York, normalised to the same value as `Deployed Engineer
(NYC)` from 68 days earlier, because `early career` was being treated as a
seniority band. The older row became canonical, was discarded on posting age,
and took the early-career role — the single best-matching posting in the
dataset for a recent graduate — with it.

Stripping only helps when a company changes the level between listings, which
is rare. So only spelling variants are unified now: `Sr.` → `senior`,
`Jr.` → `junior`. Everything else is left in the title.

This only works if `normalized_title` is precise, and originally it was not.
Two further bugs made it far too aggressive:

* the seniority regex stripped `staff` from **"Member of Technical Staff"**,
  turning every variant into the meaningless `member of technical`
* *all* trailing parentheticals were stripped — but for several companies the
  parenthetical **is** the role: `Member of Technical Staff (AI Inference
  Engineer)`, `Senior Solutions Architect (DS/ML/GenAI/LLM)`

Together those collapsed 42 distinct Perplexity engineering roles into a
single hash. Dropping location from the key on top of that would have deleted
41 real jobs. Now a parenthetical is dropped only when it is a level, an
employment type, or a restatement of the posting's own location — so those 42
rows produce 38 distinct hashes, while Harvey's 5 produce 1.

On live data: 6,905 rows fetched, 1,473 collapsed as duplicates (1,188 of them
contributing a city to a canonical row), 5,432 unique reqs.

## Stage B — scoring

```bash
python -m jobpipe.cli score --yes             # --yes is mandatory; it spends money
python -m jobpipe.cli score --rescore --yes   # after any rubric change
python -m jobpipe.cli sheet                   # preview what the sheet will hold
python -m jobpipe.cli sync                    # push it to Google Sheets
python -m jobpipe.cli calibrate --sample 20   # blank sheet to hand-score
python -m jobpipe.cli calibrate               # compare model vs your judgement
```

### Two score axes, not one

`overall` is strict fit on paper. `apply_score` is whether it is worth an
application anyway. They diverge constantly in one direction — **right skills,
not enough years** — and collapsing them into a single number means either
losing those roles or inflating every score to keep them.

The sheet surfaces on `apply_score` and shows `overall` beside it.

Three precedence rules govern `apply_score`: a hard dealbreaker caps it at 2;
a wrong-function role caps it at 3; otherwise a *years* bar alone can never
drag it below 6 when `skills_match >= 7`. A low `seniority_fit` driven by
**scope** rather than years is explicitly not covered by that floor.

### Rank, not threshold

The sheet takes the **top N by `apply_score`** above a low floor, rather than
everything above a cutoff. Calibration showed the model's positives pile onto
exactly 6, so one point of drift swung the sheet between three rows and
twelve. Ranking gives the spec's "5–20 roles" reliably and removes a parameter
that could not be tuned on the available sample.

### Archetype and legitimacy (§3.1, §3.2)

Every posting is classified into a lane — `ai_ml_engineer`, `data_scientist`,
`ml_platform`, `swe_generalist`, `analytics`, `non_technical` — *before*
scoring, and the rubric branches on it. A single rubric spanning AI/ML
engineering and data science is a known cause of scores drifting to the middle.
`non_technical` short-circuits to 1.

`legitimacy` (`ok` / `suspicious` / `likely_ghost`) rides on its own axis and
never feeds any score: a legitimate poor-fit role and a fake perfect-fit role
need different handling. It appears in the sheet only when it is *not* `ok`,
since an "ok" in every row trains you to stop reading the column.

**Calibrate before trusting the ranking.** The first version of any scoring
prompt clusters everything at 6–8, which looks fine in the sheet and carries
no information. `calibrate` reports mean absolute error, directional bias, and
— separately — whether the scores are clustered at all, because a good MAE
with a 3-point range is still a useless ranking.

Three deliberate deviations from the spec, all verified:

* **No `temperature`.** Non-default sampling parameters return a 400 on
  `claude-sonnet-5`, the escalation model — so the spec's determinism rule
  breaks exactly on the borderline scores it matters most for. Determinism
  comes from `output_config.format` with a JSON schema instead, which is a
  stronger guarantee and works on both models.
* **Prompt caching is asserted, not assumed.** Haiku 4.5 refuses to cache a
  prefix under 4,096 tokens and says nothing when it declines —
  `cache_read_input_tokens` just stays 0 and the bill is 10× higher forever.
  `check_cacheable()` measures the prefix at startup and refuses to run if it
  is short. The current profile measures ~6,290 tokens.
* **Thinking disabled.** This is a fixed-shape classification run thousands of
  times. Thinking is on by default on Sonnet 5, so it is turned off explicitly.

## Tuning Stage A

This is where the recurring API bill is won or lost — every posting killed
here is one you never pay to score.

```bash
python -m jobpipe.cli filter --refilter    # re-evaluate after editing filters.yaml
python -m jobpipe.cli audit                # what was thrown away, and by which rule
python -m jobpipe.cli audit --rule title_exclude --limit 50
python -m jobpipe.cli audit --passed       # what survived
python -m jobpipe.cli stats                # per-company pass rates
```

**Stage A is deliberately loose.** At Haiku pricing a false positive costs a
fraction of a cent, while a false negative is invisible and permanent — you
never learn the role existed. So Stage A only filters on what is cheap and
certain (location, obviously non-technical functions). Seniority judgement
belongs in Stage B, where `seniority_fit` is an explicit scored dimension and
the model has read the description.

Consequences of that principle, each driven by a posting it was losing:

* `manager` is **not** excluded. It killed 1,266 postings, ~405 of which also
  matched the title allowlist. Stage A only sees a title; it should not be
  deciding whether "Technical Lead" is an IC role.
* `new grad`, `new graduate` and `early career` are **not** excluded. They are
  the highest-value postings in the dataset for a recent graduate, and
  excluding them was silently deleting Snowflake's "AI Research Scientist, New
  Grad", Sentry's "Software Engineer, New Grad (2027)" and Sigma's "Software
  Engineer (New Grad Program)".
* `max_age_days` is 60, and campus programmes are exempt from it entirely via
  `max_age_exempt_titles` — they are posted months ahead and stay open. Sigma's
  New Grad Program, open in New York, was 127 days old.

**Read the discard pile before trusting a kill rate.** Use the histogram — it
groups rejections by the exact term that fired, which is the number that says
whether a rule earns its place:

```bash
python -m jobpipe.cli histogram --min-count 20
```

That is how `manager` was found killing 1,266 postings, and how `intern` was
confirmed to be killing only 18 rather than every "Internal" and
"International" title. Auditing the first real run turned up three false
negatives that a headline "96.7% killed" completely hid:

* `staff` in the title blocklist was deleting every **"Member of Technical
  Staff"** — the standard IC engineering title at Perplexity, Cohere, OpenAI
  and Cockroach Labs. 81 postings, 65 of them in a location you'd work in.
* Evaluating a multi-location string as one blob killed genuine New York roles
  because they were *also* open in London. 101 postings.
* Plain substring matching had `intern` matching "**Intern**al Agent" and `ny`
  matching "Germa**ny**" and "Su**nny**vale".

The filter engine now uses word-boundary matching, blocklist masking
(`title_exclude_unless`) and per-location evaluation. Every one of those bugs
is pinned by a test in `tests/test_filters.py::TestRegressions`.

## Tests

```bash
python -m pytest tests/ -q
```

## Layout

```
config/
  targets.csv          company target list (verified board tokens)
  filters.yaml         Stage A rules — tune here, not in code
jobpipe/
  fetchers/
    http.py            shared retry/backoff + polite delay
    greenhouse.py      lever.py      ashby.py
  normalize.py         Posting dataclass, title normalization, content_hash
  db.py                SQLite — the source of truth
  dedupe.py            source_id (layer 1) + content_hash reposts (layer 2)
  filters.py           Stage A
  pipeline.py          stage orchestration, per-company error isolation
  cli.py               init / fetch / filter / audit / stats / targets
tests/
run_daily.py           cron entry point
```

## Notes carried from verifying the APIs

* **Greenhouse** `content` is HTML-*escaped* (`&lt;h2&gt;`), and `first_published`
  is the real posting date — `updated_at` moves on any edit. `content=true`
  makes the endpoint large (Stripe alone is ~4.3 MB).
* **Lever** (out of Phase 1, recorded for later) returns a bare JSON array;
  the title field is `text`, `createdAt` is epoch **milliseconds**, and the
  body is split across `descriptionPlain` / `openingPlain` /
  `descriptionBodyPlain` / `additionalPlain`. A bad token 404s, a valid empty
  board returns `[]` — keep those distinguishable.
* **Ashby** ships `descriptionPlain` and a real `isRemote` boolean, but
  unlisted postings are still in the array and must be dropped on `isListed`.
* **SmartRecruiters** returns no description in the list response — it needs a
  second request per posting, so it is an N+1 fetcher and is deliberately last.

## Provider and models (addendum §2)

Scoring runs on **Gemini**, behind a provider abstraction in
`jobpipe/providers/` — swapping is a config change:

```
JOBPIPE_PROVIDER=gemini
JOBPIPE_SCORING_MODEL=gemini-3.1-flash-lite
JOBPIPE_ESCALATION_MODEL=gemini-3.5-flash-lite
```

**`gemini-2.5-flash-lite`, the model §2 names, is unusable** — verified live,
the API returns "no longer available to new users" for a freshly created key.
The addendum's first escalation step is the effective primary.

The key must belong to a **billing-enabled** project: Google's free tier
permits training on submitted prompts, and the prompt is a full career profile.

Three provider differences the abstraction hides:

1. **Caching is implicit on Gemini**, not an explicit breakpoint. There is no
   TTL to set — keeping the profile byte-identical at the front of
   `system_instruction` *is* the technique. Floors differ by model (2,048 on
   2.5, 4,096 on 3.x), so `cache_minimum_tokens()` is part of the provider
   interface. Measured hit rate: ~36% of input tokens.
2. **Both vendors fail silently below the cache floor** — no error, the counter
   just stays at 0 and the bill is ~10× forever. `check_cacheable()` measures
   the prefix at startup and refuses to run if it is short.
3. **`temperature=0` works on Gemini but 400s on `claude-sonnet-5`.**
   Determinism comes from the JSON schema, the one mechanism both support.

Anthropic remains implemented as the last rung of §2's escalation ladder.

## Measured cost

| | |
|---|---|
| per scored posting | ~0.18¢ (incl. ~28% escalation) |
| one-time backfill, 209 postings | ~40¢ |
| new scoreable postings per day | 4–5 (measured over 30 days) |
| **projected steady state** | **~30¢/month** |

Inside the addendum's $0.30–1.20/month estimate, at the low end.

## What's needed next

Write the master profile to `config/master_profile.md` (gitignored). The spec
is right that length is a feature: 3,000+ words, every project, metric, tool
and responsibility — including things you'd never put on a resume. Everything
downstream inherits its quality.
