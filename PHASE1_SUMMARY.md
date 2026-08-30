# Phase 1 — Summary

Daily discovery → dedupe → deterministic filters → LLM scoring → Google Sheet.
Runs unattended at 10:00 via Task Scheduler.

## What runs

| Stage | Detail |
|---|---|
| Fetch | 142 companies, Greenhouse + Ashby public JSON. ~13,400 postings/run |
| Dedupe | `UNIQUE(ats_type, source_id)` + `content_hash(company + normalized_title)` |
| Stage A | Word-boundary regex filters. ~95% kill rate, no model, no cost |
| Stage B | Gemini 3.1 Flash-Lite, escalating borderline scores to 3.5 |
| Sheet | Top 20 by `apply_score`, upserted on url. Human columns never written |

`python -m jobpipe.cli daily --yes` does all of it. Each stage is isolated —
a failed board, a rubric rejection or a Sheets outage degrades the run rather
than aborting it.

## Measured

- **Calibration** against 24 hand-scored postings: `overall` MAE 1.21 / bias
  +0.71, `apply_score` MAE 1.12 / bias +0.79. At the live top-20 cutoff:
  **recall 100%, precision 59%** — nothing the human would apply to is hidden,
  at the cost of seven surfaced roles they would rate below the bar. That is
  the intended error direction: a false negative is invisible and permanent, a
  false positive costs a fraction of a cent.
- Loosening `non_technical` to classify on the work rather than the title
  fixed the roles it was wrongly zeroing, and cost ~0.45 MAE on `overall` by
  making the model broadly more generous. Accepted knowingly.
- **Cost** ~0.18¢ per scored posting, ~40% prompt-cache hit rate. Steady state
  ~30¢/month at 4–5 new postings a day.
- **Determinism**: clear-cut postings reproduce exactly across runs; ambiguous
  ones vary ~2 points even at temperature 0.
- 298 tests, no network.

## Decisions that departed from the spec

**Lever dropped, Ashby added.** Lever's public endpoints were dead on every
target. Verified by curling all four ATS APIs before writing a parser.

**Gemini, not Anthropic.** `gemini-2.5-flash-lite` — the model §2 names — is
unavailable to new API keys. Confirmed live, not assumed. A provider
abstraction keeps Anthropic as the escalation ladder's last rung.

**Rank, not threshold.** The sheet takes the top N rather than everything
above a cutoff. Calibration showed scores pile onto one value, so a fixed
threshold swung the sheet between three rows and twelve.

**Two score axes.** `overall` is strict fit on paper; `apply_score` is whether
it is worth applying anyway. They diverge constantly in one direction — right
skills, not enough years — and one number cannot carry both.

**Liveness without a browser.** §4.1 prescribes Playwright. Ashby's SPA
returns HTTP 200 for dead postings, so a browser check reports them live —
wrong on half the sources. Feed absence via `last_seen_at` is free and
correct; the window is 7 days so one failed fetch cannot bury a board.

**Status taxonomy adapted, not copied.** Lifted from career-ops (MIT) per
§5A.1, minus its Spanish and Turkish aliases, plus a `not_applied` resting
state the source had no need for.

**Status history is an event log, not a column.** §4.3 wants per-channel
advance rates, and an advance rate is a question about transitions. A column
overwritten from `interview` to `rejected` destroys the evidence an interview
happened.

## Bugs worth remembering

- `normalize_title` over-collapsed, giving 42 distinct roles one hash. Caught
  by verifying before applying a requested fix that would have deleted 41 real
  roles.
- Substring matching: `intern` matched "Internal", `ny` matched "Germany".
  Everything moved to word-boundary regex.
- Sponsorship allow-phrases matched *inside* refusals ("no sponsorship is
  available"). All allow phrases are now anchored to `we <verb>`.
- `--rescore` deleted every score and *then* hit the spend cap, leaving the
  table empty — the guardrail causing the outage it exists to prevent.
- Evicted sheet rows froze at stale scores; one showed 10 where the truth was
  4. Rows are never deleted, so they are now refreshed instead.
- `engagement` was added to the schema, migration and queries but missed in
  both INSERT statements. 220 rows wrote NULL silently. The column list now
  has one home and a schema-driven round-trip test.

## Deferred, deliberately

- **Ghost-job detection (§3.3)** needs ~6 months of repost history; the
  database has days. Running it now would flag every multi-location req.
- **SmartRecruiters + Firecrawl long-tail.** Canva and Glean are verified live
  on SmartRecruiters and excluded only because no fetcher exists; probing a
  board is not the same as being able to read it. ~43 more candidates
  (dbt Labs, Mistral, Replicate, Rippling, Statsig, Zoox…) responded on
  neither Greenhouse nor Ashby and are presumably on Lever, Workday, or custom
  pages — that is the Firecrawl long tail.

## Target list

142 companies, every board token verified against the live endpoint rather
than guessed — a wrong token fails silently, returning an empty list rather
than an error, so the company would just never appear.

Expanded from 36 by probing 207 candidates across three ATS types and keeping
only boards that returned postings. Hit rate 108/207; the misses were mostly
token spellings, not absent boards.

Job aggregators (LinkedIn, Indeed, SimplyHired, Monster, CareerBuilder,
ZipRecruiter, Dice, Wellfound) remain out. Their terms prohibit scraping, they
resyndicate the same ATS feeds a day later with worse fields, and Dice's
legacy public API no longer resolves. ZipRecruiter's Publisher Partner API is
the one legitimate paid route if volume ever justifies it.

## Known limits

- Calibration measures agreement on roles the pipeline *surfaced*. Roles Stage
  A killed remain invisible by construction.
- `apply_score` runs ~0.6 generous and is more clustered than the human's
  (stdev 1.22 vs 1.74).
- Only 14 of 220 postings mention work authorisation at all. Silence is not
  consent — sponsorship stays unresolved on most roles until a recruiter call.
