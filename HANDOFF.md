# Handoff — 2026-09-10

Where Phase 5 actually stands, what is verified against what is assumed, and
what in the session transcript has since been overturned.

**Updated 2026-09-10: Track C is built, and the first application has gone
through it end to end** — resume, two grounded form answers, a supervised
fill, submitted by Anish. That run found three defects the offline suite
could not; they are in §4 and §5. §5a is rewritten for what comes next.

Read this before `session_2026-08-31_phase5.txt`. The transcript is a record of
what was *said*, and several things in it were corrected by later turns — a
fresh reader going straight through will absorb the wrong versions. The
corrections are in §5.

Read after `PROJECT.md` and `PHASE5_PLAN.md`. `CONTEXT.md` §2 and §4 were
remeasured on 2026-09-10; its §9 is superseded by `PHASE5_PLAN.md`.

---

## 1. State

```
837 tests passing, 12 tools on the MCP surface, branch phase5/track-c
  (off master at 476df9b; master and origin/master unmoved)
18,330 postings | 714 past Stage A | 645 scored (625 of them survivors)
89 survivors unscored | 23 applied | 2 letters measured
  (only 1 of the 2 counts toward the per-slot means — see §3)
```

Shipped configuration — all four models resolve on Vertex:

```
JOBPIPE_GEMINI_BACKEND=vertex        scoring    gemini-2.5-flash
GOOGLE_CLOUD_LOCATION=global         escalation gemini-2.5-pro
RUBRIC_VERSION=2026-08-31.4          grounding  gemini-2.5-flash-lite
                                     prose      gemini-2.5-flash
```

`JOBPIPE_GEMINI_BACKEND` now has **no default** — it decides which account is
billed, so an unset value raises rather than falling back to the Developer
API. If something suddenly refuses to start, that is the first thing to check,
and the usual cause is a process that did not load `.env`: python-dotenv
searches upward from the **calling file**, not the working directory, so a
script outside the repo root silently gets nothing.

Scheduled run is 11:30, S4U, **fetch/filter/sync only — it scores nothing.**
That is a deliberate temporary posture while calibration settles;
`register-task.ps1 -Spend` puts scoring back. Consequence: an unscored backlog
grows, and nothing new reaches the sheet until someone runs `cli score`. It
stood at 89 on 2026-09-10, ten days after the last scoring run — about 9 a
day, three times the rate first estimated.

---

## 2. Done and verified on real data

| | |
|---|---|
| **Slice 0** | all 9 items |
| **Track B slice 1** | 5 read-only MCP tools, stdio, exercised from a live session |
| **Track B slice 2** | `copy_for_editing` + `advance_status`, used on a real application |
| **Track A, all 6** | probe, `VertexGeminiProvider`, rubric + `domain`, holdout gate, recalibration, queue backfill |
| **Track B slice 3** | `render_resume`, the seven reviewed orderings, the header generator |
| **Track B slice 4** | `save_cover_letter` + `check_grounding`, shipped together |
| **Track C, built** | `save_short_answer`, `check_grounding(question=…)`, `form_answers`, the answers file, the form rules in `CLAUDE.md` and the server instructions |
| **Track C, fill 1 of 2–3** | LangChain Deployed Engineer (Early Career-NYC), submitted 2026-09-10 |

Verified means observed, not inferred:

- The **11:30 task launches** — it failed on 5 of 17 days with `0xC0000142`
  until the S4U principal was applied. A manual trigger then ran clean and
  wrote the first row `runs` has ever had.
- **`measure` has two data points.** PostHog 0.032 and a University of Utah
  `Data Scientist I` at 0.033, both Cowork-written. The seven block labels
  land on the right blocks — the Slice 0 slot fix confirmed on real output
  rather than a fixture. Two independent letters agreeing to within 0.001 is
  the first evidence the light-edit result is not a one-off.
