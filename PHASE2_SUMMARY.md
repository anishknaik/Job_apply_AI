# Phase 2 — Summary

Queued role → tailored resume PDF → cover letter → verified. Two stages split
by a human decision, because resumes are cheap and reusable while letters are
neither.

## What runs

| Stage | Detail |
|---|---|
| Bank | 20 immutable bullets across 10 roles, every one traced to the resume or capstone |
| Selection | Flash-Lite, bullet ids compiled into the response schema as an `enum` |
| Render | Playwright → PDF, one page enforced, ATS parse verified against the output |
| Letter | Gemini 3.5 Flash, few-shot from his own letters, banned-phrase and closing-shape rules |
| Grounding | Flash-Lite, every claim routed to evidence that can settle it |
| Measure | Normalised Levenshtein per letter and per paragraph slot |

```
cli tailor --queued --yes          batch: a resume per queued role
cli tailor --cover-letter N --yes  one letter, verified
cli measure [--detail]             what you changed in what you sent
```

## Measured

- **Selection** ~9,700 in / 340 out per call, **79% cache hit**. Squarely in
  §5's 0.3–0.5¢ estimate.
- **Letters** 332–374 words, grounding 100%, first attempt, no banned phrases.
- **Prose regenerates at 0.53 distance from itself** at temperature 0. The
  single most consequential measurement in the phase — see below.
- **9pt fits 15 bullets; 10pt fits 9.** One point of type, six bullets.
- 547 tests, no network.

## Decisions that departed from the spec

**`gemini-3.1-flash` does not exist.** §5 names it; ListModels says the 3.1
generation ships only flash-lite. Same trap as addendum §2's dead
`gemini-2.5-flash-lite` in Phase 1. Using `gemini-3.5-flash` — the cheapest
non-lite flash — and deliberately not `gemini-flash-latest`, since an alias
silently changes model underneath a calibrated prompt.

**§3.2's 10pt floor rests on a false premise.** It gives "ATS parse risk" as
the reason. Measured: a 9pt render extracted 30/30 fields and 5,032 characters
with nothing missing, because extraction reads the text layer and point size
does not touch it. The floor stays at 10pt by default anyway — the real
argument for it is human readability, which is the candidate's call — and
`JOBPIPE_MIN_FONT_PT=9.0` lowers it deliberately. It is set to 9.0.

