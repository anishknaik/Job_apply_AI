# Post-Phase 2 — Fixes

> Written after `PHASE2_SUMMARY.md`. Nothing here is a new subsystem.
> §1 is the group that matters; the rest is maintenance.
>
> **Do not start Phase 3 until §4 has data.**

---

## 1. Profile corrections have not propagated past the LLM layer

Phase 2's grounding check found two places where `master_profile.md` was wrong. The
corpus was re-scored, which fixed what the *model* sees. It did not touch the
deterministic filters that decide what the model ever sees, and Phase 1's own known
limits state the problem plainly: roles Stage A killed remain invisible by construction.

**These are the highest-value fixes in this document. The rest can wait.**

### 1.1 Co-founder — positioning and seniority filters

`master_profile.md` §1 positions the candidate as "roughly nine months full-time
post-graduation," targets new-grad through early-career, and §10 hard-excludes `senior`,
`lead`, `manager`, `principal`, `staff`, `head of`, `director`, `vp`.

Co-founding an AI company invalidates that positioning at the root, and Stage A is
enforcing the old version with word-boundary regex before scoring ever happens.

**Actions:**

1. **Rewrite `master_profile.md` §1.** The level band is no longer "new-grad through
   early-career" alone. Founding experience puts Founding Engineer, early-employee, and
   some Lead-titled IC roles genuinely in range.
2. **Revisit `title_exclude` in `filters.yaml`.** `lead` in particular now over-kills —
   "Founding Engineer" is fine, but "Technical Lead" at a 20-person startup may not be
   the same disqualification it was. `manager`, `director`, `vp`, `head of` stay.
3. **Add co-founder bullets to the bank.** The bank traces every bullet to the resume or
   capstone; co-founder appears in neither, so the fact is in the profile but **cannot
   appear on any tailored resume**. Until bullets exist, the strongest differentiator in
   the profile is unrenderable.
4. **Add the company and role to `master_profile.md` §6** employment history, with dates.
   Currently the May–Nov 2025 period and the founding work are unreconciled.
5. **Re-run calibration.** The hand-scored set was scored against the old positioning.
   MAE, bias and spread are all suspect now.

### 1.2 Relocation — probably the larger supply lever

§10 listed on-site outside NYC metro as near-disqualifying. `filters.yaml` encodes that
as `location_allow`. If relocation to SF is now acceptable, a large share of postings
across 142 companies are being killed at Stage A and never scored — invisibly.

**Actions:**

1. **Run `cli audit` filtered to location rules** over the last month. Count what the
   location filter killed. **Do this before changing anything** — the number decides
   whether this is a footnote or the single largest change available.
2. **Update `master_profile.md` §10** to state the actual constraint, including any
   conditions (relocation assistance required, specific metros, timing).
3. **Update `location_allow`** to match.
4. **Do not backfill blindly.** If the audit shows thousands of killed postings,
   re-scoring all of them at once will breach the spend cap mid-run — the `--rescore`
   failure mode. Scope by company tier or date window.

### 1.3 Audit the profile for further gaps

Two errors surfaced only because a cover letter happened to reference them. Both were
cases of the profile *understating* the candidate, which no test can catch.

Read `master_profile.md` end to end against the actual history. The §12 gaps list has
items still marked `[CONFIRM]`. This is an hour, not a project, and everything in the
system inherits from this file.

---

## 2. Grounding check reliability

### 2.1 Grounding was validated on one sample of a high-variance process

Letters returned 100% grounding on first attempt. But prose regenerates at 0.53 distance
from itself at temperature 0 — so a single pass is one draw from a wide distribution,
not a reliability claim.

**Action:** generate the same letter 10 times, run grounding on all 10, report the pass
rate. Costs a few cents. If it holds, the claim is real; if it doesn't, there is a defect
a single pass cannot structurally surface.

### 2.2 Make the checker independent of the writer

The summary names this: the grounding check shares a model family with the writer, and a
blind spot common to both would not be caught.

**Action:** route grounding through the provider abstraction to Anthropic. The escalation
ladder already exists and is unused. At this volume the cost difference is negligible,
and it converts a known blind spot into a genuinely independent check.

---

## 3. Outstanding from Addendum 2

Neither appears in `PHASE2_SUMMARY.md`.

### 3.1 Rank the 142 tiers

All rows still at `tier = 2`. Phase 2's queue ordering is `tier` then `apply_score`, so
until this is done the ordering is `apply_score` alone.

More urgent now than when written: if §1.2 opens up relocation, the queue gets longer,
and ordering does more work.

~1 hour. 1 = would accept today, 2 = strong yes, 3 = would consider.

### 3.2 Re-measure top-25 precision

The 59% figure came from 36 companies and a top-20 sheet. Both inputs changed. Needs a
week of data at the new cutoff.

Determines whether queue ordering is doing real work or shuffling a uniformly good list,
and whether the 3-per-company cap is hiding anything worth seeing.

---

## 4. Use the system before extending it

**Three measurements cannot move without applications going out:**

- Edit distance needs ~15 letters; it has one, and that one's baseline was destroyed
  before the fix landed.
- Top-25 precision needs a week at the new cutoff.
- The corrected profile's effect on scoring is unvalidated.

Phase 3 saves perhaps five minutes per application. At the current rate that is minutes
per week, and it would add a fourth unmeasured system on top of three.

**Target: 15 applications over the next two weeks.** Then re-read this list — several
items will have answered themselves, and the deferred variants in §5 will have evidence
behind them instead of speculation.

---

## 5. Smaller fixes

### 5.1 `non_technical` short-circuits to 1

Named in the summary as the most damaging label in the system. It was defined by domain
words until a real posting exposed it — "Marketing Engineer" is an engineering role — and
it fails in the invisible direction, so nothing surfaces the misses.

**Action:** pull every posting labelled `non_technical` in the last month and read the
titles. If any are engineering roles, the classifier is still wrong and the fix was
incomplete.

### 5.2 Selection concentration

The prompt asks for few roles; the model spreads across nine or ten, so the renderer
drops one or two bullets a tighter selection would have kept.

**Action:** try an explicit cap — at most N roles represented — in the response schema
rather than the prompt, the same structural approach that worked for bullet ids. Compare
rendered output before and after.

### 5.3 Letter length runs short

332–374 words against the candidate's own 360–431. Inside range, consistently at the
bottom of it.

**Action:** raise the target range in the prompt. Low priority — verify against edit
distance first, since if letters are not being lengthened during editing, this is not
actually a problem.

### 5.4 Hyphenated compounds

`citation-grounded` extracts as `citation-\ngrounded` or fuses. Correctly diagnosed as
having no honest render-time fix.

**Action:** none. Leave reported. Recorded here so it is not rediscovered.

---

## Order

1. §1.2 audit (read-only, decides everything downstream)
2. §1.1 and §1.2 profile + filter corrections, then re-calibrate
3. §1.3 profile read-through
4. §3.1 tier ranking
5. §2.1 and §2.2 grounding reliability
6. **§4 — apply to 15 roles**
7. §3.2 and §5 after data exists