- **The new score fields land.** `domain`, `seniority_gap`, `scorer`,
  `rubric_version` all populate; Wayve classified `robotics_cv`, matching the
  hand classification.
- **Implicit caching fires on 2.5 Flash** against the real profile prefix, 98%
  when it lands. Flash Lite caches nothing — measured twice, eleven calls.
- **The profile overstated a claim, and a human edit caught it.** `gt-nbs7` and
  `master_profile.md` both said he *built* the CDC NBS 7 Azure deployment and
  that it was *the first Azure reference deployment*. Editing a letter he
  actually sent, Anish downgraded it to "helped build the first proof of
  concept for the Azure reference deployment" — he contributed to a PoC and
  does not know whether the client won the work. Both sources now carry his
  wording verbatim, plus a **Precision point** forbidding "delivered" or
  "shipped".

  Two things worth carrying forward. **Twelve live resumes already went out
  with the strong version**, because a bank bullet cannot be softened per
  application — that is the cost of the header/bullet text being trusted
  hand-edited YAML. And `CLAUDE.md` says the profile has been wrong twice and
  both times it understated; this is the third time and the first in the other
  direction. An overstating profile is the more dangerous failure, because
  nothing downstream can catch it: the fabrication firewall checks that a
  bullet *exists*, never that it is *true*.
- **The first application through Track C, observed at every step**
  (posting 1642). `render_resume` ran live for the first time at 9.0pt, so
  the server's `.env` fix holds; 14 bullets, drop list reviewed. Two form
  answers were saved, passed Gemini's check, were typed in, and were
  compared with the stored copies by SHA-256. `form_answers` read the real
  files. reCAPTCHA was left alone, Anish submitted, and `advance_status`
  recorded it. **The letter path did not run** — the form asked for no
  letter — so `save_cover_letter` and `copy_for_editing` are still
  exercised offline only.
- **The profile was wrong a fourth time — understating, like the first
  two.** "Have you shipped and operated production software?" turned on a
  fact §2 did not hold: the client hosted the lesson-plan pipeline, and
  Gradient Tensor kept developer access and fixed and updated it while it
  was in use. §2 says so now, with a precision point forbidding "hosted" or
  "ran the infrastructure". §12 also gained `work_authorization` and
  `github`.

---

## 3. Open

**Track B is complete, all four slices. Track C is built and has one
supervised fill behind it; two more before its rules are settled.** What is
still open in it is listed under Track C below.

**Slice 3 — DONE 2026-09-01.** `render_and_record` split out of
`generate_resume`; seven orderings in `jobpipe/variants.py`, reviewed and
signed off; the header generated from `master_profile.md` §12 by
`jobpipe/header.py`; `render_resume` on the tool surface. It takes a variant,
never a list of bullet ids — the client could name bullets and the point of
§4.2 is that it does not.

Rendering them for real found that §4.2's own premise was false. "The
one-page loop already drops from the bottom, so an ordering *is* a
selection" was true while the model picked ~16 bullets and the loop trimmed
two or three; handing it all 21 promoted `rank` into being the primary
selector. See §5.

**Slice 4 — DONE 2026-09-01.** `save_cover_letter` takes four body
paragraphs and assembles the rest; `check_grounding` routes to Gemini on the
server's own credentials. Ten tools on the surface now. §5.2's gate is
covered end to end, including the sequence that would otherwise smuggle prose
past it: save, check, rewrite, copy — the rewrite clears the verdict and the
copy is refused.

`check_grounding` is the first tool that spends money. It is a fraction of a
cent on Flash Lite, and it is unavoidable rather than an oversight — §5.2
makes a passing check mandatory before a letter reaches a human, so a gate
that cannot be run blocks everything. `CLAUDE.md` now says so: three of the
four stay off the surface, grounding is the exception in its MCP form only,
and `cli ground` stays banned because the CLI form takes a posting id and can
be pointed at anything, while the tool can only check the draft already there.