**§4.1's account of the letters is close but wrong in two places.** It says
the NORC letter opens with a reason for wanting the work rather than a title
restatement; all seven open by restating the title. The real distinction is
the SECOND sentence — NORC states a position ("I treat evaluation as an
experimental design problem rather than a leaderboard exercise") where the
weak three recite a credential ("I graduated from Rutgers with a B.S."). And
the closing tell is broader than stated: three letters use "bring that
combination of X, Y and Z" verbatim, but **six of seven** close on "welcome
the chance" in some form.

**Banning phrases moved the tell rather than removing it.** With the phrase
list in place, three generated letters closed on "look forward to discussing
how my experience [with X] can support [Company]'s [thing]" — same template,
different words, zero bans triggered. The fix had to describe the *shape*: no
closing whose subject is "I" and whose verb expresses eagerness, no
summarising the letter, plus four concrete alternatives.

**Bullet ids are a schema `enum`, not an instruction.** §2.1 asks for
fabrication to be structurally difficult rather than discouraged. Compiling
the bank's ids into the response schema means the API rejects an unknown id
before it reaches us. `Bank.select` re-checks anyway, so a provider that
ignores enums fails loudly instead of silently dropping a bullet.

**Claims are routed to evidence that can settle them.** §2.2 describes one
check against the master profile. That would flag "Abridge maps summaries to
ground truth via Linked Evidence" as unsupported — a true sentence — and would
let an invented product name through. So `candidate` claims are checked
against the profile and bank, `employer` claims against the posting.

**§1 conflicts with Phase 1's human-owned columns.** §1 has `tailor` write
`status` and `resume_version`; Phase 1 says the pipeline never touches them.
Resolution: that rule is about *sync*, a bulk rewrite where a bug erases a
week of hand-tracking. `sync` still never writes them. `tailor` writes named
columns on one row, conditionally — if the sheet no longer says "Queued", the
write is skipped rather than reverting a human edit.

**Sub-project groups, not in the spec at all.** The bank had no concept of a
named workstream inside a role, so Gradient Tensor's three projects collapsed
into one flat list, losing "NBS 7 Azure Deployment" and its stack line — where
most of an experience section's ATS keywords live. Found by the candidate
reading the output, not by any test.

**Letter scaffolding is assembled in code.** Date, salutation and sign-off are
fixed text built from the profile. A model asked to write a salutation will
eventually invent a hiring manager's name.

## The measurement that changed a design

Edit distance was going to compare the generated file on disk against the
edited one. The first real edit renamed the file rather than copying it, and
the baseline was gone.

The obvious repair — regenerate and compare against that — is wrong, and only
measuring showed why: **prose regenerates at 0.53 distance from itself.** A
comparison against a fresh generation returned 0.736, which would have been
reported as human editing when it was almost entirely model variance.

So generated content is stored in the `documents` table, not merely pointed
at. That is a correctness requirement, not a convenience: without a preserved
baseline, §7's metric is noise wearing the costume of signal.

## Bugs worth remembering

Every one of these was found by producing a real artefact and inspecting it,
not by reasoning about the code.

- **The parse check verified text that was never rendered.** The one-page loop
  fell back to short variants; the checker tested the long text. Five failures,
  one bug, all in the checker.
- **Height measured at the wrong width.** `scrollHeight` at Chromium's default
  1280px viewport against a 710px printable width — text wrapped less when
  measured than when printed, so the loop dropped six bullets it did not need
  to.
- **`page-break-inside: avoid` was actively harmful.** Right on a multi-page
  document; here the output is enforced to one page, so there is never a break
  to avoid — and the rule ejects a whole block when content is 2% over.
- **A hyphenated compound split across lines.** `citation-grounded` extracts as
  `citation-\ngrounded` or fuses to `citationgrounded`; neither matches. No
  honest render-time fix exists — a non-breaking hyphen changes the codepoint,
  which is the ligature bug in a new hat — so it is reported, not patched.
- **The font floor was read at import.** The CLI imports `jobpipe.resume`
  before calling `_load_env()`, so every resume silently rendered at the 10pt
  default however `.env` was set. Nothing errored; documents were four bullets
  thinner than configured.
- **`measure` counted one edited letter four times.** Superseded document rows
  point at the same file, so the mean was dragged toward a number nobody
  produced.
- **A stale `.sent.md` was measured as signal.** Regenerating leaves an edit of
  a previous draft beside a fresh one. Now refused, with the reason stated.

## What the system found about its own inputs

The grounding check exists to catch fabrication. It caught two cases of the
profile being *incomplete*, which is the same failure seen from the other side:

- **Co-founder.** A cover letter in the sample corpus opened "As the
  co-founder of an AI company", a claim absent from `master_profile.md`. It
  was true; the profile understated him, and 466 Phase 1 scores had been
  computed against a record showing no founding experience.
- **Relocation.** A letter claimed willingness to relocate to San Francisco.
  The check blocked it because the profile listed on-site outside NYC metro as
  a near-dealbreaker. The claim was true and the profile was stale.

Both are now recorded, and the corpus was re-scored against the corrected
profile.

## Deferred, deliberately

- **`ml_platform` and `swe_generalist` variants.** §3.1 ships two; revisit
  after ten applications if a posting was served badly by both.
- **`research_healthcare` variant.** The likeliest third, leading with the
  CDC/HL7 work. Measure before building.
- **Cover letter PDF rendering.** Letters are Markdown; every ATS accepts a
  paste, some also offer a file upload.

## Known limits

- **Edit distance has no data.** One letter has been edited; its baseline was
  destroyed before the fix. The trend needs ~15 and cannot be hurried.
- **Letters run 332–374 words** against the candidate's own 360–431. Inside
  range, consistently at the low end.
- **Selection concentration is weaker than intended.** The prompt asks for few
  roles; the model spreads across nine or ten, so the renderer drops one or
  two bullets that a tighter selection would have kept.
- **The grounding check shares a model family with the writer.** It is
  adversarially prompted and runs on a different tier, but a blind spot common
  to both would not be caught.
- **`non_technical` short-circuits to 1** and is the most damaging label in the
  system. It was defined by domain words until a real posting exposed it —
  "Marketing Engineer" is an engineering role.