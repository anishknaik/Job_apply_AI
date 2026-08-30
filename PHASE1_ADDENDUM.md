# Phase 1 Spec — Addendum

> Read after `PHASE1_SPEC.md`. Where the two disagree, **this document wins**.
> Written after the first real run exposed problems the original spec didn't anticipate.

---

## 1. Corrections to the original spec

### 1.1 Build order — Lever is out, Ashby is in

The actual target list is 16 Greenhouse, 20 Ashby, **0 Lever**. The original build order
had Lever at step 4 and Ashby at step 10, which would deliver nothing at step 4 and leave
over half the companies unfetched until step 10.

**Revised:** Greenhouse → Ashby → SmartRecruiters. Drop Lever from Phase 1 entirely.
Curl the Ashby endpoint and confirm its response shape before writing the parser — it is
now the largest source and was the least verified.

### 1.2 `content_hash` must not include location

The original definition was `sha256(company + normalized_title + location)`. Both Ashby
and Greenhouse return **one entry per location** for a multi-location req, so a single
role open in SF/NYC/London/remote produces four rows with four different hashes. All four
survive dedupe, get scored, and land in the sheet.

**Revised:** `content_hash = sha256(company + normalized_title)`. Collapse locations into
a joined list field on the posting, and run location filters against that list.

**Verify:** pull one known multi-location req and confirm it collapses to a single row.

### 1.3 Stage A is under-filtering, not over-filtering

A random sample of 20 survivors contained three explicitly `Senior`-titled roles, six at
lead or manager level, and four non-technical roles (Deal Operations Analyst, Business
Partner Analyst, GTM Systems Engineer, Product Marketing Manager). Sixteen of twenty were
obvious rejects.

**Revised `title_exclude`:** restore `senior`, `sr`, `lead`, `manager`, `head of`,
`director`, `vp`, `principal`, `staff`.

**Use word-boundary regex, not substring matching.** Substring `intern` silently kills
"Internal" and "International"; substring `lead` kills "Leadership" and "Lead Generation"
in unintended ways. Match `\bintern\b`, `\blead\b`, etc.

**Keep description-level rules loose.** The years-of-experience check (`5+ years` and
above) stays a Stage A kill, but nuanced judgment about seniority belongs in Stage B,
which reads the full description.

### 1.4 Add a Stage A audit command

`cli audit` — group killed postings by `stage_a_reason` and print a histogram. This is the
diagnostic for both over- and under-filtering, and it is far faster than inspecting
individual companies. Run it after every filter change.

### 1.5 Add `cli verify-targets`

One request per `board_token`, printing HTTP status and returned row count, no parsing.
A wrong token returns empty rather than erroring, so a typo'd company silently never
appears. Run this **before** the fetchers are written.

### 1.6 First run is insert-only

The initial backfill is a few thousand postings, not a daily delta. The first run must
populate `postings`, run Stage A, and **score nothing**, so the Stage A output can be
audited before any inference spend.

---

## 2. Model and provider (supersedes the original Model Usage section)

The original spec named Claude Haiku with Sonnet escalation. Superseded.

**Use Google Gemini 2.5 Flash-Lite on the paid tier.** Roughly $0.30–$1.20/month for this
workload. The paid tier matters for a non-cost reason: Google's **free** tier terms permit
using prompts to improve their products, and the prompt contains a full personal career
profile. Adding billing switches the data policy as well as raising rate limits.

- Put the master profile at the **front** of the prompt as a static cached prefix; job
  description last. Caching cuts repeated input cost ~90%.
- Structured JSON output with the fixed schema, `temperature = 0`.
- Do not use free tiers (Groq, OpenRouter `:free`, Gemini free) as the primary path —
  model catalogs get pruned without notice and most train on submitted data.
- **Do not use DeepSeek for this.** Its policy trains on API inputs by default and stores
  data in the PRC. Cheapest option available, disqualified by the payload.

**Escalation path if Flash-Lite can't discriminate in the 6–9 band:** Gemini 3.1
Flash-Lite (~$1/mo) → Qwen3.5-Plus (~$1.50–2.50/mo) → Claude Haiku 4.5 (~$2.50–4/mo).
Escalate the model, not the provider.

Wrap all calls in a thin provider abstraction so a model swap is a config change.

---

## 3. Additions to Stage B

### 3.1 Archetype detection before scoring

**The most important change in this document.** The master profile declares AI/ML Engineer
and Data Scientist as co-primary lanes. A single rubric spanning both is why scores drift
toward 6.