**Not built, deliberately:** §5.4's first-save-to-final-save distance. The
STORAGE decision is done and tested — every save is its own revision and
earlier drafts survive intact — but `measure` still only reads the live row,
and there are no MCP-written letters yet to measure. Small addition when
there is data.

**Track C — BUILT 2026-09-10**, `PHASE5_PLAN` §6.1. Twelve tools now.
`save_short_answer` stores each form answer under its question — a
`documents.question` column keys supersession, so answering Q2 never retires
Q1 — and `check_grounding(question=…)` checks it with the letter's checker.
`form_answers` is read-only: §12 of the profile read live, plus the
gitignored `config/answers.yaml`, which holds self-ID as the form's exact
option text behind a loader that refuses a YAML boolean. It is a tool rather
than the MCP resource §6.1.1 names, because a tool behaves the same in every
client. The rules are a section of `CLAUDE.md` and, shorter, the server
instructions, so a Cowork session gets them too.

Open in Track C:

- **Two more supervised fills.** Prefer forms that ask for a letter, so the
  letter path finally runs live — which needs the next item first.
- **A letter as a file.** Letters are stored as markdown, so a form that
  wants a letter *upload* has no tool-produced file to attach. Paste where a
  text box is offered, or render the edited `.sent.md` to PDF with the
  resume renderer — undecided.
- **Who owns the sheet's status column** (§6.2). `sync` no longer reverts a
  tool-recorded status (§5), but it still never writes the cell, so the
  sheet shows a status behind the database until Anish updates it; `sync`
  reports each such row. Leave it, or let `sync` write that column — an
  ownership decision, deliberately not taken as a side effect.
- **The answers file holds no reusable answers yet** (§6.1.1 constraint 2).
  LangChain's customer-experience answer is the obvious first entry, and
  would still be re-checked on every use.

**Track A step 5 — DONE 2026-08-31**, against `2026-08-31.3` over the merged
56-posting set. Rank agreement +0.808, the best any configuration has
measured, and zero false negatives. The gain generalises: on the 36 postings
never used to tune `.3`, rank goes +0.604 to +0.743. Figures and the cost side
are in `PHASE5_PLAN` Track A step 5. It cost three runs rather than one — see
§5 for why.

**Track A step 6 — DONE 2026-08-31.** 67 scored, 56 escalated (84%), 0 failed.
All 67 queued postings now carry `domain`, `archetype`, `seniority_gap`,
`scorer` and `rubric_version`; the 565 untouched rows keep their 3.1
provenance per `PHASE5_PLAN` §3.5. The queue spans six of the seven variants
(`ai_infra` 31, `ai_saas` 15, `product_saas` 7, `robotics_cv` 5, `fintech` 5,
`healthcare` 2, `data_infra` 2), so **slice 3's grid is exercisable.**

`data_infra` at 2 is worth watching: §4.2 sized it at 29% of the target list
and gave it a variant on that basis. Two live postings is not yet evidence
against it — but if it stays at 2 after a few more fetches, the variant's
priority is wrong.

Track A is complete.

**More measured letters — and they have to be assembled ones.** The open
question is still `why this role` at 0.110 against ~0.01 elsewhere, ten times
more editing in the opening. It is **still standing at n=1**, because the
second letter cannot speak to it: written free-form outside
`letters.assemble()`, it has six body paragraphs where the schema has four, so
every block after the first extra one is offset and `summarise()` excludes it
from the per-slot means (`dec195d`).

That exclusion is the point. Averaged in naively it would have taken the slot
from 0.110 to 0.524 while the letter was 97% unchanged end to end — answering
the open question, in the wrong direction, from an artefact of paragraph
counting. The whole-document distances do aggregate, and the two agree closely
(0.032 and 0.033).

So the two or three letters that settle this must come out of the assembled
seven-block shape. That was the argument for **slice 4 before more
letters**, and slice 4 has shipped: `save_cover_letter` takes the four body
paragraphs and assembles the scaffolding server-side, so the mismatch is now
structurally impossible rather than something to remember. The next letters
written through it are the ones that settle the question.

