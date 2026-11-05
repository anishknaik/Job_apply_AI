# jobpipe — Context

> Written 2026-08-29, **§2 and §7 remeasured 2026-08-31; §2 and §4 again on
> 2026-09-10, 2026-10-04 and 2026-11-05, and once more later on 2026-11-05
> on the `apply-session` branch, after Phase 7A was finished (§1, §2, §3,
> §4, §6, §7, §8.10).** Every number in §2 was read from
> the live system on the date it carries rather than copied forward,
> because several figures in `PHASE1_SUMMARY.md` and `POST_PHASE2_FIXES.md`
> had drifted.
>
> **Read order for a new session:** `PROJECT.md` (why) → `HANDOFF.md` (where
> things stand right now — its "Start here" block first, and §9 for the
> apply session) →
> `PHASE5_PLAN.md`, `PHASE6_PLAN.md`, `PHASE7_PLAN.md` (the decisions and
> build orders) → this file (the standing design, §1–§6) → `CLAUDE.md`
> (what you may and may not run).
>
> **This file is no longer the front door.** §1–§6 still hold — they are the
> design and the guardrails, and nothing has overturned them. §7's Vertex
> question is **settled** (answered inline below). §8's defect list is mostly
> **closed** — each item now carries its resolution. §9 is **superseded by
> `PHASE5_PLAN.md`**, which was written from it; §9's architecture survived,
> its open decisions did not. Where §9 and the plan disagree, the plan wins.
>
> `PHASE1_SUMMARY.md` and `PHASE2_SUMMARY.md` hold the bug archaeology worth
> not rediscovering.

---

## 1. What this is

A personal job-search pipeline for Anish Kumar Naik. It automates the
mechanical parts of applying — finding postings, scoring them for fit,
tailoring documents — and stops at the two points where automation actively
backfires: submitting an application, and sending email.

The founding assumption is that **filtering quality beats application
volume**. 400 unreviewed applications produce motion and no interviews. The
metric is interviews per hour spent, not applications per day.

| Phase | Scope | State |
|---|---|---|
| 1 | Discovery → dedupe → filters → LLM scoring → Google Sheet | Unattended daily 11:30, **scoring included again since 2026-11-04** (US and India queues, 200 each per run). Two lanes since 2026-10-03: US (main tab) and India (its own tab) |
| 2 | Queued role → tailored resume PDF → cover letter → grounding check | Running on demand |
| 3 | Application form fill, human clicks submit | Built as Phase 5 Track C; two supervised fills (LangChain 2026-09-10; LangChain 2026-10-04, from Cowork) |
| 4 | Recruiter research → outreach drafts | Not started |
| 5 | **MCP server + Cowork** | Slice 0 + Tracks A, B and C built; runs under the desktop app and Cowork since 2026-10-04 |
| 6 | **Apply runner** — fills Greenhouse and Ashby forms in its own browser; auto-submit only where nothing checks for bots, otherwise Anish reviews and submits (`PHASE6_PLAN.md`) | Built 2026-11-04, in real use: 3 applications in review mode (Sierra, Chime, Deepgram), each submitted by Anish; records `applied` after his submit. Auto-submit has never fired |
| 7 | **Apply session + AI agent** — the workbench works through the queue; Gemini 2.5 Pro drafts essays and letters; an agent for Workday/Lever/SmartRecruiters (`PHASE7_PLAN.md`) | **7A built 2026-11-05** on branch `apply-session`, not merged and not run live (slices 0-4 in a cloud session, 5-6 locally; `HANDOFF.md` §9). 7B (the agent) not started |

---

## 2. Where it stands today (2026-11-05, remeasured)

