# Phase 2 Spec — Document Tailoring

> Prerequisite: Phase 1 running and calibrated. See `PHASE1_SUMMARY.md`.
> Read `PROJECT.md` for the constraints that hold across all phases.

---

## Objective

For a posting the human has chosen to apply to, produce a tailored resume PDF and a
cover letter draft, grounded entirely in `master_profile.md`, ready for review.

**Definition of done:** selecting a role in the sheet and running one command produces
two files in a review folder. Human edits and sends. Time from decision to submission
drops from ~40 minutes to under 10.

## Non-goals

- No form filling, no browser automation (Phase 3)
- No email drafting or sending (Phase 4)
- No submission of any kind, ever
- No generation for roles the human hasn't explicitly selected

---

## 1. The trigger is human, not score

Phase 1's own measurement says why: `apply_score` runs +0.79 generous with a tighter
spread than the human's (1.22 vs 1.74), and top-20 precision is 59%. Roughly seven of
twenty surfaced roles are ones the human would not apply to. Generating documents for
those is wasted spend and, worse, dilutes attention across a padded list.

**Flow, in two stages:**

1. Human sets `status = queued` → `cli tailor --queued` generates a **resume only**,
   sets `status = drafted`, writes `resume_version`.
2. Human decides to actually apply → `cli tailor --cover-letter <posting_id>` (or
   `status = applying`) generates the letter.

**Resumes are cheap and reusable; cover letters are not.** A resume is bullet selection
from a fixed bank — a single constrained Flash-Lite call, no free prose, no grounding
check needed. A cover letter is generated prose that needs the expensive model and the
verification pass in §2.2, and it is worthless until the human has committed to applying.

This also resolves the score-5 case cleanly: a role the pipeline rated low but the human
queued anyway gets a resume sitting ready, costing a fraction of a cent, with no letter
until the human decides. No refusal logic needed anywhere.

Never generate from `apply_score` alone. The sheet's human columns are the gate.

### 1.1 Supply is no longer the constraint — throughput is

The target list is now **142 companies** (72 Greenhouse, 70 Ashby), up from 36, with
~13,400 postings fetched per run.

The sheet is moving to **top 25, capped at 3 postings per company** (see
`PHASE1_ADDENDUM_2.md` §1). The per-company cap is the substantive half: without it a
single large employer with forty open reqs crowds out every mid-size company on the list,
and rank alone would bury an actively hiring 30-person startup below Datadog's back
catalogue. The cap makes the sheet a survey of the market rather than of whoever posts
most.

**This is a Phase 1 change and it should land before Phase 2 is built**, because Phase 2's
queue ordering operates on whatever the sheet contains.

**Triage burden still rises.** Twenty-five rows at the current 59% precision is ~15
roles worth applying to, against a realistic throughput of 5–10 applications a week, and
the sheet accumulates rather than resetting. The queue will regularly exceed capacity.
Two consequences:

- **`cli tailor --queued` must batch.** Generating one document at a time interactively
  does not survive a queue of fifteen. Process the whole queue, write to the review
  folder, report a summary.
- **The queue needs an order.** Since the queue will always exceed what gets applied to,
  ordering is not a nicety — a partial run must be the *right* partial run.

Re-measure precision after a week at the new cutoff. The 59% figure came from 36
companies and a top-20 sheet; both inputs have changed.

### 1.2 Prioritising the queue

`targets.csv` still has **all 142 rows at `tier = 2`**. At 36 companies I advised leaving
it — the field did nothing and ranking it was an evening spent on a no-op. At 142 that
advice no longer holds: tier is now the only signal distinguishing a company the human
would take an offer from today from one they would merely consider, and `apply_score`
cannot express that because it scores the *role*, not the employer.

**Rank the tiers before building the queue ordering.** It is an hour of work and it is
the input that makes prioritisation meaningful. Queue order: `tier` ascending, then
`apply_score` descending.

If the tiers stay unranked, order by `apply_score` alone and accept that a Tier 3
company's 8 outranks a Tier 1 company's 7 — which is usually the wrong call.

---

## 2. The fabrication constraint

