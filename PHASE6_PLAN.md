# Phase 6 — the apply runner

Written 2026-11-04. Read `PROJECT.md`, `CLAUDE.md` and `HANDOFF.md` first;
this plan changes one guardrail in them and keeps every other one.

## What is being built

A local runner that takes a posting from the queue, fills its Greenhouse or
Ashby application form in a browser of its own, and then either submits it
or leaves it for Anish to review — decided by a rule in code, never by a
model. The web workbench starts runs and shows their reports. An AI browser
agent for other forms comes later (§8).

## 1. The guardrail change — Anish's decision, 2026-11-04

`PROJECT.md` said "No fully automated application submission — human clicks
submit". Anish has changed it to:

> **Auto-submit only where nothing checks for bots. Everywhere else the
> runner fills and stops, and Anish reviews and submits.**

Everything else in `PROJECT.md` stands, unchanged:

- **No CAPTCHA or bot-detection bypass.** Detection is used for one thing
  only: deciding to stop. No stealth plugins, no fingerprint spoofing, no
  solver services, no editing or removing a challenge element, no retry
  designed to slip past one.
- **Never fabricate.** Every value traces to `form_answers`,
  `master_profile.md` or `bullets.yaml`, through the tools that already
  enforce it.
- **Never fill** salary, SSN or any government id, payment details, or
  "I certify…" / signature boxes. A form that *requires* one of these is
  not auto-submittable — it goes to review. That is a consequence of the
  rule above, not a new one: an attestation is Anish's signature.

**Measured on 2026-11-04:** the Ashby form page loads reCAPTCHA, and 6 of 8
Greenhouse boards sampled load it too (static HTML; Alloy and Betterment did
not). So auto-submit will be the minority path. Build for that honestly: the
review path is the main product, auto-submit is the exception.

## 2. Architecture

```
workbench (127.0.0.1)  ──enqueue──▶  runner (local process)  ──▶  Playwright
   │  report, screenshots ◀──────────────┘   │                    own profile
   │                                         ▼
   │                         jobpipe tool bodies (mcp_server.py)
   │                         form_answers, render_resume, prepare_letter,
   │                         check_grounding, advance_status
   ▼
jobpipe.db: apply_runs (new), status_events, documents
```

- The **runner** is a separate local process (`python -m jobpipe.apply`).
  The web app never drives a browser and never submits: it enqueues a run
  and renders its report. `tests/test_web.py`'s "no page says submit" rule
  stays true for the web layer.
- **Its own browser profile** (`.jobpipe-browser/`, gitignored), never
  Anish's Chrome, headed by default. It holds no logins; Greenhouse and
  Ashby forms need none.
- **Structured form data first, DOM second.** Greenhouse publishes each
  form's questions as JSON:
  `boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}?questions=true`
  returns `questions`, `location_questions`, `compliance` and
  `demographic_questions` with labels, types, required flags and options.
  Ashby embeds its form in the job page as `window.__appData`. Parse these
  into one `FormSpec`; use the DOM only to locate and fill the fields.
- **Nothing on the MCP surface changes.** No submit tool, no run tool.
  `TOOL_ALLOWLIST` stays as it is.

## 3. Modules (`jobpipe/apply/`)

| Module | Does | Pure? |
|---|---|---|
| `spec.py` | `FormSpec` / `Field` dataclasses; Greenhouse JSON and Ashby `__appData` parsers | yes |
| `resolve.py` | each `Field` → a value **and its source**, or a blocker. The safety core | yes |
| `signals.py` | bot-check detection on the *rendered* page | DOM in, verdict out |
| `fill.py` | Playwright: fill, upload, read back, screenshot | no |
| `decide.py` | `auto_submit` or `review`, with reasons | yes |
| `submit.py` | the one click, then confirmation detection | no |
| `runner.py`, `__main__.py` | queue, locking, `apply_runs` rows, CLI | no |

### resolve.py — every field gets a source or a blocker

| Field kind | Source | Otherwise |
|---|---|---|
| Name, email, phone, location, LinkedIn, GitHub, school, degree, graduation, work authorization, sponsorship | `form_answers` (profile §12) | blocker |
| Self-ID (gender, race, veteran, disability, optional ones) | `form_answers`, option text **exactly** equal to a stored entry | leave blank, note it; not a blocker unless required |
| Dropdowns generally | exact option-text match only | blocker |
| Resume | `render_resume` output for **this** posting | blocker |
| Cover letter | `prepare_letter` (text or `pdf=true`) — reads only the reviewed `.sent.md` | required → blocker if absent; optional → leave empty |
| Open-ended questions | a stored `documents` row for (posting, question) with a **passing** `check_grounding` verdict, typed exactly as stored; one paragraph if the box is single-line | blocker |
| Salary, government id, payment, attestation, signature | never | required → blocker; optional → empty |
| Start date, how-you-heard, preferred name | `form_answers`; empty → leave for Anish | required → blocker |
| Anything unrecognised | — | blocker |
| Hidden fields | never touched | — |

**The runner does not write prose in v1.** An open-ended answer comes from
the existing path — a Claude session's `save_short_answer` plus
`check_grounding` — or the posting goes to review with that question listed.
An optional Vertex drafting step (Flash writes, `check_grounding` checks) may
come later behind a flag, and **any posting answered by it is review-only**:
Gemini writing and Gemini checking loses the writer≠checker independence
`CLAUDE.md` relies on.

### signals.py — stop, never evade

