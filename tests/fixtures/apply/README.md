# Phase 6 fixtures

Real public data, captured 2026-11-04 through a fetching service (this
environment cannot reach either ATS directly). Nothing here is invented.

| File | What | Source |
|---|---|---|
| `greenhouse_alloy_8495229002_questions.json` | Greenhouse `?questions=true` JSON. Has a **required salary question**, hidden location fields, EEOC blocks | `boards-api.greenhouse.io/v1/boards/alloy/jobs/8495229002?questions=true` |
| `greenhouse_datadog_8053714_questions.json` | Same shape. Has **"I certify…" and privacy attestations**, two multi-selects, `preferred_name` | `boards-api.greenhouse.io/v1/boards/datadog/jobs/8053714?questions=true` |
| `greenhouse_alloy_8495229002_form.html` | The **rendered** application form (react-select comboboxes, file inputs, submit button). Country list collapsed, SVGs emptied | `job-boards.greenhouse.io/alloy/jobs/8495229002`, rendered |
| `ashby_langchain_74e5f9f4_application.html` | The **rendered** Ashby application page: field-entry divs with `data-field-path`, yes/no buttons, autocomplete, `g-recaptcha-response`, `window.__appData`. Styles dropped | `jobs.ashbyhq.com/langchain/74e5f9f4-…/application`, rendered |
| `ashby_langchain_74e5f9f4_appData.json` | `window.__appData` from that page: posting, organisation, `recaptchaUniversalPublicSiteKey`. Description trimmed | same page |
| `ashby_sierra_0dfe7805_application.html` | The **rendered** Ashby application page with **radio and checkbox groups** (whose `data-field-path` div lacks the field-entry class), "Legal First/Last Name", and a UUID-keyed Location autocomplete. Captured locally with Playwright, 2026-11-04; styles and SVGs dropped | `jobs.ashbyhq.com/sierra/0dfe7805-…/application`, rendered |

The `content` / `descriptionHtml` posting text is truncated in the JSON
files; the form is what these are for.

`demographic_questions` were seen live on Affirm and Chime (2026-11-05);
the rendered control uses the bare numeric id (`spec.page_id`).

| `greenhouse_chime_8868948002_confirmation.html` | Greenhouse's **real confirmation page** after a submit, captured by the review wait. No personal data on it | `job-boards.greenhouse.io/embed/job_app/confirmation?for=chime&token=8868948002` |

**Not captured, and why:** Ashby's form definition JSON
(`POST /api/non-user-graphql?op=ApiJobPostingForApplicationRequest`). The
fetching service does only GETs. The parser reads the rendered form instead,
which is what the filler drives anyway.

## `pages/` - test doubles, not captures

`greenhouse_form.html`, `ashby_form.html`, `recaptcha_form.html`,
`fake_ats.html`, `fake_ats_precaptcha.html` and `widgets.js` are pages
written for the Playwright tests, modelled on the two real captures above:
the same ids, classes and `data-field-path` keys, with react-select,
autocomplete, yes/no and submit behaviour scripted in `widgets.js`. They
prove what `fill.py` and `submit.py` do and never do; they do not prove
what Greenhouse's or Ashby's real components do. The fake ATS's success and
error markup follow what was verified for Ashby and, since 2026-11-05, for
Greenhouse (`submit.py`).

`fake_ats.html` also takes `?auto_after=<ms>` (the page submits itself,
standing in for Anish's click during a review wait), `?outcome=gone` (the
form leaves with nothing recognisable after it), and carries a radio
question the runner has no source for, which is what the review wait
remembers. The Greenhouse double has the phone widget's `country`
sub-field and the resume group's `aria-required` markers as on the real
Alloy page, for the required-markers check.
