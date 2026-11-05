# Handoff — 2026-11-05

Where things actually stand, what is verified against what is assumed, and
what in older documents has since been overturned.

## Start here (2026-11-05)

A fresh local session needs these, in this order; the dated notes below
are the history behind them.

- **Branches.** `master` is the mainline and is what is checked out in
  `D:\Job_apply_AI`, so the 11:30 scheduled task runs it. `apply-runner`
  and `web-workbench` were fast-forwarded into it on 2026-11-05 and hold
  nothing `master` lacks; new work branches from `master`.
- **The Phase 6 apply runner is in real use** (§8). Three applications went
  through it in review mode - Sierra (Ashby), Chime (Greenhouse), Deepgram
  (Ashby) - each submitted by Anish. Since 2026-11-05 it **records
  `applied` itself after his submit** (unless the ATS shows its error box)
  and **refuses to fill a posting already `applied`** - so run `cli sync`
  first if he applied by hand. Greenhouse's and Ashby's confirmation pages
  are both verified on real submits. Start a review run in its own console
  (`Start-Process` or a terminal), never from a non-interactive shell.
- **Auto-submit has never fired** and stays off (`config/apply.yaml`). Every
  live form so far loads reCAPTCHA, so `--auto` would decide review anyway.
- **Scheduled scoring is on.** `register-task.ps1 -Spend` was re-registered
  by Anish on 2026-11-04 (confirmed: the task runs `daily.ps1 -Spend`). The
  11:30 run scores the US and India queues, each capped at 200 per run.
- **Stage A gained two rules on 2026-11-05**: `graduation_window` and
  `location_remote_abroad` (§8, "later"). Audited, refiltered; 16 postings
  removed by each.
- **The sheet now carries every posting with a status**, ranked or not
  (`db.get_tracked_rows`), so an application outside the daily top 25
  still gets a row.
- **Phase 7 is planned, not started**: `PHASE7_PLAN.md` - an apply session
  in the workbench (Gemini 2.5 Pro drafts essays and letters, Anish
  reviews), then an AI agent for Workday/Lever/SmartRecruiters. Both are
  built in cloud sessions; the prompts are in its §6. Anish will start 7A
  from `master`. Do not build it locally unless he asks.
- **Open, in order of value:** the Greenhouse fill gaps in §8 (Education
  section, Location cleared, phone country, resume read-back timeout,
  Hispanic/Race); the grounding check's output loop (slice 1 of 7A); live
  checks 3 and 4 (a learned answer reused; the required check on a final
  Greenhouse form); InfraCloud's `data_infra` tier is Anish's to confirm;
  the review-wait tests are timing-sensitive and failed twice in one
  under-load run on 2026-11-05.
- **Applications recorded 2026-11-05** (dated that day, not the day he
  applied): through the runner Sierra, Chime, Deepgram; by hand, from his
  batch of 2026-11-04, Affirm, OpenAI (18833), Cohere (25952), Snowflake
  (25733), Stripe (19565), PostHog (20767), `letter_required=false`
  assumed. Perplexity 19713 and 19496 he did not apply to.

**Updated 2026-09-10: Track C is built, and the first application has gone
through it end to end** — resume, two grounded form answers, a supervised
fill, submitted by Anish. That run found three defects the offline suite
could not; they are in §4 and §5. §5a is rewritten for what comes next.

**Updated 2026-10-04: India is a second lane, Workday is a fourth board, and
the whole open backlog was scored.** Anish opened the search to India on
2026-10-03. India roles get a tab of their own and a scoring queue of their
own; Stage A's US verdict is untouched. 26 companies were added, two of them
on a new Workday fetcher. The rubric moved to `2026-10-03.5` and was
calibrated the next day. Track C did not move: still one supervised fill.
What was built is in §2 and §3 ("The India lane"); what a reader would get
wrong is at the end of §5.

**Updated 2026-10-05: the server runs under the desktop app, and the second
application went through Cowork.** jobpipe is registered in the desktop
app's own config as well as `.mcp.json`, and a Cowork task worked LangChain's
Agent Reliability Engineer role (posting 17754) to `applied` on 2026-10-04.
Getting there found that `render_resume` took three minutes against Cowork's
60-second limit on a tool call; it takes 13 seconds now. §3 ("Cowork") has
the setup, §5 the defect. The form asked for no letter, so the letter path is
still unexercised live.

**Updated 2026-11-04: the five waiting decisions are made and built.**
Anish's calls, in one session. (1) An employer's edit replaces the stored
posting text, and an edit to the years or skills asked for sends it back to
be scored - `posting_changes.py`, §4. (2) The india tab's status column is
mirrored into the event log; a role on both tabs is left to the main tab.
(3) The 26 India-lane companies are tiered by domain like the rest: 13 to
tier 2, 13 stay tier 3. (4) Scoring goes back on the 11:30 schedule, US and
India queues both - the GCP credit ends mid-November. (5) Stage A drops
bank and back-office `analyst` titles and `avp`, after an audit; applied
with `filter --refilter`, which took 31 live postings out and added none to
the scoring queue. The scheduled task still has to be re-registered with
`-Spend` from an elevated shell for (4) to take effect.

