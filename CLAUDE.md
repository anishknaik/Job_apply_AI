# Working on jobpipe

A personal job-search pipeline for Anish Kumar Naik. Phase 1 (discovery →
scoring → sheet) and Phase 2 (resume + cover letter tailoring) are built and
running. Read `PROJECT.md` first — it holds the guardrails that apply to
everything here.

## What you are here to do

Help work through the application queue: generate documents, review them
against the real posting, edit the drafts, and record what happened. That is
judgment work on real artefacts. The pipeline itself is automated and does not
need you.

## Commands

```
python -m jobpipe.cli tailor --queued              # preview the queue, no spend
python -m jobpipe.cli tailor --queued --yes        # a resume per queued role
python -m jobpipe.cli tailor --cover-letter <id> --yes
python -m jobpipe.cli measure [--detail]           # edit distance, free
python -m jobpipe.cli status                       # where applications stand
python -m jobpipe.cli sync                         # mirror the sheet, free
python -m pytest tests/ -q                         # 557 tests, no network
```

Posting ids come from `postings.id` in `jobpipe.db`.

## Rules

**Never submit an application.** Fill a form if asked, then stop before submit.
Anish clicks it. This is a `PROJECT.md` guardrail, it is what most ATS terms
require, and the twenty-second human review is what stops a mis-tailored
application going out under his name.

**Never send email.** Drafts only, always.

**Never fabricate.** Every claim in a generated document traces to
`config/bullets.yaml` or `config/master_profile.md`. If a posting wants
something he has not done, the honest output is a document without it. If you
believe a claim is true but the profile does not support it, fix the profile
first — that has already happened twice and both times the profile was wrong.

**COPY a letter before editing it. Never rename, never edit in place.**

```
cp cover_letter.md cover_letter.sent.md     # then edit the .sent.md
```

The original is the baseline `measure` compares against. A baseline was
already destroyed by renaming instead of copying, and it is unrecoverable:
prose regenerates at 0.53 distance from itself at temperature 0, so a fresh
draft is not a substitute. Destroying it makes `measure` report model variance
as if it were human editing.

**Do not edit a `.sent.md` that predates the current draft.** Regenerate, copy,
then edit. `measure` refuses stale pairs and says so.

**Do not hand-edit `resume.pdf`.** It is assembled from the bullet bank, and
that traceability is the whole fabrication guarantee. If a resume is wrong, fix
the bank, a bullet's `rank`, or its `skills` tags — then regenerate.

## Do not run anything that scores

```
DO NOT RUN:  cli daily,  cli score,  cli calibrate
```

Scoring runs on Gemini against a prepaid balance with **about $1.82 left**, and
a full re-score costs ~85¢. The daily cron already handles it at 10:00 for
~2.5¢/day. Document generation is cheap by comparison — a resume is ~0.4¢, a
cover letter ~2¢ — so `tailor` is fine to run.

`measure`, `status`, `sync` and the test suite cost nothing.

## Where things are

| | |
|---|---|
| `config/master_profile.md` | source of truth for every claim. Gitignored |
| `config/bullets.yaml` | the bullet bank — immutable text, hand-edited |
| `config/targets.csv` | 143 companies, `tier` drives queue order |
| `config/filters.yaml` | Stage A rules. Do not loosen without an audit |
| `output/applications/<role>/` | `resume.pdf` + `cover_letter.md` per role |
| `jobpipe.db` | SQLite, source of truth. Never delete rows |

## Context worth reading

- `PROJECT.md` — the guardrails, and why the phases are shaped as they are
- `CONTEXT.md` — where things actually stand: live numbers, verified
  defects, and the Phase 5 plan to expose jobpipe over MCP. Read it
  before trusting a figure quoted in an older document
- `POST_PHASE2_FIXES.md` — the current work list; `CONTEXT.md` §8.8 lists
  the items it still shows as open that are in fact done
- `PHASE1_SUMMARY.md`, `PHASE2_SUMMARY.md` — what was built, what departed from
  spec and why, and the bugs worth not rediscovering

## Working style

Verify before acting on a requested change. Several fixes in this repo turned
out to rest on stale premises, and the audits that caught them were cheap:
a `normalize_title` "fix" would have deleted 41 real roles, and two of three
premises in `POST_PHASE2_FIXES.md` §1 did not survive the audit that document
itself asked for.

## Reviewing a generated resume

Read the posting alongside the resume and answer three specific questions
rather than forming a general impression:

1. **Which required skills have no corresponding bullet?** If the bank has no
   evidence for something the posting demands, the correct output is a resume
   without it — say so plainly, do not look for a way to imply it.
2. **Do the selected bullets skew to the wrong lane?** A `data_science`
   posting that produced an `ai_ml` variant, or vice versa.
3. **What was dropped to fit one page?**

The third is the most useful and the least obvious. Every resume is enforced
to one page, so bullets get cut — and `tailor --queued --yes` now prints what
went:

```
  Cohere — Software Engineer, Agents & Automations
      ai_ml, 14 bullets at 9.0pt, parse OK
      dropped to fit: gt-remittance, capstone-quality
```

`RenderResult.dropped` holds the same list in code.

**If the drop list contains something the posting explicitly asked for, that
is a concrete defect**, not a matter of taste. "The resume looks about right"
is an impression; "the posting names PostgreSQL three times and
`gt-remittance` was dropped" is actionable, and the fix is specific — lower
that bullet's `rank`, add the posting's vocabulary to its `skills` tags, or
drop something else instead.

The drop order is relevance-aware: a bullet whose `skills` overlap the
posting's vocabulary is promoted up to two ranks before being cut. So a
relevant bullet appearing in the drop list usually means its `skills` tags do
not match the words the posting actually uses, which is a bank fix rather than
a rendering one.

Letters run 332–374 words against Anish's own 360–431. Tightening and
lengthening drafts is most of the editing work.
