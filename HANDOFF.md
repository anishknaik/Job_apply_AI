# Handoff — 2026-08-31

Where Phase 5 actually stands, what is verified against what is assumed, and
what in the session transcript has since been overturned.

Read this before `session_2026-08-31_phase5.txt`. The transcript is a record of
what was *said*, and several things in it were corrected by later turns — a
fresh reader going straight through will absorb the wrong versions. The
corrections are in §5.

Read after `PROJECT.md` and `PHASE5_PLAN.md`. `CONTEXT.md` is now the stalest
of the three: §7's Vertex question is settled, and §9 is superseded by
`PHASE5_PLAN.md`.

---

## 1. State

```
32 commits, 685 tests passing, branch master (phase5/slice-0 is identical)
16,628 postings | 625 past Stage A | 635 scored | 1 letter measured
```

Shipped configuration:

```
JOBPIPE_GEMINI_BACKEND=vertex        scoring   gemini-2.5-flash
GOOGLE_CLOUD_LOCATION=global         escalation gemini-2.5-pro
RUBRIC_VERSION=2026-08-31.2          grounding  gemini-2.5-flash-lite
```

Scheduled run is 11:30, S4U, **fetch/filter/sync only — it scores nothing.**
That is a deliberate temporary posture while calibration settles;
`register-task.ps1 -Spend` puts scoring back. Consequence: an unscored backlog
grows about 3/day and nothing reaches the sheet until someone runs `cli score`.

---

## 2. Done and verified on real data

| | |
|---|---|
| **Slice 0** | all 9 items |
| **Track B slice 1** | 5 read-only MCP tools, stdio, exercised from a live session |
| **Track B slice 2** | `copy_for_editing` + `advance_status`, used on a real application |
| **Track A 1–4** | probe, `VertexGeminiProvider`, rubric + `domain`, holdout gate |

Verified means observed, not inferred:

- The **11:30 task launches** — it failed on 5 of 17 days with `0xC0000142`
  until the S4U principal was applied. A manual trigger then ran clean and
  wrote the first row `runs` has ever had.
- **`measure` has its first data point.** PostHog, distance 0.032, and the
  seven block labels land on the right blocks — the Slice 0 slot fix confirmed
  on real output rather than a fixture.
- **The new score fields land.** `domain`, `seniority_gap`, `scorer`,
  `rubric_version` all populate; Wayve classified `robotics_cv`, matching the
  hand classification.
- **Implicit caching fires on 2.5 Flash** against the real profile prefix, 98%
  when it lands. Flash Lite caches nothing — measured twice, eleven calls.

---

## 3. Open

**Track A step 5** — record calibration figures against `2026-08-31.2`. Worth
more than it was: it is the first measurement with escalation actually working
(see §5), and the n=19 holdout is too small to steer by. Do it over the
**56-posting hand-scored set**. ~50¢.

**Track A step 6** — backfill the ~67 live rows so the queue carries `domain`.
Only 3 scores have it. Slice 3's variant grid is unexercised until this runs.

**Track B slice 3** — `render_resume`: split `pipeline_tailor.generate_resume`,
build the seven variant orderings, add the header generator from
`master_profile.md` §12. The orderings need a human review; that is the part
where being wrong makes every resume in a lane wrong the same way.

**Track B slice 4** — `save_cover_letter` + `check_grounding`, shipped
together, never separately.

**More measured letters.** One data point showed `why this role` at 0.110
against ~0.01 elsewhere — ten times more editing in the opening. Two or three
more applications settle whether that is a prompt defect or noise.

---

## 4. Known defects, deliberately unfixed

**The model over-scores the bottom band.** Postings hand-scored 1 come back
6–8. It shows up in MAE and bias, which measure a decision the sheet does not
make — the sheet ranks. Spread already matches (2.401 vs 2.43) and both false
negatives are fixed, so this is cosmetic for a ranked sheet. **Two attempts to
fix it both made ranking worse** (§5).

**`CONTEXT.md` is stale.** §7's open Vertex question is answered; §9 is
superseded.

**Two experiments are off by default, with the mechanism kept and tested:**
`JOBPIPE_CALIBRATION_EXAMPLES` and `JOBPIPE_SCOPE_CEILING`.

---

## 5. What the transcript gets wrong

Each of these was stated confidently and corrected later. A reader taking the
transcript at face value will believe the first version.

**Escalation never ran on 2.5.** `gemini-2.5-pro` rejects `thinking_budget=0`
with a 400, and the provider set it unconditionally. All three 2.5 calibration
runs were single-model — 19 of 19 postings, checked. Fixed in `6e42d7b`.
**Every "2.5 vs 3.1" comparison in the transcript is therefore confounded**:
3.1 escalated and worked, 2.5 did not. Comparisons *between* the three 2.5
runs remain valid; they shared the same broken escalation.

**The `non_technical` floor was aimed at the wrong roles.** Proposed to fix
the bottom-band over-scoring. None of the six over-scored roles was
`non_technical` — they were `ai_ml_engineer` ×4, `data_scientist`,
`swe_generalist`.

**The scope ceiling made things worse.** Built as the replacement fix, it
collapsed 11 of 19 postings onto exactly 5: spread 2.401 → 1.742, rank +0.309
→ +0.160, recall 50% → 0%. **A cap does not lower scores, it collapses them,
and a ranked sheet needs spread.** Off by default.

**Calibration examples made ranking worse too.** Built from real hand scores
and notes, they halved the bias and took rank agreement from +0.346 to −0.016.
Off by default.

**`overall: 0` was not out of range.** Reported as a schema violation; the
scale is documented 0–10 in five places. The real gap was that the range was
never *enforced* — now bounded.

**The pattern behind three of those:** having demoted MAE and bias as measuring
the wrong decision, I kept optimising them anyway. Every intervention aimed at
absolute scores degraded rank. **The profile fix was the only change that
helped, and it worked by removing a rule the model was misreading rather than
by adding one.**

---

## 6. Two things that nearly cost data

Both worth knowing because both will recur.

**An exact-name gitignore does not survive the next file.** It happened three
times: `master_profile.md.bak`, the generated calibration examples, and a new
session transcript. All three are globbed now.

**Checking out a branch that predates a rename deletes the now-ignored file.**
Fast-forwarding `master` silently removed `config/targets.csv`, which `master`
still tracked and `phase5/slice-0` had renamed. Recovered from the example, but
`bullets.yaml` was in the same category and survived — and I cannot explain
why, which is the reason to be careful rather than confident.

Backups are in `D:\jobpipe-backups\` — two `.git` snapshots plus the untracked
half, with a restore recipe. The pre-rewrite snapshot **contains the resume in
all 27 commits**; it is recovery-only and must not be pushed.

---

## 7. Sharing

The repo is private on GitHub. `config/bullets.yaml` was purged from all
history — it is a resume — along with a real phone number in test fixtures.
`targets.csv` is now personal and gitignored; `targets.example.csv` ships with
143 companies and verified board tokens.

`README.md` is a setup guide with nine ordered steps. The step people miss is
sharing the Google Sheet with the service account's email — Sheets permissions
and Cloud IAM are separate systems.

**Before any push, check `git status` and `git ls-files config/`.** The latter
should show only `*.example.*` plus `filters.yaml` and `states.yml`.