Classify each posting into an archetype first, then score against lane-specific criteria:

```
archetype: ai_ml_engineer | data_scientist | swe_generalist |
           ml_platform | analytics | non_technical
```

Cheap first pass on title plus the first ~500 characters of the description. Add
`archetype` to the Stage B output schema and branch the rubric on it. `non_technical`
short-circuits to a score of 1 without a full scoring call.

### 3.2 Legitimacy scored separately from fit

A new field that **never** contributes to `overall`:

```
legitimacy: ok | suspicious | likely_ghost
legitimacy_reason: str
```

A legitimate poor-fit role and a fake perfect-fit role need different handling, so keep
them on separate axes rather than collapsing legitimacy into `dealbreakers`.

### 3.3 Ghost-job detection via repost frequency

Distinct from dedupe. Dedupe collapses reposts; this **counts** them. If the same
`content_hash` has appeared 3+ times in 6 months, the req is not getting filled — set
`legitimacy = likely_ghost` and deprioritize regardless of fit score.

Requires the full posting history the schema already retains.

### 3.4 Sponsorship filter — verify by hand before trusting

Currently the highest-impact rule in the system. On STEM OPT against a target list where
new-grad reqs are scarce and often explicitly non-sponsoring, this filter removes a large
share of otherwise-strong roles.

A false positive here is invisible and permanent. **Check its output against ten postings
read manually before relying on it.**

---

## 4. Additions to the pipeline

### 4.1 Liveness verification

ATS feeds routinely retain closed postings. Add an optional Playwright pass that checks
whether a posting is still live and drops expired ones before they reach the pipeline.

Constraints: run **only** against new postings, **after** dedupe, sequentially. This
bounds the cost — it is the difference between a cheap check and a scraping job.

### 4.2 Status taxonomy

Lift `templates/states.yml` from the career-ops repo (MIT) rather than inventing a status
vocabulary and migrating it later. The `status` column is currently undefined.

### 4.3 Per-channel advance rates

Once `status` is populated, track which ATS source produces interviews. Phase 5 work, but
design the schema for it now so no migration is needed.

---

## 5. Calibration protocol (replaces the original approach)

The first calibration set was unusable: 16 of 20 postings scored ≤3, with nothing in the
8 band. A rubric fitted to that set learns to separate a 1 from a 2, when the pipeline's
actual job is separating a 6 from an 8.

**Correct sequence:**

1. Fix Stage A (§1.3), re-run, audit the histogram (§1.4).
2. Confirm the survivor count drops. 172 → 40–60 is the *expected* outcome, not a problem.
3. Re-sample 20 from the corrected survivor pool.
4. Hand-score them **personally**. Do not use scores generated by any model that has read
   the master profile — the pipeline reads the same document, so agreement between them
   measures nothing. Model-generated scores may be used as a first pass to react against,
   never as ground truth.
5. Report **three** metrics, not one:
   - MAE against hand scores
   - Signed bias (systematic over- or under-scoring)
   - **Spread** — standard deviation and range. A 0.8 MAE across a 3-point range is a
     useless ranking. This check matters more than MAE.
6. Re-run calibration monthly, and after any rubric change.

---

## 5A. Reference implementation: career-ops (MIT)

`github.com/santifer/career-ops` is a mature open-source job-search system (63.5k stars,
~1,200 commits, MIT licensed). It solves many of the same problems and several of its
components are directly reusable. **MIT permits copying freely; attribution is only
required if this project is ever distributed.** Its name is separately trademarked, which
is irrelevant for private use.

Browse the codebase at `deepwiki.com/santifer/career-ops` — faster than reading GitHub
file by file.

Architecture note: career-ops is agent-driven and session-based (you paste a URL into an
AI CLI). This project is a deterministic cron script. Do not adopt its agent-per-
evaluation model, its 1.0–5.0 scale, or its markdown-first tracker. Its **SQLite index
over the markdown tracker** is closer to this design than it first appears.

### 5A.1 Lift these — highest value first

**`providers/*.mjs` — the single most valuable asset in the repo.**
`scan.mjs` auto-loads every module in that directory and calls each one's `detect()` in
order. ~55 modules cover Greenhouse, Ashby, Lever, Workday, BambooHR, Breezy, Recruitee,
Comeet and others. Each encodes one ATS's endpoint URL pattern and JSON field mapping.

This is the "curl it first and confirm the response shape" work from §1.1 — already done,
in production, against thousands of real postings. JavaScript, but URL patterns and field
mappings port directly to Python.