**This is the highest-stakes requirement in the project.** A hallucinated metric on a
resume is career-damaging in a way no other failure here is. Design so fabrication is
structurally difficult rather than merely discouraged.

### 2.1 Bullet bank, not free generation

Decompose `master_profile.md` into a bank of discrete, immutable bullet records:

```yaml
- id: dist-ablation
  text: "Designed a controlled ablation isolating reasoning traces as the causal
         driver: traces +16.7 pts vs. answer-only data +4.0 pts on identical
         problems, with non-overlapping 95% CIs (n=1,000)."
  source: projects.reasoning_trace_distillation
  archetypes: [ai_ml_engineer, data_scientist, ml_platform]
  skills: [ablation, experimental_design, qlora, evaluation]
  metrics: true
  variants:
    short: "Controlled ablation isolating reasoning traces as causal driver
            (+16.7 vs +4.0 pts, non-overlapping 95% CIs, n=1,000)."
```

**Resume tailoring is selection and ordering, not writing.** The model picks bullet IDs
and their order. It may choose between pre-written `variants`. It may not author new
bullet text. This removes the hallucination surface from the resume entirely.

Every number in the bank traces to the resume or a capstone report. Nothing enters the
bank that isn't already evidenced in a source document.

### 2.2 Grounding check on the cover letter

The cover letter is the only genuinely generated prose, so it needs verification. After
generation, a second cheap pass takes each factual claim in the letter plus the master
profile and returns:

```
claim: str
supported: true | false | partial
source_section: str | null
```

Any `false` or `partial` blocks the draft and is surfaced to the human. Do not
auto-correct — surface and stop. Silent correction hides the failure mode.

### 2.3 Tier discipline carries over

`skills_match` rules from the master profile apply here too: **Exposure-tier skills must
never appear in a tailored resume or cover letter as claimed capability.** If a posting
demands MLflow and the bank has no MLflow bullet, the correct output is a resume without
MLflow, not an invented line. Absence is the honest answer.

---

## 3. Resume generation

### 3.1 Two base variants, not four

Build **two** resume layouts. The four-archetype plan was premature — ship the two that
cover the overwhelming majority of queued roles, and add others only when a real posting
is served badly by both.

| Variant | Leads with | De-emphasises |
|---|---|---|
| `ai_ml` | Distillation project, RAG assistant, Gradient Tensor LLM pipelines | BI/analytics, survey work |
| `data_science` | Ablation design, hypothesis testing, Bumi models, capstone | Kubernetes, backend depth |

Phase 1's lane classification picks the variant. Layout is fixed per variant; the model
selects which bullets fill each section and in what order, constrained to the bank.

`ml_platform` and `swe_generalist` are deferred, not cancelled. Revisit once ten
applications have gone out and it is clear whether anything was poorly served.

### 3.1a Keyword alignment — honestly

ATS keyword matching is real, and the `data_science` variant in particular needs to
surface the vocabulary a DS posting screens on. But keyword optimisation is where
fabrication pressure is highest, so constrain the mechanism:

1. **Extract the posting's required-skill vocabulary** in the selection call.
2. **Prefer bullets whose `skills` tags overlap that vocabulary.** Selection, not
   invention — the bullet text is still immutable.
3. **Reorder the Skills section** to front-load matching entries.

**Hard rule:** only Core and Working tier skills may be surfaced or reordered.
An Exposure-tier skill is never added to a resume to match a keyword, and no skill absent
from the master profile is ever added at all. If a posting demands MLflow, the honest
output is a resume without MLflow.

Selection-and-reordering captures most of the ATS benefit and cannot produce a claim the
human can't defend in an interview. Keyword stuffing produces a screen-pass followed by a
screen-fail, which is worse than not passing.

### 3.2 One page, hard

He is nine months post-graduation. Two pages reads as padding at this level. Enforce
programmatically: render, measure, and if it overflows, drop the lowest-ranked selected
bullet and re-render. Fail loudly rather than shrinking font below 10pt or margins below
0.5in — both are ATS parse risks.

### 3.3 ATS safety — non-negotiable

