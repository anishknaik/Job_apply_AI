# Phase 1 — Addendum 2

> Changes required before Phase 2 is built. See `PHASE2_SPEC.md` for why each matters.
> Small, self-contained, no new subsystems.

---

## 1. Sheet cutoff: top 25, max 3 per company

Currently top 20 by `apply_score`, no per-company limit.

**Change to top 25, capped at 3 postings per company.**

The cap is the substantive half. Without it a single employer with forty open reqs
crowds out every mid-size company on the list, and an actively hiring 30-person startup
sinks below a large company's back catalogue. The cap makes the sheet a survey of the
market rather than of whoever posts most.

**Apply the cap during selection, not after truncation.** Rank all candidates, then walk
the ranked list taking postings while each company is under its limit, until 25 rows are
filled. Truncating to 25 first and then dropping over-cap rows leaves the sheet short.

25 rather than 50: the cap already delivers the company diversity that motivated
widening, and 25 slots with a 3-cap guarantees at least nine distinct companies. 50 rows
at current precision is ~30 roles worth applying to against a realistic throughput of
5–10 a week — a queue that permanently exceeds capacity is not more useful than one that
occasionally does. The sheet also accumulates rather than resetting, so a larger N
compounds.

## 2. Rank the tier column

`targets.csv` has all 142 rows at `tier = 2`. Nothing reads the field.

Phase 2 orders its generation queue by `tier` then `apply_score`. Without ranking, a
Tier 3 company's 8 outranks a Tier 1 company's 7 — usually the wrong call, and
`apply_score` cannot express it because it scores the role, not the employer.

Roughly an hour of work. 1 = would accept an offer today, 2 = strong yes, 3 = would
consider.

## 3. Confirm the status taxonomy covers the Phase 2 handshake

Phase 2 keys off three states. Verify they exist in the adapted career-ops taxonomy and
add any that don't:

- `queued` — human has selected it; triggers resume generation
- `drafted` — resume generated and waiting
- `applying` — human has committed; triggers cover letter generation

No other Phase 1 change is needed. `resume_version` already exists and stays unwritten
until Phase 2 populates it.

## 4. Re-measure precision at the new cutoff

The 59% figure came from 36 companies and a top-20 sheet. Both inputs have changed.

Re-measure after a week at top-25 with the cap. This determines whether Phase 2's queue
ordering is doing real work or shuffling a uniformly good list — and whether the cap is
hiding anything worth seeing.

## 5. Raise the spend cap

Sized against a ~30¢/month workload. Phase 2 adds resume generation across the queue plus
cover letters on demand. Still well under $1/month total, but raise the cap before the
first batch run rather than discovering it mid-generation — the `--rescore` pattern,
where the guardrail causes the outage it exists to prevent.

## 6. SmartRecruiters fetcher — parallel workstream

Canva and Glean are verified live and excluded only because no parser exists. ~43 further
candidates sit on Lever, Workday, or custom pages.

Independent of Phase 2 and touches none of its code. Run alongside, but do not let it
block the bullet bank.

---

## Order

1, 2 and 3 before Phase 2 starts — Phase 2's queue ordering and triggers depend on them.
5 before the first batch run. 4 after a week of data. 6 whenever.