**Updated 2026-11-04, later the same day: Phase 6, the apply runner, is
built and tested against fixtures, and has not touched a real form.** Anish
changed one guardrail - auto-submit only where nothing checks for bots,
review everywhere else - and `PHASE6_PLAN.md` is the spec. All nine
slices are on `apply-runner`, one commit each; the suite is offline. Two of
the plan's premises did not survive contact with the real pages, and both
ATSes load reCAPTCHA Enterprise on the rendered form, so review is the
main path and auto-submit the exception the plan said it would be. §8 has
what was built, which fixtures are real, what is UNVERIFIED, what has not
run live, and the supervised sequence to run first. Read §8 before
`python -m jobpipe.apply` on anything real.

Read this before `session_2026-08-31_phase5.txt`. The transcript is a record of
what was *said*, and several things in it were corrected by later turns — a
fresh reader going straight through will absorb the wrong versions. The
corrections are in §5.

Read after `PROJECT.md` and `PHASE5_PLAN.md`; for the runner,
`PHASE6_PLAN.md`; for what is next, `PHASE7_PLAN.md`. `CONTEXT.md` §2 and
§4 were remeasured on 2026-11-05; its §9 is superseded by `PHASE5_PLAN.md`.

---

## 1. State

```
read 2026-11-05
1,371 tests, 13 tools on the MCP surface, on master
30,304 postings | 1,227 past Stage A | 1,727 scored
16 live survivors unscored (the 11:30 run scores them)
169 targets on 4 boards
45 applied (3 through the Phase 6 runner) | 2 letters measured
  (only 1 of the 2 counts toward the per-slot means — see §3)
```

Shipped configuration — all four models resolve on Vertex:

```
JOBPIPE_GEMINI_BACKEND=vertex        scoring    gemini-2.5-flash
GOOGLE_CLOUD_LOCATION=global         escalation gemini-2.5-pro
RUBRIC_VERSION=2026-10-03.5          grounding  gemini-2.5-flash-lite
                                     prose      gemini-2.5-flash
```

`JOBPIPE_GEMINI_BACKEND` now has **no default** — it decides which account is
billed, so an unset value raises rather than falling back to the Developer
API. If something suddenly refuses to start, that is the first thing to check,
and the usual cause is a process that did not load `.env`: python-dotenv
searches upward from the **calling file**, not the working directory, so a
script outside the repo root silently gets nothing.

Scheduled run is 11:30, S4U. **Since 2026-11-04 it scores again**
(registered with `-Spend`): fetch, filter, score the US queue, score the
India queue, sync, refresh the india tab. Each scoring stage refuses rather
than bills past `JOBPIPE_MAX_SCORES_PER_RUN` (200). The queue is the
unscored live survivors plus any posting whose employer changed the years
or skills asked for (`posting_changes.py`). Registering without `-Spend`
takes scoring off again; the GCP credit it bills to ends mid-November.
(Between 2026-08-30 and 2026-11-04 it scored nothing and a backlog built up
between deliberate runs.)

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
| **Track C, after fill 1** | `sync` writes untouched status cells back; reusable answers; `prepare_letter` — the letter as text or a read-back PDF |
| **Track C, fill 2** | LangChain Agent Reliability Engineer, GTM (17754), recorded `applied` 2026-10-04 from a Cowork task. No letter asked for |
| **Cowork** | jobpipe registered in the desktop app's config; tools called from a Cowork task; `render_resume` brought under its 60-second limit |
| **The India lane** | `india.py`, `cli india [--sync]`, `cli score --india`, the `india` tab, the daily stage. 170 roles scored and synced live |
| **Workday fetcher** | Barclays (763) and Broadridge (284) fetched live, filtered and scored |
| **Open-only scoring** | `get_unscored` skips postings off their board for 7 days; 20 closed ones were not paid for |

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

**Track B is complete, all four slices. Track C is built and has two
supervised fills behind it; one more, with a letter, before its rules are
settled.** What is
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
server's own credentials. Ten tools on the surface at that point. §5.2's gate is
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

**Track C — BUILT 2026-09-10**, `PHASE5_PLAN` §6.1. Thirteen tools now.
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

- **One more supervised fill, and it should be a form that asks for a
  letter.** Two are done and neither did, so `save_cover_letter`,
  `copy_for_editing` and `prepare_letter` have still only run offline.

**Cowork — WORKING 2026-10-04.** `.mcp.json` is project-scoped: only a
Claude Code session opened on this folder reads it. The desktop app and
Cowork read `%APPDATA%\Claude\claude_desktop_config.json`, which now carries:

```json
"mcpServers": {
  "jobpipe": {
    "command": "cmd",
    "args": ["/c", "cd /d D:\\Job_apply_AI && D:\\python_312\\python.exe -m jobpipe.mcp_server"]
  }
}
```

Three things about it that are not obvious:

- **It goes at the top level of that file**, beside `preferences`. Nested
  inside `preferences` it parses as valid JSON and is ignored.
- **The `cd` is the point.** The app starts servers from its own directory.
  `python -m jobpipe.mcp_server` only finds the package from the repo, and
  `pipeline_tailor.OUTPUT_ROOT` is the relative `output/applications`, so
  from anywhere else documents would be written somewhere else without an
  error. Making that path absolute would remove the dependency; not done.
- **The app reads the file at startup only, and closing the window does not
  quit it.** Quit from the tray icon. The same applies after a change to the
  server's code.

A Cowork task does not load `CLAUDE.md` by itself; it gets the server
instructions, and the prompt should tell it to read `CLAUDE.md` first. The
queue comes from `list_queue`, never from the sheet in a browser tab.

