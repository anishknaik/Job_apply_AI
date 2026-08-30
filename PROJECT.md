# Job Search Automation — Project Overview

> Context document for Claude Code. Read this first for the *why*.
> `PHASE1_SPEC.md` is the current build target and contains the *what*.
> **Only Phase 1 is in scope right now.**

---

## What this is

A personal job search pipeline that automates the mechanical parts of applying — finding
relevant postings, scoring them for fit, tailoring documents, filling forms, and drafting
outreach — while keeping a human in the loop at the two points where automation actively
backfires.

It is not a mass-apply bot. The design assumption throughout is that **filtering quality
matters more than application volume**. A system that submits 400 unreviewed applications
produces a lot of motion and few interviews. A system that surfaces the right 30 roles and
removes the busywork around them is worth building.

---

## The four phases

| Phase | Adds | Automation level | Status |
|---|---|---|---|
| 1 | Discovery, scoring, tracking | Fully automatic | **In progress** |
| 2 | Resume + cover letter tailoring | Fully automatic, human reviews output | Not started |
| 3 | Application form fill | Semi-auto, human clicks submit | Not started |
| 4 | Recruiter research + outreach drafting | Drafts only, human sends | Not started |

Each phase is independently useful. Phase 1 alone saves hours per week. Do not begin a
phase before the previous one is running reliably.

---

## Data flow

```
targets.csv (150-250 companies)
      |
      v
[ATS JSON endpoints]  +  [Firecrawl for long-tail career pages]
      |
      v
normalize -> Posting dataclass
      |
      v
dedupe (source_id primary, content_hash for reposts)
      |
      v
Stage A: deterministic filters        -- kills ~70%, no LLM, no cost
      |
      v
Stage B: LLM fit scoring vs master profile   -- Haiku, escalate borderline to Sonnet
      |
      v
SQLite (source of truth)  ->  Google Sheet (human view, score >= 6)
      |
      v
   [Phase 2] tailored resume + cover letter
      |
      v
   [Phase 3] browser fills form, human reviews and submits
      |
      v
   [Phase 4] company research -> outreach draft in Gmail drafts, human sends
```

---

## Core design decisions and why

### Target ATS boards, not job aggregators

LinkedIn and Indeed are excluded deliberately. They block aggressively, their terms
prohibit scraping, and their data is stale relative to the source. Greenhouse, Lever,
Ashby and SmartRecruiters expose **public JSON syndication endpoints** — the same ones
that power the embedded job widget on a company's own careers page. No scraping, no HTML
parsing, no blocking. This is the single biggest simplification in the project.

Firecrawl exists in the architecture only for the long tail: companies running a custom
careers page with no standard ATS. Roughly 15% of the target list, and it will generate
most of the bugs. It is never the primary path.

### The master profile is the foundation

A single long document (3,000+ words) containing every project, metric, tool, and
responsibility I've had — far more than a resume would hold, including things I'd never
put on one. Stored in Drive, cached in the scoring system prompt.

Everything downstream inherits its quality. If it's a rewarmed resume, scoring is
mediocre and Phase 2's cover letters are worse. Length is a feature.

### Two-stage scoring

Stage A is deterministic filters — seniority keywords, location, posting age, dealbreaker
phrases. No model, no cost. Stage B is LLM scoring only on survivors.

Scoring 400 postings a day with a frontier model is wasteful when 280 of them can be
eliminated by an `if` statement. Tightening Stage A is the cheapest optimization
available and should always be preferred over changing models.

Scoring is broken into sub-dimensions (skills, seniority, domain) rather than one gestalt
number, with reasoning forced before the score. A single holistic score tends to cluster
at 6–8 and carries no information.

### Semi-automatic submission (Phase 3)

The browser fills every field and uploads the right documents, then **stops before the
final submit** for a ~20 second human review. This captures ~90% of the time savings
while avoiding two real problems: most ATS terms of service prohibit fully automated
submission, and an unreviewed application can misrepresent me in ways that are hard to
walk back.

Related: the pipeline does not bypass CAPTCHAs or bot-detection. Fully automated
apply flows hit these constantly, which is itself a signal the approach is wrong.

### Outreach drafts, never automated sends (Phase 4)

Cold-emailing scraped addresses in bulk was the original plan and was redesigned, for
three practical reasons:

1. **Deliverability.** Scraped lists carry high bounce rates and spam traps. A few hundred
   sends from a personal Gmail damages domain reputation — after which *legitimate*
   follow-ups to real recruiters start landing in spam. An expensive failure mode to hit
   mid-job-search.
2. **Legal exposure.** CAN-SPAM requires opt-out handling; GDPR applies to any EU
   recipient. Scraped individual work addresses sit in a genuinely gray zone.
3. **It doesn't work.** Recruiters read dozens of templated cold emails daily and spot
   them instantly. Generic blast response rates run 1–2%; researched, specific outreach
   runs 15–25%.

The replacement: per-company research (recent funding, product launches, the actual
hiring manager rather than a generic recruiter), address discovery via Hunter.io or
Apollo APIs rather than raw scraping, and a draft written to **Gmail drafts**. I skim and
send 5–15 a day. Ten minutes of human time, an order of magnitude better response rate.

**No phase of this project sends email automatically.**

---

## Tooling split

| Work | Tool | Billing |
|---|---|---|
| Building the pipeline | Claude Code | Pro subscription |
| Writing the master profile | Claude chat | Pro subscription |
| Scoring calibration | Claude chat / Claude Code | Pro subscription |
| Daily scoring loop | Anthropic API (Haiku, escalate to Sonnet) | API credits, pay-as-you-go |
| Phase 4 company research | Cowork | Pro subscription |

The subscription and the API are separate billing systems — a cron job cannot draw on a
Pro plan. Conversely, running the daily loop through Cowork on a schedule would consume
the shared subscription usage pool and leave me throttled for everything else, including
the job search itself. The split above is deliberate.

Watch for a stray `ANTHROPIC_API_KEY` in the environment — it silently causes Claude Code
to bill the API instead of the subscription.

Model IDs and pricing change; verify against current Anthropic docs rather than assuming.

---

## Architectural principles

**Phase 1 is a script, not an agent.** It must run identically at 6am every day with no
one watching. Deterministic control flow, explicit error handling, `temperature = 0`.
Agent-shaped tooling arrives in Phase 3 (browser) and Phase 4 (research), where the work
genuinely is open-ended.

**SQLite is the source of truth.** The Google Sheet is a rendered view. Never treat the
sheet as authoritative state, with one exception: the `status` column is human-edited and
must never be overwritten by a sync.

**Nothing is ever deleted.** Full posting history is what makes repost detection work.

**Partial failure is normal.** One company's endpoint returning garbage must never abort
a run. Log it, continue, surface it in the run summary.

**The schema is defined once, up front.** Phase 1 writes seven of the twelve sheet
columns; the other five exist from day one so later phases don't force a migration.

---

## Guardrails

These hold across all phases:

- No LinkedIn or Indeed scraping
- No automated email sending — drafts only, always
- No fully automated application submission — human clicks submit
- No CAPTCHA or bot-detection bypass
- No fabricated experience in any generated document; tailoring means selecting and
  reframing real material from the master profile, never inventing it
- Secrets in `.env`, never committed

---

## What success looks like

**Phase 1:** I open one sheet each morning with 5–20 scored roles and never visit a job
board. Scoring correlates with my own judgment closely enough that I trust the ranking.

**Full system:** applications go out with tailored documents in minutes rather than
hours, follow-ups fire on schedule instead of being forgotten, and outreach is specific
enough to actually get replies.

The metric that matters is interviews per hour spent, not applications per day.