- Single column. No tables, text boxes, headers, footers, or images
- Standard section headings only: Education, Experience, Projects, Skills
- Selectable text, not vector outlines
- Standard fonts embedded
- Dates in a consistent parseable format

**Verify, don't assume.** Every generated PDF goes through a parse check: extract text,
confirm name, email, all employer names, and all dates are recoverable. A resume that
renders beautifully and parses to mush is worse than a plain one. This is the Phase 1
liveness lesson applied — test what the consumer actually sees.

### 3.4 Lift the template

`templates/cv-template.html` + `fonts/` from career-ops (MIT), rendered via Playwright.
Working ATS-optimised template, saves most of this work. Restyle freely.
`openai-tailor.mjs` is their headless tailoring path — closest analogue to this
architecture, worth reading before writing.

---

## 4. Cover letter generation

### 4.1 Voice comes from his own letters

Five existing letters (City of Hope, Fervorly, Fanatics, NORC ×2) are the style corpus.
Include 2–3 as few-shot examples in the prompt.

**Two observations from them that should become explicit prompt constraints:**

- **The NORC letters are the strongest and the least templated.** They open with a reason
  for wanting *that* work rather than a restatement of the job title, and they say what he
  found absorbing rather than what he achieved. Anchor on these.
- **Four of five close on the same construction** — "I would welcome the chance to bring
  that combination of X, Y and Z." It reads as a tell. Ban the pattern explicitly and
  require the closing paragraph to differ structurally between letters.

### 4.2 Structure

Four paragraphs, ~350 words:

1. Why this role, specifically — something true about the company or team, not the title
2. Strongest archetype-matched evidence, with the metric
3. Second evidence thread, ideally the rigor/evaluation angle
4. Close — varied, no formula

### 4.3 Don't raise sponsorship

Cover letters are not where work authorisation belongs. Answer application-form
questions truthfully and completely; volunteering it in the letter invites a screen-out
before anyone reads the evidence. This is a positioning decision, not a concealment one —
if a posting explicitly asks candidates to state authorisation status in the letter,
state it plainly.

---

## 5. Model and cost

Two different jobs, two different tiers:

- **Bullet selection** — structured JSON, constrained choice. `gemini-3.1-flash-lite`,
  temperature 0. Same tier as Phase 1 scoring.
- **Cover letter prose** — quality-sensitive. Start at `gemini-3.1-flash`; escalate if
  drafts read generic. Prose quality is the whole deliverable here, so do not
  cost-optimise this call.
- **Grounding check** — `gemini-3.1-flash-lite`, temperature 0.

Cache the master profile and bullet bank as a static prefix, as in Phase 1.

**Estimated cost:** the two-stage trigger (§1) splits this cleanly. Resumes run
~0.3–0.5¢ each — one constrained selection call, no prose, no grounding check — so
generating for every queued role is negligible even at a queue of thirty. Cover letters
run ~2¢ each and only fire on roles actually applied to, maybe 5–10 a week.

Expect **well under $1/month** on top of Phase 1's ~30¢. Cost is not a constraint in
this phase; quality and the human's review time are.

Note the spend cap set for Phase 1 was sized against a ~30¢/month workload. Raise it
before the first batch run, or a fifteen-role queue will trip it mid-generation —
exactly the `--rescore` failure mode in §8, where the guardrail causes the outage.

Keep the existing provider abstraction. Anthropic remains the last rung.

---

## 6. Storage and versioning

`resume_version` already exists in the schema and is unwritten. Populate it.

```sql
CREATE TABLE documents (
    id              INTEGER PRIMARY KEY,
    posting_id      INTEGER REFERENCES postings(id),
    doc_type        TEXT,          -- resume | cover_letter
    archetype       TEXT,
    bullet_ids      TEXT,          -- JSON array, resume only
    generated_path  TEXT,
    sent_path       TEXT,          -- populated after human edits
    model           TEXT,
    generated_at    TEXT,
    edit_distance   REAL           -- computed when sent_path appears
);
```

**Why `sent_path` matters:** when a role reaches interview, you need to know exactly what
they read. It also gives the feedback loop in §7 something to measure.

---

## 7. Measurement

Phase 1's calibration harness is the reason it's trustworthy. Phase 2 needs its own.