**Read their SSRF hardening before writing any fetcher.** A release note records
"providers: harden lever/ashby/workday against SSRF via redirect" — their fetchers follow
redirects, and a compromised or malicious board can redirect an outbound request somewhere
it should not go. This project has the same exposure and no mitigation.

**`verify-portals.mjs`** — an ATS slug validator. This *is* the `cli verify-targets`
command specified in §1.5, already written. Port it rather than reimplementing.

**`templates/portals.example.yml`** — the header comments document the API pattern for
each supported ATS (e.g. Comeet:
`https://www.comeet.co/careers-api/2.0/company/<uid>/positions?token=<token>`). Use as an
endpoint reference, and mine the company list to expand `targets.csv`.

**`gemini-eval.mjs`** — the standalone no-CLI evaluation path: reads a key from `.env`,
calls Gemini, parses the result. Directly relevant now that §2 settles on Gemini. Take
the API call structure and the JSON-response handling.

**`modes/oferta.md`** — the scoring rubric prompt, refined across 740+ real evaluations.
The most valuable non-code artifact in the repo, and language-agnostic, so it is a
straight lift. Adapt the A–F block structure to the sub-dimension schema in §3; do not
adopt the 1.0–5.0 scale.

**`openai-tailor.mjs`** — headless CV tailoring for the no-agent path. Their closest
analogue to this project's architecture. The right reference for Phase 2.

**`templates/cv-template.html` + `fonts/`** — working ATS-optimized CV template rendered
to PDF via Playwright. Pure HTML/CSS, drop-in, restyle freely. Most of Phase 2's PDF work.

**`templates/states.yml`** — canonical status taxonomy (see §4.2).

### 5A.2 Read, then reimplement in Python

- `dedup-tracker.mjs` — dedupe logic
- `normalize-statuses.mjs` — status normalization
- `verify-pipeline.mjs` — integrity checks
- `detect-reposts.mjs` — ghost-job detection (§3.3)
- `followup-cadence.mjs` — fills the empty `followup_due` column
- `analyze-patterns.mjs` — per-channel advance rates (§4.3)
- `DATA_CONTRACT.md` — their schema contract; useful structural reference
- `docs/SUPPORTED_JOB_BOARDS.md` — table of all 55+ providers

### 5A.3 Do not lift

- **Federal-board providers, including the proposed USAJOBS one.** Federal civil-service
  postings almost universally require US citizenship. On STEM OPT these are wasted scans.
- The agent-per-evaluation architecture — incompatible with an unattended cron.
- The 1.0–5.0 scale — the 0–10 sub-dimension schema is what makes the calibration
  harness in §5 possible.
- Their markdown-table tracker as primary storage. SQLite stays the source of truth.

---

## 6. Revised build order

1. Clone career-ops for reference (§5A). Read `providers/greenhouse.mjs`,
   `providers/ashby.mjs`, and the SSRF redirect hardening before writing any fetcher.
2. `cli verify-targets` — port `verify-portals.mjs` (§5A.1), validate all 36 board tokens
3. SQLite schema + `db.py`
4. Greenhouse fetcher → DB, run twice, confirm zero dupes
5. Ashby fetcher — port field mappings from `providers/ashby.mjs`
6. Dedupe with corrected `content_hash` (§1.2), verify on a multi-location req
7. Stage A filters with word-boundary regex (§1.3) + `cli audit` (§1.4)
8. **Insert-only backfill run** (§1.6) — audit histogram, score nothing
9. Archetype classifier (§3.1)
10. Stage B scoring on Gemini Flash-Lite with caching (§2); adapt the rubric from
    `modes/oferta.md` (§5A.1)
11. Re-sample and hand-score calibration set (§5)
12. `cli calibrate` reporting MAE, bias, and spread
13. Sheets sync using lifted status taxonomy (§4.2)
14. Cron
15. Liveness verification (§4.1)
16. Ghost-job detection (§3.3) — reference `detect-reposts.mjs`
17. SmartRecruiters fetcher, then Firecrawl long-tail

---

## 7. Unchanged from the original spec

Still binding, and worth restating because they are the constraints most likely to erode:

- No LinkedIn or Indeed scraping
- No automated email sending — drafts only
- No automated application submission
- Never delete rows from `postings`
- Follow redirects safely in all fetchers (SSRF, §5A.1)
- One company failing must never abort a run
- SQLite is the source of truth; the sheet's `status` column is human-owned and never
  overwritten by a sync