---

## 4. Known defects, deliberately unfixed

**The model over-scores the bottom band.** Postings hand-scored 1 come back
6–8. It shows up in MAE and bias, which measure a decision the sheet does not
make — the sheet ranks. Spread already matches (2.401 vs 2.43) and both false
negatives are fixed, so this is cosmetic for a ranked sheet. **Two attempts to
fix it both made ranking worse** (§5).

**Posting text is frozen at first sight.** `db.upsert_posting` only touches
`last_seen_at` when a posting is seen again, so an employer's later edit
never lands. LangChain rewrote the Deployed Engineer posting on 08-17 —
"3+ years" became "1-3 years", "Sales Engineers" became "Deployed Engineers",
the pay band rose — and the 08-31 score, `get_posting` and grounding's
employer evidence all carried the 08-12 text. The fix is a decision, not a
patch: the stored text is what the score was computed from and what repost
detection hashes, and overwriting it loses both.

**`tests/test_grounding.py` fails run on its own.** It needs
`JOBPIPE_GEMINI_BACKEND`, and passes in the full suite only because an earlier
test loads `.env` into the process. A test-order dependency.

**One false event in the status log.** Event 116 is a sheet-sourced
`not_applied` for LangChain, thirteen seconds after `advance_status`
recorded `applied` — the mirror bug in §5. The log is append-only, so it
stays, and the advance-rate history shows one regression that never
happened.

**Comments in `mcp_server.py` promise resources that do not exist.** Two
say the banned phrases and the letter word target are "in the resources";
no MCP resource is registered. A session writing a letter finds that
guidance only by reading files.

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

**Escalation working made things worse before it made them better.** Fixing
`6e42d7b` was assumed to be a pure gain. Measured, it regressed both roles the
holdout exists to test — Ramp 7 to 5, Scale AI 6 to 3, both scored by Pro. The
profile fix and the `non_technical` demotion had been developed and validated
against Flash alone, because escalation was broken for the entire period they
were written. **A fix validated on one model in a two-model ladder is
validated on half the system**, and the half that was untested went on to
decide 71% of scores. `2026-08-31.3` restates both rules on the schema field
that applies them; the gate passes again, and rank agreement is the best
measured.

**One rule, correct in prose, wrong at the point of use — three times in one
day.** This is the most transferable thing in this document.

  1. **Grounding** called `default_model()`, which ignores
     `JOBPIPE_SCORING_MODEL` (`574fdd4`).
  2. **The rubric's field descriptions.** `archetype` gates the
     `non_technical` cap and is filled FIRST, before any reasoning exists, yet
     carried no test; `skills_match` had the bare description `"0-10"` while
     the frontend rule sat in a profile section about overall fit. Pro read
     the prose and did the forbidden thing anyway (`015236f`).
  3. **`dealbreakers` said "mandatory relocation"** unqualified, while the
     rubric says "relocation the candidate has ruled out" and the profile says
     he relocates anywhere in the US. Verkada's CV posting: every axis 9,
     reasoning positive, apply capped to 2 on a five-days-onsite line, which
     put it below the sheet's floor of 4 and removed it entirely (`1bb0f0a`).
  4. **`DEFAULT_PROSE_MODELS`** was provider-keyed, so cover letters were
     pointed at `gemini-3.5-flash` on Vertex — a model Vertex does not serve
     (`1bb0f0a`).

The fix each time was **relocation, not more prose**: state the rule on the
field the model fills, or key the value by the backend that will receive it.
`7917ada` had already shown a fourth attempt at rubric prose does not stick.
**When a scoring result looks wrong, check where the rule is written before
rewriting it.**