- **Grounding accuracy** — % of cover-letter claims marked `supported`. Target 100%;
  anything below is a defect, not a metric to optimise.
- **ATS parse rate** — % of generated PDFs where all key fields extract cleanly. Target
  100%.
- **Edit distance** — normalised Levenshtein between `generated_path` and `sent_path`.
  This is the real quality signal. Heavy editing means the prompt is wrong. Track the
  trend; it should fall over the first ~15 applications.
- **Per-archetype edit distance** — if one archetype is consistently edited harder, its
  base layout or bullet tagging is wrong.

Do not measure "does the human like it" in the abstract. Measure what they change.

---

## 8. Lessons from Phase 1 that apply here

Carried forward because these bugs will recur in new clothes:

- **Verify before applying a requested fix.** The `normalize_title` near-miss would have
  deleted 41 real roles. The equivalent here is a bullet-bank refactor that silently
  drops bullets — diff the bank before and after any restructuring.
- **One home for column lists, plus a schema-driven round-trip test.** The `engagement`
  NULL bug came from a column defined in three places and written in one.
- **Destructive operations must be transactional.** `--rescore` deleted then hit the
  spend cap. A `--regenerate` flag must not delete existing documents until new ones
  have been written successfully.
- **Never delete, refresh.** Stale sheet rows froze at wrong scores. Documents follow the
  same rule — supersede, don't overwrite.
- **Anchor phrase matching.** The sponsorship allow-phrases matched inside refusals. Any
  new phrase matching in this phase gets the same `we <verb>` anchoring treatment.
- **Test with no network.** 298 tests, no network. Hold the line.

---

## 9. Build order

1. Bullet bank — extract from `master_profile.md` into YAML, tag by archetype and skill.
   **Manual review of every bullet.** This is the foundation; do not automate it.
2. `documents` table + migration
3. Resume renderer — lift `cv-template.html`, wire to Playwright, hardcode `ai_ml`
4. ATS parse check (§3.3), run against the hardcoded output
5. One-page enforcement loop (§3.2)
6. Bullet selection call, constrained to bank IDs
7. `data_science` variant + keyword alignment (§3.1a)
8. Cover letter generation, on-demand trigger only, few-shot from his own letters
9. Grounding check (§2.2)
10. `cli tailor --queued` wiring with batch processing and queue ordering (§1.1–1.2),
    status transitions, `resume_version` population
11. Edit-distance tracking once `sent_path` starts being populated

Steps 1–5 produce a working single-variant resume pipeline. Ship that and use it on the
next five applications before building the rest.

**Parallel workstream:** the SmartRecruiters fetcher (Canva, Glean, and ~43 further
candidates) runs alongside Phase 2 rather than waiting for it. It is independent Phase 1
work and touches no Phase 2 code. Do not let it block step 1.

---

## 10. Open questions

Resolved by the human, recorded here so the reasoning survives:

- *Per-archetype rewrites* → two variants (`ai_ml`, `data_science`), with keyword
  alignment by selection and reordering only (§3.1, §3.1a).
- *Score-5 queued roles* → resume generated, cover letter withheld until the human
  commits (§1). No refusal logic needed.
- *Sheet cutoff* → top 25, max 3 per company (§1.1). Phase 1 change, lands first.
- *SmartRecruiters* → parallel workstream during Phase 2 (§9).

Still open:

- **Does a third variant earn its place?** Deferred until ten applications have gone out
  and it is visible whether any posting was served badly by both. A `research_healthcare`
  layout leading with the CDC/HL7 work is the likeliest candidate — Abridge-type
  employers weight it far more heavily than either current variant does. The human's own
  read is that this depends on how the filters behave at the new cutoff, so measure
  before building.
- **What precision does top-25 with a 3-per-company cap actually give?** Both inputs to
  the 59% figure have changed. Re-measure after a week. This determines whether queue
  ordering (§1.2) is doing real work or just shuffling a uniformly good list.
- **Does the per-company cap hide roles worth seeing?** A company with five genuinely
  strong openings now shows three. Check whether the suppressed two were ever worth
  surfacing before adding an override.