The posting 17754 run left four resume rows (59-62): three renders that
finished after Cowork had stopped waiting, and the one that was used. Each
superseded the last, so only 62 is live.

Decided and done the same day:

- **A letter goes in as text or as a PDF — Anish's call.** `prepare_letter`
  returns the reviewed copy (`cover_letter.sent.md`, never the draft) to
  paste where a form has a letter box, and with `pdf=true` renders it for a
  file field: one page via the resume's Chromium path, then read back and
  refused unless its text is the approved letter word for word. It refuses
  an ungrounded draft, a missing reviewed copy, or a copy older than the
  draft. Checked on the real PostHog letter: 423 words, one page at 11pt.

- **`sync` writes the sheet's status column — for untouched cells only.**
  Anish's call (§6.2). A cell nobody edited since the last sync, whose
  status the database moved past through `advance_status`, is brought up to
  date by `status_mirror.write_back`, compare-and-set through
  `sheets.set_cells`. A cell he edited is never written; his edit goes into
  the database. Verified live: LangChain's blank cell became "Applied", and
  the next sync was a no-op.
- **Reusable answers.** `answers.yaml` has a `reusable_answers` section and
  `form_answers` returns it. The first entry is LangChain's
  customer-experience answer, copied from its checked database row. Using
  one is still a save and a check on every form.

**Web workbench — BUILT 2026-11-04, not yet run on real data.**
`python -m jobpipe.web` serves the queue at http://127.0.0.1:8765 over the
same tool bodies as the MCP server (`jobpipe/web.py`, `templates/web/`,
`tests/test_web.py`); built and tested against fixtures only, so the first
local run is the first time it sees `jobpipe.db`, the profile and
`answers.yaml`. `CLAUDE.md` "Web workbench" says what it can and cannot do.

**The India lane — BUILT 2026-10-03.** Anish's decision: India joins the US
as a place he will work, and he needs no sponsorship there. Profile §10 and
§12 say so, and `form_answers` returns it.

The design is a second lane, not a looser filter. `filters.yaml` still
excludes India and `stage_a_passed` still means "actionable in the US".
`india.select` is the India lane's Stage A: live postings whose location
names India, passed through every Stage A rule EXCEPT location. Three things
follow from keeping the lanes apart:

- **Scoring is a separate queue.** The default queue is Stage A's survivors,
  so it never reaches an India role. `cli score --india` scores them, and it
  needs `--yes` like every other spend.
- **A scored India role stays off the main tab.** `get_sheet_rows` takes an
  `exclude` predicate and `sync` passes `india.lane_only`, applied before
  the cap and the limit so the top 25 still fills. A role open in both
  countries passed Stage A on its US location and shows on both tabs.
- **The india tab has its own columns** - location, `last_seen`, then the
  human ones, then score, fit and reasoning - through a `columns` parameter
  on `sheets.sync`. Rows are never deleted; `last_seen` is how a closed one
  shows.

- **The india tab has a floor of 6 on `score`** (`india.MIN_SCORE`), Anish's
  call on 2026-10-08. A role scored below it is not added; one with no score
  yet is. The sync still never deletes, so the 177 rows already below the
  floor - none with a status or a note - were removed by hand that day, a
  one-off. 23 remained. The scoring queue is unchanged: it is the unscored
  roles, which the floor keeps.

Open in it:

- **The india tab's status column is mirrored - BUILT 2026-11-04.**
  `cli india --sync` runs `status_mirror` over the tab, with readings kept
  per tab (`sheet_tab_status`) and events sourced `sheet_india`. A role on
  both tabs is skipped there: the main tab's cell is its one status.
- **No India role has been hand-scored**, so the calibration below speaks to
  whether the rubric change hurt US ranking and says nothing about whether
  India scores rank well. A hand-scored India set is the missing piece.
- **Tiers - DONE 2026-11-04.** The 26 were classified by domain with the
  definitions in `scripts/classify_targets.py`, and tier follows domain as
  it does for the other 143: ai_infra, ai_saas and data_infra to tier 2
  (13), fintech and product_saas stay tier 3 (13). Tier 1 is still a hand
  pick and none was added. InfraCloud sits in data_infra as the nearest fit
  and wants review. `config/targets.csv.pre-india-tiers` is the backup.
- **Accenture is deliberately out.** Its board lists 36,099 roles in India
  alone; a Workday query returns at most 2,000 and each posting needs its
  own request. The fetcher refuses a board past the cap rather than
  collecting the first 2,000.

**Workday — BUILT 2026-10-03.** `fetchers/workday.py`, `board_token` as
`tenant/wd/site`. The list is a POST (`get_json(body=...)`), 20 per page, and
`total` is only true on the first page - Broadridge reports 0 on every later
one. Each tenant has its own host, so the SSRF allowlist names the two hosts
exactly; a third Workday company needs its host added, and is refused until
then. `cli verify` probes it.

**Calibration of `2026-10-03.5` - DONE 2026-10-04**, over the merged
56-posting set (55 scored, 33 by Pro), result in
`calibration_merged56_v1003_5_result.json`. Against the `.3` baseline:

| | `.3` | `.5` |
|---|---|---|
| Rank agreement (Spearman) | +0.808 | +0.760 |
| False negatives at the cutoff | 0 | 0 |
| Recall / precision at >= 9 | 100% / 38% | 100% / 43% |
| Apply MAE / bias | 1.44 / +0.42 | 1.51 / +0.49 |
| Fit MAE / bias | 1.28 / +0.81 | 1.09 / +0.63 |
| Precision@5 / @10 | 40% / 70% | 20% / 60% |