Checked on the rendered page after load and again just before submit:
reCAPTCHA (`grecaptcha`, `g-recaptcha-response`, `recaptcha/api.js`,
`recaptcha/enterprise.js`), hCaptcha, Cloudflare Turnstile, Arkose/FunCaptcha,
any challenge iframe, and any visible "verify you are human" text. Any hit →
`review`. A miss is not proof of absence, so the confirmation step (below)
also treats an unexpected challenge as a stop.

### decide.py — auto-submit only when every condition holds

1. Auto-submit is enabled in **config** (`config/apply.yaml`, gitignored,
   default off) **and** for this run. No web route can turn it on — a page
   that can flip it can be POSTed by any site (see `web.py`'s origin checks).
2. No bot-check signal, before fill or before submit.
3. Zero blockers; every required field filled from a source in the table.
4. Every typed value read back equal to its source (SHA-256), every upload
   is this posting's file.
5. No required attestation, salary, government-id or payment field.
6. Every open-ended answer is a session-written, grounding-passed row.
7. The posting has no prior submit attempt and is not already `applied`.
8. Under the daily cap (`max_auto_submits_per_day`, default 5) and spaced
   (`min_minutes_between`, default 10).

Otherwise `review`, with the failing conditions listed in the report.

### submit.py — one click, no retries

Click the form's own submit button once. Then wait for the ATS's
confirmation (Greenhouse and Ashby each have a recognisable thank-you
state). Confirmed → `advance_status(posting_id, "applied",
letter_required=…, source="agent")`. Not confirmed, error shown, or a
challenge appears → record `submit_unconfirmed`, screenshot, **never click
again**, surface it in the workbench. A duplicate application is worse than
a missed one.

## 4. Data

- `apply_runs` table: id, posting_id, started/finished, mode (`dry_run`,
  `review`, `auto`), decision, reasons (JSON), report (JSON: field → value
  hash, source, status), screenshot paths, submit outcome. Append-only, like
  `status_events`.
- `states.yml`: add `ready_for_review` (filled, waiting for Anish) and
  `submit_unconfirmed`. `applied` still only after a confirmation page.
- `advance_status` accepts `source="agent"` the way it accepts `"web"`.

## 5. Workbench

- Posting page: **Fill application** (enqueues a review-mode run) and the
  last run's report — each field, its source, what was left blank and why,
  the decision and its reasons, screenshots.
- Queue: a **Ready for review** tab and a **Needs attention** tab
  (`submit_unconfirmed`, blockers).
- `ROUTE_ALLOWLIST` grows deliberately, with tests, by the enqueue and
  report routes only. No route submits, and no route changes `apply.yaml`.
- The review step itself: Anish opens the form in the runner's browser window
  (left open on the filled page) or re-runs fill in his own browser, checks
  it, and clicks submit; `advance_status` after the confirmation, as today.

## 6. Build order

| Slice | Delivers | Verified by |
|---|---|---|
| 0 | `PROJECT.md` and `CLAUDE.md` guardrail text per §1; `apply.yaml` example; `apply_runs` migration; states | tests; docs diff |
| 1 | `spec.py` parsers for Greenhouse JSON and Ashby `__appData` | saved fixtures of real public forms |
| 2 | `resolve.py` | table-driven tests, one per row of §3's table, plus every blocker |
| 3 | `signals.py` | fixture pages with and without each challenge type |
| 4 | `fill.py` against local fixture pages served by a test server | Playwright tests, offline; read-back hashes |
| 5 | `decide.py` | one test per condition, each failing alone |
| 6 | `submit.py` against a fake ATS fixture (confirm, error, challenge, timeout) | tests prove one click and no retry |
| 7 | runner, CLI (`python -m jobpipe.apply <posting_id> [--dry-run] [--auto]`) | tests; dry run is the default |
| 8 | workbench routes and templates | `test_web.py` allowlist and origin tests |
| 9 | docs: `HANDOFF.md`, `CLAUDE.md` "Filling an application form" | — |

**Live verification is local and supervised, never in the cloud:** first two
or three real forms in `--dry-run`, then review mode end to end with Anish
submitting, then the first auto-submit watched live. Track C's rule —
supervise the first few — applies again.

## 7. Cost

Phase 6 as above is **$0 in model calls** except `check_grounding` (a
fraction of a cent per answer, already the practice) and `render_resume` /
`prepare_letter` (~0.4¢ / ~2¢, already the practice). Playwright, the runner
and the workbench are free and local.

## 8. Later — the AI browser agent

For forms that are neither Greenhouse nor Ashby, or whose layout the
scripts cannot place: `browser-use` (MIT) driving Gemini on Vertex, or
Google's Computer Use reference agent (Apache-2.0) on
`gemini-2.5-computer-use` (billed on the 2.5 Pro SKU). Run locally — ADK is
free, but Agent Engine is paid past its free tier and cannot reach a local
browser anyway. **Review-only to begin with**: an agent that chose its own
clicks has not earned the auto-submit path the deterministic runner has.
The same `resolve.py` sources and blockers apply; the agent proposes fills,
the resolver approves them.

**Is it free on the GCP credit?** The libraries are free. The Gemini calls
bill to Vertex, which the credit already covers (scoring runs there today) —
including the computer-use model, though confirm on the billing page that
the credit applies to its SKU. The credit ends mid-November; after that,
Flash is cents per form and computer-use several times that. Do not use the
unpaid Gemini API tier: it may use content to improve Google's products,
and this agent would send the profile and self-ID answers.