| | |
|---|---|
| Postings ingested | 30,304 |
| Stage A survivors / scored | 1,227 / 1,727 — 16 live survivors unscored, for the next scheduled run |
| India lane | its own tab and scoring queue; many scores sit outside Stage A's survivors for that reason |
| Scoring models in the corpus | `2.5-flash` 606, `2.5-pro` 494, `3.1-flash-lite` 469, `3.5-flash-lite` 158 |
| Rubric versions in the corpus | `2026-10-03.5` 1,016, none 627, `2026-08-31.3` 80, `.2` 3, `.4` 1 |
| Applications | 45 applied (210 status events); 3 through the Phase 6 runner |
| Documents | 130 rows — 81 resumes, 33 letters, 16 form answers |
| **Edit distances computed** | **7** letters measured: PostHog 0.032 and U. of Utah 0.033 (hand-written in Cowork, `letter_required` never recorded), and **five at exactly 0.000** — Sentry, Scale AI, Vapi, Sierra, Chime (Claude-session letters). Anish confirmed on 2026-11-05 that he sent all five unchanged. Since §8.10's fix the headline is letter-required only: **n=1** (Scale AI, 0.000); the other six are counted beside it, and every letter is split by writer |
| Targets | 169 companies — tier 1: 8, tier 2: 104, tier 3: 57 |
| ATS split | greenhouse 82, ashby 79, smartrecruiters 6, workday 2 |
| Bullet bank | 24 bullets across 11 roles (the jobpipe project's three added in November) |
| Tests | **1,471** on `apply-session`, all passing locally (1,371 on `master`), offline |
| Apply runs | 20 rows in `apply_runs` (dry runs and review runs together) |
| Last scheduled run | 2026-11-04 16:55 UTC — 15,280 fetched, 330 new, 0 scored (it ran before `-Spend` was re-registered that day) |
| Billing | Vertex, GCP credit (ends mid-November). The prepaid AI Studio balance is no longer drawn on |

**The scheduled run scores again since 2026-11-04.** It fetches, filters,
scores the US and India queues (each refusing past
`JOBPIPE_MAX_SCORES_PER_RUN`, 200), syncs and refreshes the india tab.
Between 2026-08-30 and 2026-11-04 it scored nothing and a backlog built up
between deliberate runs.

**Stage A, 2026-11-05:** two rules added after an audit —
`graduation_window` (a new-grad window that leaves out a May 2025
graduate) and `location_remote_abroad` (a bare "Remote" beside a non-US
place). 16 postings removed by each.

**The scoring queue is open postings only.** `get_unscored` applies the
sheet's own 7-day `last_seen_at` window, so a posting that left its board
while it waited is never paid for. 20 survivors are unscored for that reason
and will stay so.

The mixed provenance in rows 3 and 4 is deliberate, not drift: `PHASE5_PLAN`
§3.5 keeps the 3.1-era corpus as historical record and rescores only what
ranks. The `scorer` / `rubric_version` columns exist to make that mixture
visible rather than implicit.

**Posting 16,669 is a manual row, and the corpus's only one.** A University of
Utah `Data Scientist I` applied to outside the pipeline, recorded so its cover
letter had something to hang off: `ats_type='manual'`, `stage_a_passed` NULL
because Stage A never ran on it, no score, and a description marked as a
retrieved summary rather than the raw ATS payload every fetched row holds.

It is also the **second measured letter**, and it is measured only in part.
Written free-form rather than through `letters.assemble()`, it has six body
paragraphs where the schema has four, so its blocks sit offset against every
assembled letter and `summarise()` excludes it from the per-slot means —
reporting the exclusion rather than hiding it. Its whole-document distance is
indifferent to where the blank lines fall, so that does count. The practical
consequence is in `HANDOFF.md` §3: on slots the sample is still n=1.

---

## 3. Data flow

```
config/targets.csv  (169 companies, board tokens verified live)
      |
      v
Greenhouse / Ashby / SmartRecruiters / Workday public JSON
                                                       no scraping, no LinkedIn
      |
      v
normalize -> Posting  ->  dedupe (ats_type+source_id, then content_hash)
      |
      v
Stage A  deterministic word-boundary regex              ~95% kill, $0
      |    US lane: every rule. India lane (`india.select`): every rule but
      |    location, over postings Stage A excluded for being in India
      v
Stage B  gemini-2.5-flash on Vertex, escalating 5-7 to 2.5-pro
         on demand — `cli score` (US), `cli score --india`; open postings
         only. The 11:30 cron no longer scores
      |
      v
SQLite jobpipe.db  (source of truth)  <->  Google Sheet (rendered view)
      |                     two tabs: `roles` (US) and `india`
      |                     status: a changed cell is Anish's edit and flows
      |                     in; an untouched one is written back
      v
MCP session:  render_resume -> save_cover_letter / save_short_answer
              -> check_grounding (Gemini) -> copy_for_editing -> his edit
      |
      v
browser:  fill from form_answers + prepare_letter, stop before submit
      |
      v
Anish submits -> advance_status -> sync
```

**The apply session (Phase 7A, `apply-session` branch)** runs the same path
on a loop, from the workbench's `/session` page, as a child process Anish
starts. For each ticked posting: is the form still live → `render_resume`
from the grid variant, with the bank's tags the posting names as
`posting_skills`, flagging any dropped bullet carrying one → Gemini 2.5 Pro
drafts the open-ended answers and the letter (`apply/draft.py`) →
`save_*` with the model recorded as writer → `check_grounding` (a failing
draft is left blank) → the Phase 6 runner fills in review mode → Anish
submits → the runner records `applied` → next. Drafting is capped per
session (`JOBPIPE_MAX_DRAFTS_PER_SESSION`, 10).

Sheet selection (`db.get_sheet_rows`): `apply_score >= 4` floor, optional
per-company tier threshold, `last_seen_at` within 7 days, **top 25 by rank**,
max 3 per company applied *during* selection rather than after truncation.
India-only roles are excluded before the cap (`india.lane_only`). The india
tab is not ranked down to 25: it lists every live India role scoring 6 or
more (`india.MIN_SCORE`, since 2026-10-08) and any not yet scored, best first.

---

## 4. Module map

17,246 lines of Python across 58 modules and 1,465 offline tests on
`apply-session` (`master`: 15,651 lines, 56 modules, 1,371 tests), counted
with `wc -l` on 2026-11-05. The table below was last itemised at 38 modules.
Of `jobpipe/apply/` it lists only the two Phase 7A modules; see
`PHASE6_PLAN.md` §3 for the rest of that package.

| Module | Lines | Does |
|---|---|---|
| `cli.py` | 1377 | 19 subcommands; the only entry point |
| `db.py` | 889 | Schema, migrations, sheet-row ranking, documents, status log |
| `scoring.py` | 914 | Stage B rubric, escalation, spend cap, cache accounting |
| `sheets.py` | 529 | Service-account sync; human columns written only by `set_cells`, conditionally. A tab may bring its own columns |
| `india.py` | 122 | The India lane: selection, its scoring queue, its tab's rows |
| `letters.py` | 544 | Prompt, banned phrases, closing-shape rule, assembly |
| `resume.py` | 575 | HTML → Playwright PDF, one-page loop on one held Chromium, ATS parse check |
| `calibrate.py` | 552 | Hand-scored set vs model — MAE, bias, recall/precision |
| `filters.py` | 333 | Stage A rules from `config/filters.yaml` |
| `tailor.py` | 283 | Bullet selection; ids compiled into the response schema |
| `measure.py` | 274 | Levenshtein per letter and per paragraph slot |
| `bullets.py` | 249 | The bank; `Bank.select` raises on an unknown id |
| `grounding.py` | 280 | Claim extraction and routing to evidence — letters and form answers |
| `pipeline.py` / `pipeline_tailor.py` | 148 / 163 | The two runs |
| `providers/` | 671 | Gemini (Developer API and Vertex) + Anthropic behind one interface |
| `mcp_server.py` | 1099 | The 13-tool MCP surface. Thin: imports and calls |
| `variants.py` | 233 | The seven orderings, headlines, `(lane, domain)` grid |
| `header.py` | 225 | The one parser for §12; generates the resume header from it |
| `answers.py` | 159 | Form answers: the answers file, reusable answers, the §12 view |
| `status_mirror.py` | 122 | Sheet status ↔ event log: edits in, newer statuses written back |
| `letter_pdf.py` | 78 | The reviewed letter as a one-page PDF, read back before it is used |
| `fetchers/` | 738 | Greenhouse, Ashby, SmartRecruiters, Workday, SSRF-guarded HTTP |
| `apply/session.py` | 769 | Phase 7A: the apply session's worker: queue, `state.json` + `command` file, the per-posting loop, the drafting cap, the cost estimate |
| `apply/draft.py` | 309 | Phase 7A: Gemini 2.5 Pro drafts form answers and letters under `letters.py`'s rules; one retry, then a blank |

`bullets.py` also holds `posting_vocabulary` and `flag_dropped` since
Phase 7A slice 5: a posting's skill vocabulary read from its text, and the
drop flags it drives. `web.py` serves the session's six routes.

Config: `master_profile.md` (657 lines, gitignored, source of truth for every
claim), `bullets.yaml` (570), `filters.yaml` (458), `states.yml` (108),
`targets.csv` (169 rows), and `answers.yaml` (gitignored; self-ID and form
logistics, never in a prompt).

---

## 5. The decisions that carry the design

**Target ATS JSON, not aggregators.** Greenhouse/Ashby/SmartRecruiters expose
the public endpoints that power companies' own careers widgets. No scraping,
no blocking, no HTML parsing. The single biggest simplification in the
project. LinkedIn and Indeed are out on terms, staleness and legality.
Workday (2026-10-03) is the same kind of source with one difference that
matters: every company has its own host, so the SSRF allowlist lists each
one and a new Workday company is refused until its host is added.

**India is a second lane, not a looser filter.** Stage A's verdict keeps
meaning "actionable in the US"; `filters.yaml` still excludes India. The
India lane re-reads the postings Stage A excluded for location, applies the
other rules, and has its own scoring queue and its own tab. Loosening the
location rule instead would have put India roles into the US top 25 and
changed what `stage_a_passed` means for every number computed from it.

**Two-stage scoring.** An `if` statement kills 95% for free; only survivors
reach a model. Tightening Stage A is always cheaper than changing models.

**Two score axes.** `overall` is strict paper fit; `apply_score` is whether it
is worth applying anyway. They diverge constantly in one direction — right
skills, not enough years — and one number cannot carry both.

**Rank, not threshold.** Scores pile onto 6, so a fixed cutoff swung the sheet
between 3 and 12 rows. Top 25, floor 4, 3 per company.

**The bullet bank is a structural firewall — over bullet text.** Bullet ids
are compiled into the response schema as an `enum`, so the API rejects an
invented id before it reaches the code; `Bank.select` re-checks anyway. Under
MCP the enum is gone and that re-check carries the guarantee alone, which is
why `render_resume` takes a variant rather than a list of bullet ids. **No
model can put a sentence in the experience section that a human did not
write.** Only the cover letter writes prose, which is why grounding exists for
that module alone.

**The header block is not covered by the firewall, and is now GENERATED
instead.** `meta:`, `education:` and each role's company, title and dates are
trusted YAML that nothing validates. A model cannot reach them, so MCP never
widened the gap — the exposure was always that a stale or mistyped hand edit
reaches every generated resume with nothing in between, and that the two files
agreed only because somebody kept them in sync.

`master_profile.md` §12 is the source in fact now, not by convention:
`header.py` extracts it into `config/header.generated.yaml` and `load_bank()`
overlays that onto the bank. `bullets.yaml` is never written to — the
generated file is separate precisely so regenerating cannot touch a human's
bullet text, and everything §12 declines to own (`headline`, a role's `kind`
and `location`, the per-group `stack:` lines) stays hand-edited. `cli header`
regenerates, `cli header --check` reports drift, and both are free.

**Status is an append-only event log, not a column.** "Advance rate" is a
question about transitions; overwriting `interview` with `rejected` destroys
the evidence the interview happened.

**Status has two writers, and one rule between them.** Anish edits the
sheet's status cell; a session records through `advance_status`. Only a cell
that changed since the last sync counts as his edit — `sheet_status`
remembers each reading — and a cell nobody touched is written back with the
database's newer status, compare-and-set. The first version compared every
cell with the database's current status, and reverted the first
tool-recorded application thirteen seconds later (`HANDOFF.md` §5).

**Content is stored, not pointed at.** Prose regenerates at **0.53 distance
from itself** at temperature 0. A destroyed baseline is unrecoverable, and a
regeneration is not a substitute — it would report model variance as human
editing. This measurement is why `documents.content` exists.

**Liveness without a browser.** Ashby's SPA returns 200 for dead postings, so
a Playwright check is wrong on half the sources. `last_seen_at` with a 7-day
window is free and correct.

**Phase 1 is a script, not an agent.** It must run identically at 11:30 with
nobody watching. Agent-shaped tooling was always scheduled to arrive later —
which is what §9 is.

---

## 6. Guardrails

These hold across every phase and are not negotiable per-task.

- **Never submit an application.** Fill the form, stop before submit.
- **Never send email.** Drafts only, always.
- **Who writes prose changed on 2026-11-05 (Phase 7A), by Anish's
  decision.** Until then Claude wrote and Gemini checked. In an apply
  session, Gemini 2.5 Pro drafts and Gemini 2.5 Flash Lite checks: two
  models of one family, so some of the writer/checker independence of
  §8.6 is given up. What holds the line instead: a draft that fails
  `check_grounding` is left blank, and `decide.py`'s
  `answers_session_written` stops any form carrying non-Claude prose from
  auto-submitting, so Anish reviews and submits every one.
- **Never fabricate.** Every claim traces to `bullets.yaml` or
  `master_profile.md`. If the posting wants something he has not done, the
  honest output is a document without it. If a claim is true but unsupported,
  fix the profile first. It has been wrong four times — three understated, one
  (NBS 7) overstated, which nothing downstream can catch.
- **Copy a letter before editing it** — `copy_for_editing`, or
  `cp cover_letter.md cover_letter.sent.md`. Never rename, never edit in
  place. A form gets the reviewed copy, through `prepare_letter`.
- **Never hand-edit `resume.pdf`.** Fix the bank, the variant ordering, or
  `skills` tags, then regenerate.
- **Never run `cli daily`, `cli score`, `cli calibrate` or `cli ground`**
  unless Anish asks for that run. They spend money — dollars, not cents, for
  a few hundred postings (§7) — and the first three write corpus-wide. The
  11:30 cron no longer scores (§7). `CLAUDE.md` has the full rule.
- No LinkedIn/Indeed scraping, no CAPTCHA bypass, secrets stay in `.env`.

---

## 7. Cost and billing

**Current, as of 2026-08-31 — everything below runs on Vertex, billed to the
GCP billing account where the credits live.**

| Work | Model | Notes |
|---|---|---|
| Stage B scoring | `gemini-2.5-flash` | escalates on a 5–7 score |
| Escalation | `gemini-2.5-pro` | 67–84% of a run in practice, see below |
| Grounding | `gemini-2.5-flash-lite` | caches nothing on Vertex; irrelevant, no long prefix |
| Cover letter prose | `gemini-2.5-flash` | was `3.5-flash`, which Vertex does not serve |
| Apply-session drafts (7A) | `gemini-2.5-pro` | `JOBPIPE_DRAFT_MODEL`; capped per session. Not yet run live |
| `measure`, `status`, `sync`, tests | — | free |

All four defaults are backend-keyed and resolve inside the Vertex provider's
`KNOWN_MODELS`. That is not cosmetic: three separate defects came from a value
being resolved without asking which backend was about to receive it —
grounding (`574fdd4`), the rubric's field descriptions (`015236f`), and the
prose model (`1bb0f0a`).

**Escalation is not a rare second opinion.** It fired on 67% of a hand-scored
set and 84% of the live queue, because scores cluster in the 5–7 band. 2.5 Pro
is the expensive rung, so it dominates the cost of any scoring run — budget
from the escalation rate, not the posting count.

**Measured on 2026-10-03, the first large run on 2.5:** 502 postings used
10.05 million input tokens (4.0 million read from cache) and 162,000 output
tokens, with 210 escalated to Pro — about 20,000 input tokens per posting.
That is several dollars at list prices; the exact charge is on the GCP
billing page and has not been read. The "~85¢ full re-score" figure this
project quoted until then was a 3.x-era number and under-priced that run by
several times (`HANDOFF.md` §5). **Price a run from tokens, never from that
figure.**

**2.5 Pro thinks, and the thinking is billed and capped.** Pro cannot turn
thinking off (§7, the `thinking_budget=0` 400). Its thinking tokens bill at
the output rate, yet jobpipe's ledger reads `candidates_token_count`, which
leaves them out. Three consequences, none measured yet:
- every Pro cost jobpipe has reported is low, escalation included;
- the apply session's panel estimate is low too, though its docstring says
  it errs high;
- thinking counts against `max_output_tokens`, so the drafting ceilings
  (2,048 tokens for an answer, 4,096 for a letter) may truncate drafts.

`HANDOFF.md` §9 lists these as risks for the first live session. Read the
GCP billing page, not the panel.

The historical figures below were measured on the Developer API and 3.x models
and are kept for comparison only: daily scoring ~2.5¢/day at ~0.18¢/posting,
bullet selection ~0.4¢/resume at 79% cache hit, letters ~2¢ each.

Total Gemini spend across the first 22 applications, to 2026-08-31:
**~53¢.** Prompt caching does
most of the work — the ~10,204-token profile prefix is byte-identical on every
call and hits at ~40–79%.

The Pro subscription and the API are separate billing systems. A cron job
cannot draw on a Pro plan; conversely, running the daily loop through a
subscription-billed agent would consume the shared usage pool and cause
throttling everywhere else. Watch for a stray `ANTHROPIC_API_KEY` in the shell
environment — it silently bills Claude Code to the API.

### The Vertex question — SETTLED 2026-08-31

**Answered: the "Gemini 2.x SKUs" label is literal, and 2.5 was adopted.**
The Gen AI SKU group contains no Gemini 3.x SKUs at all, which corroborated
the live probe from a second direction, so the Billing cost table was never
needed to decide it. The migration is complete: `JOBPIPE_GEMINI_BACKEND=vertex`,
scoring on `gemini-2.5-flash`, and recalibration done — rank agreement +0.808
against +0.258 for the 3.1 baseline, so the "would need full recalibration"
cost priced below was paid and came out ahead.

Two probe findings below were overturned by later measurement. **Implicit
caching does fire on 2.5 Flash** against the real profile prefix (98% when it
lands), so the explicit-cache 403 stays deliberately unsolved. And
`thinking_budget=0` is accepted on 2.5 Flash and Flash Lite but **rejected by
2.5 Pro** — the probe only covered the two candidates for the scoring call,
not the escalation model, and that gap hid a broken escalation ladder for
three calibration runs. `PHASE5_PLAN` §3.3 has the full account.

The original analysis is kept below because the reasoning is still the right
shape for the next billing question, not because its conclusions are current.

### The original analysis (2026-08-29, superseded above)

Google Cloud promotional credits **cannot** be spent on the AI Studio
Developer API key in `.env`; for accounts created after 2 March 2026 they are
excluded from that billing track entirely. Vertex AI — renamed *Gemini
Enterprise Agent Platform* — bills to the GCP billing account where the
credits live. Same models, different billing door.

Setup completed on 2026-08-29: `aiplatform.googleapis.com` enabled and
`roles/aiplatform.user` granted to
`sheets-writer-249@gen-lang-client-0077852111.iam.gserviceaccount.com`.
(The role's display name is now "Gemini Enterprise Agent Platform User" and
collides with unrelated *Gemini Enterprise* roles — filter the picker on the
immutable id `aiplatform`, or use Cloud Shell, which never consults a display
name.)

Probe findings, verified live, not read from docs:

| Model | Endpoint | Implicit cache | Credit SKU |
|---|---|---|---|
| `gemini-3.1-flash-lite` | **global only** (404 regionally) | 8,054 / 8,187 tokens — 98% | 3.x not listed |
| `gemini-2.5-flash-lite` | global + us-central1 | **none across 6 calls** | ✓ "Gemini 2.x SKUs" |

Three further facts the probe settled:

1. `interactions.create` is **Developer-API only**. Vertex returns
   `Unsupported model interaction`. A port needs a second provider class on
   `models.generate_content` + `GenerateContentConfig`, with different usage
   field names (`prompt_token_count`, `candidates_token_count`,
   `cached_content_token_count`). ~80 lines — what the provider abstraction
   was built to absorb, but not the ten-line swap first estimated.
2. Explicit caching 403s on `aiplatform.endpoints.predict` — different
   resource path, more IAM. Irrelevant while implicit works on 3.1.
3. `gemini-2.5-flash-lite` **resolves on Vertex** despite being dead to new
   AI Studio keys. The constraint that pushed the project off 2.5 and the
   constraint limiting the credit to 2.x may cancel out.

**The decision then pending** — whether the credit's "Gemini 2.x SKUs" label
is literal. Answered above: it is. The two branches below were the live
options at the time; the second one is what happened.

- Credit covers 3.x → use `gemini-3.1-flash-lite` on Vertex. Same model, no
  recalibration, caching intact, credit pays. Unambiguous win.
- Literally 2.x only → free but **uncached** on a model whose scoring would
  need full recalibration (MAE 1.21, bias +0.71, recall 100%/precision 59%
  were all measured on 3.1). Against 2.5¢/day, paying card for cached 3.1 may
  genuinely beat burning credit on uncached 2.5.

If the 3.x route is taken, `GOOGLE_CLOUD_LOCATION=global` is **required**, not
a preference.

Independent of billing: Vertex carries enterprise data-governance terms. The
prompt is a complete personal career profile including contact details and
visa status. That is a real improvement over a free-tier data policy that
permits training on submitted prompts.

---

## 8. Verified defects and open questions

Everything here was reproduced on 2026-08-29. **Status as of 2026-08-31:**

| | |
|---|---|
| 8.1 date time-bomb | **fixed** — Slice 0, `8299bdf` |
| 8.2 `measure` slot labels off by two | **fixed** — Slice 0, `579c072`, confirmed on real output |
| 8.3 zero edit distances | **fixed** — `copy_for_editing`, now n=2 |
| 8.4 applications with no documents | open, historical; nothing new depends on it |
| 8.5 holdout contradicts the headline | **superseded** — see the note below |
| 8.6 grounding not independent of the writer | **fixed by inversion** — slice 4: Claude writes, `check_grounding` routes to Gemini |
| 8.7 smaller confirmed items | mostly closed in Slice 0; `title_exclude 'lead'` audited and kept, `63fa270` |
| 8.8 already done but not recorded | still accurate |
| 8.9 posting text frozen at first sight | **fixed** 2026-11-04, `HANDOFF.md` §4 |
| 8.10 `measure` ignores `letter_required` | **fixed** 2026-11-05 — headline letter-required only, the rest counted beside it, split by writer |

Defects found on 2026-10-03 are in `HANDOFF.md` §4, not here: closed rows on
the main tab, eight boards returning nothing, and Barclays titles Stage A
lets through.

**8.5 deserves a specific note**, because its conclusion was inverted twice.
It reported the model over-scoring and recommended tightening. Two attempts to
act on that — calibration examples and a scope ceiling — both improved the
number they targeted and **degraded rank agreement**, which is the metric the
sheet actually uses. Both are off by default with their mechanisms kept and
tested. The lesson, recorded in `HANDOFF.md` §5: MAE and bias measure a
decision the sheet does not make. Do not reopen 8.5 without reading that
first.

### 8.10 `measure` counts every letter, whatever `letter_required` says

Found 2026-11-05 while remeasuring §2. When an application is recorded
without `letter_required`, `advance_status` reminds that "measure reports on
letter-required applications only". `HANDOFF.md` §2 also says the runner's
letters "are not counted" because the flag is false. **Neither is true.**
`measure.py` never reads `letter_required`: it measures every letter whose
`.sent.md` exists.

So `measure` reports n=7, mean 0.009. Five of the seven are at exactly
0.000: Sentry and Vapi (`letter_required=0`), Scale AI (`=1`), Sierra and
Chime. The "why this role" slot reads 0.018 across six letters, where
the one assembled letter that was certainly edited put it at 0.110. That
open question (`HANDOFF.md` §3) now looks answered, and it is not: it
has been averaged down by letters that may never have been edited, or
were edited in the form where the `.sent.md` cannot see it. That is §8.3's
failure (a zero recorded as data) arriving by a different road.

**Resolved the same day, by Anish's answers.**

- The five zeros are real. He read each letter and sent it unchanged, so
  0.000 is a true "sent as drafted". He also said that if Gemini drafts
  in the same template, that is good enough. It does: `draft.draft_letter`
  uses `letters.py`'s prompt, his voice exemplars and banned phrases, and
  returns the four body paragraphs `save_cover_letter` assembles.
- **Both fixes, as he asked.** `measure.collect` reads each letter's
  writer (`documents.model`) and its `letter_required` (the latest
  `applied` event that carries the flag). `summarise` puts only the
  letter-required ones in the headline: n, mean, best, worst, slots and
  variants. The not-required and never-recorded letters are reported
  beside it with their own n, mean and count of zeros, and every letter is
  split by writer with that writer's letter-required figures. The CLI, the
  MCP tool and the workbench page all show the three parts.

Read on real data after the fix: headline n=1 (Scale AI, 0.000). Not
required: 4, all 0.000. Never recorded: 2, mean 0.032. By writer:
`claude-mcp` n=5, mean 0.000; `cowork-handwritten` n=2, mean 0.032.

**The `why this role` question is still open, and now visibly so.** Its
0.110 came from PostHog, one of the two letters whose application was
never recorded, so it sits outside the headline until it is. Recording
PostHog (10414) and Utah (16669) as applied, with the true flag, is
Anish's to confirm, because only he knows whether those forms required
a letter.

### 8.1 Ten tests fail — a date time-bomb, not a product bug

`tests/test_sheet_rows.py:214` hardcodes `seen="2026-08-13T00:00:00+00:00"`
and `get_sheet_rows` uses `max_stale_days=7`. Every seeded row is now 16 days
stale and filtered out before the assertions run. Fix: `seen=db.utcnow()`.
One line. All 10 failures are this.

### 8.2 `measure`'s per-slot labels are off by two

`SLOT_NAMES` assumes paragraph 0 is "why this role". `letters.assemble()`
emits seven blocks: `[0]` name/contact/date header, `[1]` salutation,
`[2..5]` the four body paragraphs, `[6]` sign-off. So `cli.py:718` and `:738`
label the header as "why this role" and the real closing as "slot 5". The
docs call the per-slot number "the number that says which instruction is
wrong" — right now it names the wrong instruction. No data is corrupted yet
because there is no data (§8.3), but it misdirects the moment there is.

### 8.3 Edit distance has zero data after 22 applications

This is the single most consequential gap in the system.

- 24 letter rows, **0 with `edit_distance`**.
- **No `.sent.md` file exists anywhere in `output/`.**
- Document 26 (Abridge) records a `sent_path` of
  `cover_letter.sent.md` in its folder — the file is not there.
- `sierra-software-engineer-agent-tech-media-telecom/` holds
  `Anish_Naik_Cover_Letter_Sierra_TMT.pdf` and **no `cover_letter.md`** —
  the destroyed baseline the docs describe, still sitting there.
- Three other folders (drata, nuro, verkada) also have no letter.

The copy-before-editing step has failed every single time it was needed:
**22 for 22**. This is a workflow failure, not a code failure, and it is what
blocks §7 of the Phase 2 spec. It is the strongest argument in §9.

### 8.4 Seven of 22 applications have no pipeline documents at all

Both Perplexity roles, LangChain SF, Sierra Financial Services, Cohere Applied
AI, and both Notion roles have no folder and no `documents` row. Worth knowing
before drawing any conclusion from a number computed over "applications sent"
— including `POST_PHASE2_FIXES.md` §4's target, which reads as met at 22.

### 8.5 The holdout calibration contradicts the headline number

`PHASE1_SUMMARY.md` reports recall 100% / precision 59%.
`config/calibration_holdout_result.json` — the independent set — reports
`recall 0.0`, 2 false negatives, 0 true positives at threshold 8, on n=19 with
only 2 positives (fit MAE 1.158, apply MAE 1.789). Both misses are
diagnostic: Scale AI "AI Advisory Consultant" (human 6, model 1 — the
`non_technical` short-circuit) and Ramp "Applied AI Engineer, Fullstack"
(human 8, model 3). Reading all 116 `non_technical`-labelled titles shows the
classifier is mostly right, so the rubric text is correct and the model failed
to follow it on that instance.

### 8.6 Grounding is not independent of the writer

`grounding.check` resolved to `default_model()` — the same Gemini family that
wrote the letter. **Closed 2026-09-01 by inversion, not migration.** Under
slice 4 Claude writes the prose and `check_grounding` routes to Gemini on the
server's own credentials, so writer and checker are different model families
and the session never sees a Gemini key. `POST_PHASE2_FIXES.md` §2.2 is
satisfied without the migration it proposed.

The second reason it stays off-session is not in §9: `get_posting` pulls
untrusted third-party text into a session that holds write tools. The resume
path is immune — a posting cannot argue a client into a bullet that does not
exist — but the letter path is free prose, and an independent checker is the
only thing between a badly-worded or hostile description and a claim in a
letter.

### 8.7 Smaller, confirmed

- `runs` is empty and `cli stats`' recent-runs section is permanently blank.
  `run_daily.py` is the only caller of `db.start_run`/`finish_run`, but
  `scripts/daily.ps1` invokes `python -m jobpipe.cli daily`. `run_daily.py` is
  effectively orphaned.
- Temporal's Greenhouse token `temporaltechnologies` 404s every morning.
- `google-genai` (2.18.0, installed) is **missing from `requirements.txt`** —
  the one runtime dependency not declared.
- `master_profile.md` is internally inconsistent: §10 now says "willing to
  relocate anywhere in the US, without qualification", while §11's calibration
  example 5 still docks a role because "the location is wrong" — and that
  example is in the scoring prompt.
- `title_exclude` still contains `lead`, which `POST_PHASE2_FIXES.md` §1.1
  action 2 asked to revisit now that co-founder experience is in the profile.
- Six `.fuse_hidden*` files in the root are orphaned SQLite `-shm` files.
  Harmless.

### 8.8 Already done but not recorded

`POST_PHASE2_FIXES.md` does not say so, but these are complete: §1.1 (profile
§1 and §6 rewritten, `gt-ownership` bullet added), §1.2 (location filter
widened US-wide — the code comment records it was discarding 272 US roles),
§3.1 (tiers ranked 8/93/42), §5.3 (letter length target raised to 340–430),
and addendum §6 (SmartRecruiters fetcher shipped).

---

## 9. Phase 5 — the final version: jobpipe as an MCP server

> **SUPERSEDED by `PHASE5_PLAN.md`.** That document was written from this one
> and settles every decision left open here. The architecture below survived
> intact; the open questions did not, and several were answered the other way.
> Where the two disagree, the plan wins. Kept because the reasoning that
> produced the architecture is not repeated there.
>
> **Progress as of 2026-09-01:** Slice 0 complete. Track A complete, all six
> steps — Vertex migration, rubric through `2026-08-31.4`, recalibration, and
> the queue backfill. **Track B complete, all four slices**: five read-only
> tools, `copy_for_editing` + `advance_status`, `render_resume` with the seven
> reviewed orderings and the header generator, and `save_cover_letter` +
> `check_grounding` shipped together. Ten tools on the surface then.
>
> **Track C built 2026-09-10** — `form_answers`, `save_short_answer`, the
> answers file and the form rules; thirteen tools with `prepare_letter`. One
> supervised fill is done
> (LangChain, submitted), with two more before the rules are settled.

The end state is that the human work left in this project — reviewing a resume
against the real posting, writing and tightening a letter, deciding what to
apply to — happens in one conversation with Claude in Cowork, against tools
that make the workflow steps impossible to do wrong.

### 9.1 The thing that actually changes: control inverts

**An MCP server does not call a model. The client does.**

Exposing jobpipe over MCP does not swap Gemini for Claude behind
`providers/`. Today `letters.generate_letter` calls out to a model; under MCP,
Claude-in-Cowork reads the posting and calls **in** to `save_cover_letter`.
`jobpipe/providers/` is not the seam here — it stays, for whatever the server
still calls on its own.

That distinction decides which model does what:

| Work | Today | Under MCP | Gemini key needed? |
|---|---|---|---|
| Bullet selection | Flash-Lite, ~0.4¢ | Claude picks ids, server validates | No |
| Cover letter prose | 3.5-flash, ~2¢ | Claude writes, server assembles | No |
| Grounding check | Flash-Lite | **Must stay off-session** | **Yes** |
| Daily scoring (11:30) | Flash-Lite, ~2.5¢/day | Nothing drives an MCP client at 11:30 | **Yes** |

So the Gemini key survives — for a smaller and better reason than it exists
for today.

### 9.2 Grounding must not move into the session

Writer and checker are already the same model family, which
`PHASE2_SUMMARY.md` lists as a known limit. If Claude writes the letter *and*
checks it in the same session, the blind spot is not merely preserved — it
deepens. Same model, same context, same reasoning that produced the claim,
now asked to audit it a few turns later. It will rationalise.

The fix is free and already built: `grounding.check()` takes a provider
argument. Expose it as a tool that routes explicitly to Gemini. **Claude
writes, Gemini checks.** `POST_PHASE2_FIXES.md` §2.2 is satisfied by
inversion instead of by the migration it proposes — and if the Vertex credit
lands, at no marginal cost.

### 9.3 The scoring cron cannot move, and should not

There is no session at 11:30, and an MCP server is inert without a client.
Scheduling a Cowork agent instead is ruled out by `PROJECT.md` deliberately:
running the daily loop on a subscription "would consume the shared usage pool
and leave me throttled for everything else, including the job search itself."
That guardrail was written for exactly this proposal and it still holds.
Scoring is ~10 postings/day and the cron works. **Keep Phase 1 on Gemini,
unchanged.**

### 9.4 Tool surface

```
list_queue(status)            postings at a status, tier then apply_score
get_posting(id)               full description
render_resume(posting_id, bullet_ids, variant, skill_order, posting_skills)
                              -> validates ids against the bank, runs the
                                 one-page loop and the ATS parse check,
                                 returns `dropped` — the review signal
save_cover_letter(posting_id, {why_this_role, primary_evidence,
                               secondary_evidence, closing})
                              -> server assembles date/salutation/sign-off,
                                 runs banned_used(), stores content in
                                 `documents`
check_grounding(posting_id, letter_text)    -> routes to Gemini, fail-closed
copy_for_editing(posting_id)  the `cp`, done right
advance_status(posting_id, status)
status() / measure() / stats()
```

Resources — loaded into context once rather than per call: the bullet bank,
`master_profile.md`, the two voice exemplars, and the letter constraints
(banned phrases, `CLOSING_SHAPE_RULE`, the 340–430 word target).

### 9.5 The rules the server must enforce

1. **Keep the fabrication firewall server-side.** `Bank.select` raises on an
   unknown id, so `render_resume` cannot be talked into a bullet that does not
   exist. The enum-in-schema trick does not transfer to an MCP client, but the
   server-side re-check — which `bullets.py` already performs precisely
   because "a provider that ignores enums should fail loudly" — carries the
   whole guarantee alone. Same for `banned_used()`: checked, never instructed.
2. **Do not expose `score`, `calibrate`, or `daily`.** They are on the
   do-not-run list because of the prepaid balance. Putting them behind a tool
   call hands that trigger to an agent. CLI-only.
3. **Encode the guardrails as absence.** No submit tool. No send-email tool.
   A tool that does not exist cannot be argued into existing. This is
   materially stronger than a prompt instruction and is the main reason the
   MCP shape is safe for this project.
4. **Decide the baseline rule up front.** If Claude revises a letter across
   three turns, is the `measure` baseline the first `save_cover_letter` or the
   last? Pick one and have the tool enforce it, or the exact ambiguity that
   `documents.content` was added to kill comes straight back.

### 9.6 Build order

1. **Settle the Vertex SKU question** (§7). It changes nothing structural, but
   it decides whether the Gemini side runs on credits or on a card, and it is
   answered by looking at one billing page.
2. **Fix §8.1 and §8.2 first.** Both one-liners. The second silently
   misdirects the moment the first letter is actually measured — which §9 is
   designed to cause.
3. **Split `pipeline_tailor.generate_resume`.** It currently bundles selection
   and render. The pieces are already separable (`bank.select`,
   `resume.render`, `db.insert_document`), so this is a decomposition, not a
   rewrite, and it is what lets `render_resume` take ids from a client.
4. **Write `jobpipe/mcp_server.py`** — stdio transport, thin. It imports
   existing modules and calls them. No logic moves. Add `mcp` and the missing
   `google-genai` to `requirements.txt`.
5. **Add `VertexGeminiProvider`** if §7 resolves that way — ~80 lines on
   `models.generate_content`, behind the existing interface, plus a
   `JOBPIPE_GEMINI_BACKEND=vertex` switch so a rollback is a config edit.
6. **Tests stay offline.** Call the tool functions directly; do not stand up a
   transport.
7. **Confirm how Cowork registers MCP servers in the current build** — in the
   app, not from memory. The architecture above does not depend on the answer.

### 9.7 What this is worth — and what it is not

**Not money.** 22 applications have cost ~53¢ of Gemini spend. Moving Phase 2
onto the subscription saves cents; if the Vertex credit lands it saves
nothing, because there is nothing left to save. **Build this for the workflow
reasons or not at all.**

The two real returns:

- **§8.3.** The copy-before-editing step has failed 22 times out of 22, which
  is why edit distance still has no data and why one baseline is already
  unrecoverable. `copy_for_editing` turns a discipline that has never once
  been followed into a tool call that cannot be done wrong. That is the whole
  case.
- **The review loop.** `CLAUDE.md` already specifies it: read the posting
  alongside the resume, name the required skills with no bullet, check whether
  the drop list contains something the posting explicitly asked for. That is
  judgment work on real artefacts, it is explicitly the human's job today, and
  it is the part Cowork is genuinely good at.

### 9.8 What does not change

Phase 1 stays a script on a cron, on Gemini. SQLite stays the source of truth.
The sheet stays a rendered view with a human-owned `status` column. Nothing is
ever deleted. Every guardrail in §6 survives the migration unchanged — several
get *stronger*, because under MCP they become tools that were never written
rather than rules a model is asked to respect.

### 9.9 A recurring lesson, now three deep

Google has renamed or withdrawn something between the docs and the live API
three times on this project: `gemini-2.5-flash-lite` (Phase 1),
`gemini-3.1-flash` (Phase 2), and the `roles/aiplatform.user` display name
(the Vertex work). In each case the docs were confident and wrong, and a
single live call settled it in seconds. **Verify against the API, not the
documentation** — and prefer immutable ids to display names.