Read it as: nothing broke, and ranking is a little lower. No posting was
missed, no posting drew a location dealbreaker, and the two holdout roles
hold (Ramp 7 against a hand 8, Scale AI's Advisory Consultant 5 against 6).
Rank agreement is down 0.05 and 14 of 55 postings moved by two points or
more in both directions. **One run each, so that gap cannot be told apart
from run-to-run variance** - nobody has scored the same set twice on one
rubric to measure it. The set is all US postings: this says the India
wording did not damage US ranking, and nothing about India ranking.

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

**Posting text was frozen at first sight - FIXED 2026-11-04.** LangChain
rewrote the Deployed Engineer posting on 08-17 ("3+ years" became "1-3
years") and every score, `get_posting` and grounding's evidence kept the
08-12 text. A live check before the fix found 30-40% of stored Anthropic,
OpenAI and Scale AI postings edited since first sight: roles renamed, pay
ranges filled in, "8+ years" lowered to "6+", and Cursor's boilerplate
rewritten for SpaceXAI. Anish's call: one version, the current one.
`upsert_posting` now replaces edited text (whitespace-only differences are
not edits), re-hashes on a title change, and resets Stage A so the new text
is re-judged - as of `first_seen_at`, so the age rule does not fail a role
for having been open a while, and never clearing a `duplicate:` marking.
If the years floor (`filters.minimum_years_required`) or the skills named
(`posting_changes.SKILL_TERMS`, a fixed vocabulary that leaves out plain
English words like "go" and "rest") changed, `rescore_requested_at` is set
and the posting rejoins the US or India scoring queue; the old score stays
on the sheet until the new one replaces it. Repost detection was never at
risk: `content_hash` is company and normalized title, not the description.
What is lost is the text an older score was computed from - accepted.

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

**Closed rows stay on the main tab when they carry anything hand-typed.** On
2026-10-03, 22 of its 81 rows were postings no longer on their board. The 10
with no status, notes or other human cell were deleted at Anish's request -
a one-off, by hand, not a feature. 12 remain: 8 are his application record,
4 have notes only. The sync still never deletes, and the main tab has no
`last_seen` column, so a closed row there looks like an open one.

**Eight boards returned nothing on 2026-10-03 and logged no error:**
ClickHouse (last seen 08-31), Marqeta (08-31), Postman (09-16), World Labs
(09-08), Amplitude (09-28), Wayve (10-01), Hightouch (10-02) and Temporal
(the known 404). Their postings age out of the 7-day window and read as
closed. Not investigated: a moved board and an empty one look the same.

**Barclays let non-engineering titles through Stage A - FIXED
2026-11-04.** `analyst` satisfied `title_require_any` and `avp` was not
excluded. `filters.yaml` now excludes `avp` and 32 narrow back-office
phrases ("kyc analyst", "business analyst", "operations analyst", ...);
the audit is written beside them. `filter --refilter` took 31 live postings
out and re-admitted 6, all already scored, so it queued nothing.
`--refilter` now judges age as of first sight too (`pipeline.filter_all`).

**The scoring corpus is now five rubrics deep.** none 603, `.2` 3, `.3` 78,
`.4` 1, `2026-10-03.5` 494. The 603 are 3.x-era scores and many still rank.

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

**"A full re-score costs ~85¢" is a 3.x-era figure, and it priced a real
run wrong.** `CLAUDE.md` and `CONTEXT.md` §6 both carried it. On 2026-10-03
it was used to estimate 499 postings at about 65¢. The runs used 10.05
million input tokens (4.0 million cached) and 162,000 output tokens, with
210 escalations to 2.5 Pro - several dollars at list prices, not cents. The
exact figure is on the GCP billing page and was not read. **Budget a scoring
run from tokens: about 20,000 input tokens per posting including its share
of escalations.** `CONTEXT.md` §7 already said to budget from the escalation
rate; the cents figure sat beside that advice and contradicted it.

**The rubric never treated a non-US location as a dealbreaker.** That was
said, in the 2026-10-03 session, as the reason the india tab started
unscored. The field said location is NEVER a dealbreaker because he
"relocates anywhere in the US" - which leaves a Bengaluru role outside the
sentence, not inside a ban. The exclusion of India lived in `filters.yaml`
and nowhere else. `.5` names both countries on the field.

**`render_resume` was reported hung, and it was only slow.** Cowork saw
"device did not respond within 60s" three times and concluded the PDF step
had stuck. Each of those renders finished and wrote its row. The one-page
loop measures every font and short-text pair again after each dropped
bullet - 80 measurements for a 21-bullet variant - and each one launched its
own Chromium: 175 seconds, measured. `resume.one_browser()` now holds one
open for the whole render: 13 seconds, same bullets, same drops. Nobody had
noticed because Claude Code waits longer than Cowork does. **A tool that
times out has not necessarily failed; look for what it wrote before
retrying**, since every retry here was another full render and another row.

**Deleting a row from the sheet does not stop it being scored.** Scoring
reads the database, where nothing is deleted. What stops a closed posting
being paid for is the `last_seen_at` window on `get_unscored`.

---

## 5a. Start here next session

**Track C's third supervised fill** — and choose the posting for what it
exercises: a form that asks for a cover letter. Fill 2 went to a form
without one, and a Cowork task cannot tell from the queue which forms have a
letter field; it has to open the form first. That runs the one path still
untested live — `save_cover_letter`, `check_grounding`, `copy_for_editing`,
Anish's edit, `measure` — and produces the first letter in the assembled
seven-block shape, which is what the open `why this role` question needs
(§3). Whether the form wants the letter pasted or uploaded, it comes from
`prepare_letter`.

Then mark the rules settled in `CLAUDE.md`, which is Anish's call.

**Phase 6 comes after that, not instead of it.** The apply runner has never
opened a real form. §8 lists the supervised sequence - dry runs on two or
three real postings first - and the four things it will tell us that the
fixtures cannot.

Two Perplexity roles were in progress in the same Cowork task when this was
written: 19496 has a resume, and 19713 has a resume and two form answers
that passed grounding. Neither has a status event, so check `status` before
assuming either went out.

The decisions that were waiting here were all made on 2026-11-04 (top of
this file). Scoring is scheduled again; a manual `cli score` or
`cli score --india` outside the schedule still spends money - price it from
tokens (§5), say the figure, and run it only when asked.

A hand-scored India set is what would let the India ranking be measured
rather than assumed. `calibrate` skips any posting Stage A discards, which
is every India-only role, so that needs a small change first.

Tests stay offline: call the tool functions directly, do not stand up a
transport.

`cli score --queue` is the tool for a rubric change from here — it rescores
only queued postings off the current rubric, deletes nothing, and is safe to
re-run. `--rescore` still means "delete all 1,179 scores"; it is almost never
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
143 companies and verified board tokens. The personal file has 169: the 26
added on 2026-10-03 are not in the example.

`README.md` is a setup guide with nine ordered steps. The step people miss is
sharing the Google Sheet with the service account's email — Sheets permissions
and Cloud IAM are separate systems.

**Before any push, check `git status` and `git ls-files config/`.** The latter
should show only `*.example.*` plus `filters.yaml` and `states.yml`.

---

## 8. Phase 6 — the apply runner (built 2026-11-04, not run live)

`PHASE6_PLAN.md` is the spec; this is where it stands. Everything below was
built in a cloud session against fixtures and the full offline suite, and
**nothing has been run against a real form, a real browser profile, or the
real database.** The first local run is the first time any of it meets
`jobpipe.db`, `master_profile.md` and `answers.yaml`.

### What was built

| Slice | Where | State |
|---|---|---|
| 0 | `PROJECT.md`, `CLAUDE.md` guardrail text; `config/apply.example.yaml`; `apply_runs` table; `ready_for_review`, `submit_unconfirmed` states; `advance_status(source="agent")` | done |
| 1 | `jobpipe/apply/spec.py` - Greenhouse JSON and **rendered Ashby form** parsers | done, real fixtures |
| 2 | `resolve.py` - every field a source or a blocker | done, one test per table row |
| 3 | `signals.py` - bot-check detection, stop only | done |
| 4 | `fill.py`, `browser.py` - type, upload, read back (SHA-256), screenshot | done, Playwright tests offline |
| 5 | `decide.py` - the eight conditions | done, one test per condition |
| 6 | `submit.py` - one click, confirmation, never a retry | done, fake ATS |
| 7 | `runner.py`, `python -m jobpipe.apply <id> [--dry-run\|--review\|--auto]` | done, dry run default |
| 8 | workbench: `/fill`, `/fill/close`, run screenshots, the run report, a needs-attention tab | done |
| 9 | this section; `CLAUDE.md` "Filling an application form" | done |

Suite on the cloud checkout: 1,050 passed, 2 skipped; the 36 failures and
126 errors are the ones a clean checkout always has (gitignored
`bullets.yaml`, the profile, `cli ground`, and a Playwright browser
mismatch in that container). Phase 6 adds about 260 tests, all offline.
Browser tests use `JOBPIPE_CHROMIUM` to point at a Chromium that is not
Playwright's download; on Anish's machine, where the download exists, leave
it unset.

### Where the plan was wrong, and what was done instead

1. **Ashby's form is not in `window.__appData`.** `__appData` carries the
   posting, the organisation and the reCAPTCHA site key; the form arrives
   by a GraphQL POST (`/api/non-user-graphql?op=ApiJobPostingForApplicationRequest`,
   `applicationForm { sections { fieldEntries { field, isRequired, ... } } }`)
   that the fetching service used here could not make. The parser reads the
   **rendered form** instead: every question is a
   `ashby-application-form-field-entry` div with a `data-field-path` key
   (`_systemfield_name`, `_systemfield_email`, `_systemfield_resume`,
   `_systemfield_location`, or a UUID), a label with a `_required_` class,
   and one widget. Consequence: an Ashby dropdown's options are not in the
   DOM until it is opened, so the filler reads them from the open listbox
   and matches exact text there. The GraphQL route is the better source once
   someone can capture one response locally; `spec.py`'s docstring has the
   operation name.
2. **Alloy's board does load reCAPTCHA.** §1 sampled static HTML; the
   rendered embed page loads `recaptcha/enterprise.js` and defines
   `grecaptcha`, as Ashby's application page does. Expect `signals.py` to
   fire on nearly every real form. `decide.py` will say `no_bot_check` and
   the run goes to review. That is the design working, not a bug - but it
   means the auto-submit path may never open on these two ATSes, and the
   first live runs should confirm or refute that before anyone tunes it.
3. **The Greenhouse form page.** `job-boards.greenhouse.io/{token}/jobs/{id}`
   redirected to Alloy's own site with the form embedded. The runner opens
   `job-boards.greenhouse.io/embed/job_app?for={token}&token={id}`, which
   rendered `#application-form` inline with no iframe. `fill.py` searches
   frames anyway.
4. **§12 holds one `name`.** A form asking for first and last names gets a
   split only from `first_name`/`last_name` keys in §12 if present, or from
   a two-word name; "Anish Kumar Naik" is three words and is a **blocker
   that names the fix**: add `first_name` and `last_name` to §12 before the
   first review run, or every Greenhouse form stops on it.

Two readings of the resolver table are deliberate and documented in
`resolve.py`: "blocker" means a *required* field with no source, and an
*optional* field with no source is left blank and listed; and the daily cap
in `decide.py` is a rolling 24 hours.

### Fixtures: real and UNVERIFIED

Real, captured 2026-11-04 (`tests/fixtures/apply/README.md`): Alloy's and
Datadog's Greenhouse `?questions=true` JSON, Alloy's rendered Greenhouse
form, LangChain's rendered Ashby application page and its `__appData`.

UNVERIFIED, marked so in code and in the README:

- `demographic_questions` on Greenhouse: parsed per the documented shape;
  no sampled posting carried one.
- **Greenhouse's confirmation page.** `submit.py` recognises Ashby's
  success box from Ashby's own bundle (`ashby-application-form-success-container`,
  role=status, "Your application was successfully submitted"; error box
  "We couldn't submit your form."). Greenhouse's job-boards bundle was not
  reachable, so its thank-you wording and a `confirmation` URL segment are
  assumed. A miss records `submit_unconfirmed`, the safe outcome. The first
  real submission - Anish's own click, in review mode - should capture the
  confirmation page's text and URL and replace the patterns.
- The pages under `tests/fixtures/apply/pages/` are **test doubles**
  modelled on the real markup, with the widget behaviour scripted. They
  prove the filler's logic, not Greenhouse's react-select or Ashby's
  autocomplete.

### What has not run live

All of it. In particular: react-select on a real Greenhouse form (the
double mimics its single-value/`data-value` structure), Ashby's location
autocomplete against its live suggestions, the phone widget's country
selector (the runner types the digits and leaves the country at its
default), the frame search on a company-hosted embed, the lock file and the
review window on Windows, and the web-started runner holding its stdin.

### The supervised sequence (PHASE6_PLAN §6), in order

1. Add `first_name` and `last_name` to `master_profile.md` §12. Copy
   `config/apply.example.yaml` to `config/apply.yaml`; leave `auto_submit:
   false`.
2. `python -m jobpipe.apply <id> --dry-run` on two or three real postings,
   one Ashby and one Greenhouse, with a resume already rendered for each.
   Read the report: every identity field `planned` from a `profile:` source,
   the resume `planned` from `render_resume`, self-ID `select` or `blank`
   with the reason, the salary/attestation rows `blocker`. Expect
   `signals_before: [recaptcha]`.
3. `--review` on one of them, watched. The window stays open; check every
   value against the report (the report carries hashes; the page carries
   the text), check the upload went through the field's own input, then
   submit it yourself. **Capture the confirmation page** (text and URL)
   and update `submit.py`'s Greenhouse patterns. `advance_status` to
   `applied` as today, then `cli sync`.
4. Only if a form ever shows no bot check at step 2: set `auto_submit:
   true`, `--auto` on that one posting, watched live, with the daily cap
   at 1 for the first week.
5. The two or three fills that Track C's rules still want apply to the
   runner too; `CLAUDE.md`'s form rules say so.

### First live dry runs, 2026-11-04 (local)

`first_name: Anish Kumar` / `last_name: Naik` added to §12 at Anish's word;
`config/apply.yaml` created with `auto_submit: false` and a cap of 1.
Three dry runs, nothing typed or clicked:

- **Harvey (21865)**: the posting was gone from Ashby hours after the
  fetch saw it; the runner stopped with `no form` and a screenshot. Correct.
- **Sierra (27481, Ashby)**: parsed from the live DOM. Two resolver misses,
  fixed with tests: "Legal First/Last Name" did not match the name patterns,
  and a UUID-keyed Location autocomplete (a select with no options until
  opened) was a blocker instead of an exact match at fill time. After the
  fix every required field is planned. Signals: `recaptcha`, `challenge_iframe`.
- **Affirm (24670, Greenhouse)**: the questions JSON and the embed page both
  worked. "How did you first learn about…" missed how-heard (fixed). Left
  as blockers, correctly: required Pronouns (stored `he/him/his`, option
  `He/him/his` - exact match only), state of residence, and "previously
  employed at Affirm". Veteran left blank (stored `Not a veteran`, option
  `I am not a protected veteran`). Signal: `recaptcha`.