**A premise that was true when written, and quietly stopped being true.**
`PHASE5_PLAN` §4.2 says "the one-page loop already drops from the bottom, so
an ordering *is* a selection." It was true when the model selected ~16
bullets and the loop trimmed two or three — `rank` was then a tiebreak among
choices already made. Feeding it all 21 made `rank` the primary selector, and
`rank` orders bullets WITHIN a role, so comparing across roles asked whether
Gradient Tensor's third bullet is worse than Brainayan's first. Every role
has a rank-1 bullet, so every role survived: 14 bullets over 9 of 10 roles in
all seven variants, and `swe_backend` losing `gt-remittance`, its own
position-1 bullet. Fixed in `c5769fd` by keying on position. **The premise
was not wrong when written; the conditions it held under changed.**

**A test that reimplemented the thing it tested.**
`TestDropOrderIsRelevanceAware` mirrored the drop formula in a local helper
rather than calling the code, so it passed unchanged through the rewrite
above while asserting a policy the code no longer had. The whole suite went
green on a changed drop policy. The decision now lives in `resume.weakest()`
at module level and the tests call it.

**`.env`, a fourth time, and the first three are why it was caught.**
`min_font_pt()` reads `JOBPIPE_MIN_FONT_PT` at call time; `.env` sets 9.0 and
the module default is 10.0. The MCP server never loaded `.env`, so
`render_resume` would have rendered every resume at the 10pt floor — several
bullets thinner, nothing raised, nothing logged (`32e94f2`). My own
verification scripts hit it too, which is how it surfaced: the first "9
bullets, drops 12" measurement was taken at the wrong floor and was wrong.
**Anything that is not the CLI gets no `.env` unless it asks.**

**The pattern behind three of those:** having demoted MAE and bias as measuring
the wrong decision, I kept optimising them anyway. Every intervention aimed at
absolute scores degraded rank. **The profile fix was the only change that
helped, and it worked by removing a rule the model was misreading rather than
by adding one.**

**The checker was told it was reading a letter — a fifth instance of the
pattern above.** `check_grounding` gained a heading for form answers while
the system prompt still said "a cover letter" and the `claim` field "from
the letter". With no section called a letter, Flash Lite took 38 of 45
claims from the job posting's requirement list and judged them as the
candidate's, and a clean answer failed on "You've deployed AI agents in
production", a line it never contained. Fixed by relocation again: one
`kind` drives the prompt, the schema and the heading, and an answer's
`claim` field carries the rule that the posting is evidence, never a source
of claims. The letter's request is byte-identical, checked by hash.

**§6.2's premise was never implemented.** "`advance_status` appends to
SQLite, and `cli sync` renders it" — but `sync` never wrote the status
column, and it did read it, comparing each cell with the database's
*current* status and recording any difference as a human edit. So the first
application recorded through the tool was reverted thirteen seconds later,
by the untouched cell it had superseded. The mirror had no tests: it was a
closure inside `cli sync`. It is `status_mirror.py` now, with nine, and only
a cell that changed since the last sync counts as an edit — `sheet_status`
remembers each reading. **A path with two writers was designed on paper and
tested with one.**

---

## 5a. Start here next session

**Track C's second supervised fill** — and choose the posting for what it
exercises: a form that asks for a cover letter. That runs the one path still
untested live — `save_cover_letter`, `check_grounding`, `copy_for_editing`,
Anish's edit, `measure` — and produces the first letter in the assembled
seven-block shape, which is what the open `why this role` question needs
(§3). Settle the letter-as-a-file question (§3) before that form, not during
it.

Then the third fill, and then mark the rules settled in `CLAUDE.md`.

Three decisions are Anish's and waiting: who owns the sheet's status column
(§3), the stale-posting fix (§4), and when to score the backlog of 89 —
`cli score` spends money, so price it first and run it only when asked.

Tests stay offline: call the tool functions directly, do not stand up a
transport.

`cli score --queue` is the tool for a rubric change from here — it rescores
only queued postings off the current rubric, deletes nothing, and is safe to
re-run. `--rescore` still means "delete all 645 scores"; it is almost never
what you want.

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