So far every live form carries reCAPTCHA: review is the path, as expected.

Later the same evening: the work-authorization answers split into
`requires_sponsorship_now` / `requires_sponsorship_future` in §12 (forms ask
both; the resolver reads the future flag when the label mentions the
future), and `answers.yaml` gained `state_of_residence` /
`state_abbreviation` plus the Greenhouse spellings of two self-ID answers.

### The first application through the runner - Sierra, 2026-11-04

`--review` on Sierra (27481), started in its own console so the window
stayed open. Name, legal name split, email, phone, LinkedIn and the
resume filled and read back equal. A cover letter went into the optional
"Anything else?" box: drafted in-session, grounding passed 49/49,
`copy_for_editing`, pasted by Anish. Anish answered the rest and submitted;
`applied` recorded with `letter_required=false` (the box was optional, so
`measure` does not count it).

**What it found - a parser gap that hid required questions.** Ashby's
radio and checkbox groups sit in a `data-field-path` div *without* the
`ashby-application-form-field-entry` class (the class is on their inner
`<fieldset>`). `spec.py` keyed on the class, so six questions - five of
them required - were absent from the plan and the report, and the dry run
read as "every required field planned" when it was not. With a form that
had no bot check, the `no_blockers` condition would have passed on fields
nobody knew existed. Fixed: every `data-field-path` div is a field; a new
`radio` kind carries its options from the DOM, resolves by exact text, and
fills by clicking the matching label and reading back the checked one.
Sierra's page is now a fixture (`ashby_sierra_0dfe7805_application.html`),
and a test asserts every `data-field-path` on it becomes a field. The live
dry run now shows all 16 questions.

**The lesson for auto-submit:** a parser that misses a widget fails
*open*. Before the first `--auto`, a run should also count the form's
required markers in the DOM and refuse when that number disagrees with the
spec's - an independent check that does not share the parser's blind spots.
Not built yet.

Also learned: a review run started from a non-interactive shell closes its
browser immediately (`input()` gets EOF). Start it with `Start-Process` or
from a terminal; the workbench's runner holds stdin open for the same reason.

### 2026-11-05: five items from the first live runs (cloud, against doubles)

Built on `apply-runner` after Sierra, one commit each, all offline:

1. **The reviewed letter goes into "anything else" boxes.** An
   additional-information *textarea*, required or optional, takes
   `prepare_letter`'s text (it reads only `cover_letter.sent.md`), source
   `prepare_letter`; a single-line box never does; without a reviewed
   letter the box is the open-ended question it was. `build_sources` asks
   `prepare_letter` for such forms too. The runner still writes no prose.
2. **"Have you previously worked at {company}?" answers No** from the
   bank's experience roles (`jobpipe/apply/employers.py`), only when the
   label names this posting's employer - "Cursor (Anysphere)" by either
   name - and only as an exact "No" option. A past employer, an unloadable
   bank, or other wording is a blocker. Posting company and employers now
   ride on `resolve.Sources`.
3. **Remembered answers** (`jobpipe/apply/learned.py`,
   `config/learned_answers.yaml`, gitignored; the example ships). Key: the
   question, whitespace collapsed, case-folded, this company replaced by
   `{company}`; an option that names the company is templated the same way
   and expanded before the exact match (Sierra's "Yes, I am based in one of
   Sierra's office locations." serves the same question elsewhere). Choice
   widgets only; never self-ID, never-fill rows, or profile facts; reused
   only where the table left a blocker or a blank; shown as
   `learned:<company> #<posting> <date>`.
4. **The review wait** (`jobpipe/apply/review.py`) replaces `input()`:
   polls every 2 s, ends on a stdin line or a closed browser, never on EOF
   (stdin is read on a daemon thread). When the form disappears after his
   submit it saves `after-submit.png`, `after-submit.html` and
   `confirmation.json` (URL, body excerpt, `submit.classify`'s verdict) to
   the run folder, records his choices on the questions the runner left,
   and prints both. **It does not advance status.** The workbench path is
   unchanged: `/fill/close` still writes a newline and closes stdin.
5. **The ninth decide.py condition, `required_match`**
   (`jobpipe/apply/required.py`): the page's own required markers - Ashby
   title labels with `_required`, by `for`; Greenhouse `aria-required`
   controls by id or `aria-labelledby` - must all be spec keys, with
   Greenhouse's phone `country` the one named exception. Not read fails;
   dry runs run it; the report carries `required_on_page` and
   `required_missing`. Sierra's real page agrees on all 13 required
   questions, and with the radio groups dropped from the spec the check
   names the five that went missing on 2026-11-04.

Two parser fixes came out of the doubles and apply to real pages: Ashby
field parsing ignores script source (a page that builds itself inline would
otherwise yield every field twice), and an `<input>` with no `type` is text.

**Verified only against the doubles**, not a real page: the review wait's
end conditions and capture; `read_choices` on a real react-select or Ashby
autocomplete; the letter landing in a real "anything else" textarea; a
learned answer matched live in an Ashby listbox; the Greenhouse
`aria-required` scan on a live form (the captured Alloy excerpt agrees; the
live page has not been read by this code). Suite on the cloud checkout:
1,163 passed, 2 skipped, the same 36 failures and 126 errors a clean
checkout always has (gitignored `bullets.yaml`, the profile, `cli ground`,
and that container's Playwright browser mismatch).

**Live checks still owed, in this order** - all `--review`, none `--auto`:

1. **Affirm (24670, Greenhouse)** in `--review`. Anish submits; the wait
   captures the confirmation page. Update `submit.py`'s Greenhouse patterns
   from `confirmation.json` and the screenshot (they are UNVERIFIED until
   this). Check that his choices on the questions the runner left landed in
   `config/learned_answers.yaml`, and that the pronoun and
   state-of-residence rows read as expected.
2. **The same on one Ashby form.** Confirm `verdict: confirmed` from the
   success box, and that the radio choices were recorded.
3. **A dry run on a third posting** that asks one of the remembered
   questions: the report should show `learned:` as the source.
4. **The required-markers check on real pages of both ATSes** -
   `required_missing: []` in a dry run's report on each - before anyone
   sets `auto_submit: true`. Every live form so far loads reCAPTCHA, so
   `--auto` would decide review regardless; the check is for the day one
   does not.

### 2026-11-05: live check 1 done - on Chime, not Affirm

Affirm turned out to be one of seven postings Anish applied to by hand on
2026-11-04 without syncing, so the database said `not_applied` and a review
run opened on it; it was closed unsubmitted and recorded `applied`. Two
decisions followed (`0369e33`): the runner **refuses review and auto on a
posting already `applied`**, and **after his submit it records `applied`
itself** (`source="agent"`) unless the ATS shows its error box. Sync the
sheet before a run if he has applied by hand.

Check 1 ran on **Chime, Software Engineer, Jade (29307, Greenhouse)**:
review mode, resume rendered for it, a cover letter written in-session,
grounding passed, approved, and attached by Anish as the
`prepare_letter(pdf=true)` PDF. He submitted; the wait captured
**Greenhouse's first real confirmation**: URL
`job-boards.greenhouse.io/embed/job_app/confirmation?for=chime&token=...`,
text "Thanks for your interest in Chime! Our team will review your
submission...", verdict `confirmed`. `submit.py`'s Greenhouse patterns are
now verified against it (fixture `greenhouse_chime_8868948002_confirmation.html`),
`applied` was recorded by the runner, and `learned_answers.yaml` gained its
first entry (Country).

Fixed from what the live Greenhouse forms showed:
- **Demographic questions** render with their bare numeric id, not the
  spec's `demographic_question_<id>`: the filler could not find them and the
  required check reported Chime's six required ones as missing
  (`0c6e58a`, `spec.page_id`).
- A **non-compete question** ("an agreement with a former employer") was
  read as have-you-worked-here and so never remembered; now it is an
  ordinary unrecognised question. A have-you-worked-here question for a
  company checked *not* to be a past employer, whose options are sentences
  ("I have not previously been employed at Affirm"), is now learnable, so
  his pick is reused at other companies; a past employer never is.

Still open from these runs:
- **Greenhouse's Education section** (school, degree, discipline, dates) is
  on some live forms and absent from the questions JSON; the required check
  catches it (Affirm). Fill it from §12 `education`.
- **Location (City)** on Greenhouse is cleared unless a suggestion is
  picked; the runner types it and reads back equal, then it empties.
- **Phone country** on Greenhouse is required and left unset.
- **Resume read-back times out** on Greenhouse (the file is attached; the
  input changes under the locator after Greenhouse parses it). Greenhouse
  also autofills School/Degree from the parsed resume - not a jobpipe
  source; Anish checks them.
- **Hispanic/Latino and Race** (Greenhouse EEOC, conditional) were not
  filled on Affirm.
- **Grounding check looped** on one Chime draft: Flash Lite produced
  ~285k characters of claims, identically twice, and the JSON was cut off.
  A reworded draft passed. Cap its output and report a loop plainly.

Checks 2-4 above still stand (an Ashby submit through the wait, a learned
answer reused, the required check clean on both ATSes).

### 2026-11-05, later: check 2 (Ashby), the sheet, two Stage A rules

**Check 2, mostly done.** Deepgram, People AI & Automation Engineer (18642,
Ashby): review mode, Anish submitted, verdict `confirmed` from Ashby's
success box, `applied` recorded by the runner within a second - with a
reCAPTCHA iframe still on the confirmation page, which is why a leftover
challenge signal is not read as failure. Not exercised: learning on Ashby
(that form held only profile and self-ID questions). The required check
came back clean on four live Ashby pages.

**The sheet now shows every application.** It added rows only from the
ranked top 25, so an application outside it (Deepgram) never got a row.
`cli sync` now also writes every posting with a status other than
`not_applied`, whatever its rank, score or age (`db.get_tracked_rows`).
Verified in the real sheet: Deepgram on the roles tab, status Applied.

**Two Stage A rules**, after the scorer rated postings 9/10 that one line
of the posting ruled out (`config/filters.yaml` 7b, `jobpipe/filters.py`):
- `graduation_window`: a new-grad window that leaves out a May 2025
  graduate ("graduating between December 2026 and July 2027", "within the
  last 6 months"). "Graduating by <date>" is deliberately not matched.
- `location_remote_abroad`: a bare "Remote" beside a non-US place ("London;
  Remote") - Harvey's form then asked to be based in London. A US city or
  a US state token keeps the posting.
Audited before enabling: 27 previously passing postings removed, every one
checked by hand; two first-draft false positives (Nuro's "graduating by",
Luma's "Redwood City, CA") were fixed before the refilter. Refilter on
2026-11-05: 16 by each rule across the corpus. The next scoring run will
not pay for them.

Applications recorded today, 2026-11-05: Sierra, Chime, Deepgram through
the runner; Affirm, OpenAI (18833), Cohere (25952), Snowflake (25733),
Stripe (19565), PostHog (20767) from Anish's batch of the day before,
`letter_required=false` assumed for those six.

### Open

- The new states are in `states.labels()`, which feeds the sheet's status
  dropdown on the next `sync`; nothing writes them to the sheet, but they
  will appear as choices.
- `needs attention` on the queue reads the last `apply_runs` row per
  posting; a posting with blockers stays there until a later run clears
  them or Anish records a status by hand.
- §8 of the plan, the AI browser agent for other boards, is not started.
