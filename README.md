# jobpipe

A personal job-search pipeline. It finds roles on company job boards, scores
them against your profile with an LLM, puts the best on a Google Sheet, and
generates a tailored one-page resume and cover letter for the ones you decide
to apply to.

It is built around one bet: **filtering quality beats application volume.**
Forty mediocre applications lose to eight targeted ones, so almost everything
here is machinery for throwing postings away accurately.

Built for one person originally, so you will find their name in the docs.
Change it or don't; nothing depends on it.

---

## What it costs

| Stage | What runs | Cost |
|---|---|---|
| Fetch, dedupe, Stage A filters | deterministic, no model | **$0** |
| Stage B scoring | one LLM call per surviving posting | ~0.2¢ each, ~2.5¢/day |
| Resume | one constrained call, no prose | ~0.4¢ |
| Cover letter | prose, deliberately not cost-optimised | ~2¢ |
| `measure`, `status`, `sync`, tests | local | **$0** |

Stage A kills ~93% of postings before any model sees them. That is the whole
reason this is affordable.

---

## Requirements

- **Python 3.12+**
- **Chromium**, for rendering resumes to PDF — installed via Playwright below
- **A Google Cloud project** with a service account, for Sheets and (optionally)
  Vertex AI
- **A Gemini API key** *or* Vertex AI access — see [Choosing a model backend](#choosing-a-model-backend)
- **A Google Sheet** you own

An Anthropic key is optional; the provider layer supports Claude as an
alternative to Gemini, but the rubric is calibrated on Gemini.

---

## Setup

### 1. Install

```bash
git clone <your-fork> jobpipe && cd jobpipe
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m playwright install chromium
```

### 2. Copy the three config templates

```bash
cp config/master_profile.example.md config/master_profile.md
cp config/bullets.example.yaml      config/bullets.yaml
cp config/targets.example.csv       config/targets.csv
cp .env.example                     .env
```

All four are gitignored. They are yours and they never get committed.

### 3. Write your master profile — this is the important one

`config/master_profile.md` is the source of truth for every claim any
generated document makes, and it is the system-instruction prefix on every
scoring call. **Its quality determines everything downstream.**

Write it in the third person, and be specific. "Strong Python skills" tells the
scorer nothing. "Built and operated an ingestion pipeline three analysts
depended on daily" tells it a great deal. A thin profile produces scores
clustered at 6–8, which carry no ranking information at all.

The template walks through all twelve sections. The ones that do the most work
are §10 (dealbreakers — what disqualifies you, what is merely a negative) and
§11 (calibration examples, which stop scores clustering).

### 4. Fill the bullet bank

`config/bullets.yaml` is the **fabrication firewall**. Resume tailoring is
selection and ordering, never writing: the model picks bullet ids from this
file and may choose between pre-written short variants. It cannot author bullet
text, so the resume literally cannot invent experience.

Every bullet needs a `source` — where the claim can be checked. The loader
refuses a bank without it, which is what makes "every claim traces to a
document" a mechanism rather than an intention.

Start from your existing resume. Fifteen to twenty-five bullets is a good size.

### 5. Choose your targets

`config/targets.csv` ships as an example with **143 companies and verified
board tokens** — finding that Anthropic's Greenhouse token is `anthropic` is
the tedious part, and it is done for you.

But the list reflects one person's lanes (AI-native companies) and the `tier`
column encodes their priorities. Edit it. Tier 1 is "drop everything", tier 3
is "if the week is quiet"; tier drives queue order everywhere.

```bash
python -m jobpipe.cli targets    # validates the file
python -m jobpipe.cli verify     # probes every board: does the token work?
```

`verify` is worth running after any edit. A wrong token does not raise — most
boards return an empty list — so without it a company silently never appears.

### 6. Google Cloud: service account

1. Create or pick a project at <https://console.cloud.google.com>
2. Enable the **Google Sheets API** and **Google Drive API**
3. **IAM & Admin → Service Accounts → Create**, then **Keys → Add key → JSON**
4. Save it as `config/service_account.json` (gitignored)

If you are using Vertex for scoring, also enable `aiplatform.googleapis.com`
and grant the service account `roles/aiplatform.user`. Filter the IAM role
picker on the id `aiplatform` rather than the display name — Google renamed
these and the names now collide with an unrelated product.

### 7. Create the sheet — and share it

Make a new Google Sheet. Take its id from the URL:

```
docs.google.com/spreadsheets/d/<THIS PART>/edit
```

> **Then share the sheet with your service account's email address, as Editor.**
>
> It looks like `something@your-project.iam.gserviceaccount.com` and is inside
> the JSON key you just downloaded. **This is the step everyone misses.**
> Sheets permissions and Cloud IAM are separate systems; granting IAM roles
> does not give the service account access to your document.

### 8. Fill in `.env`

```ini
JOBPIPE_PROVIDER=gemini
GEMINI_API_KEY=...
GOOGLE_SERVICE_ACCOUNT_JSON=config/service_account.json
GOOGLE_SHEET_ID=<the id from the URL>
GOOGLE_SHEET_TAB=roles
```

### 9. First run

```bash
python -m jobpipe.cli init      # create the database
python -m jobpipe.cli fetch     # pull every board            (free)
python -m jobpipe.cli filter    # Stage A                      (free)
python -m jobpipe.cli stats     # how much survived
python -m jobpipe.cli score --yes   # Stage B — THIS SPENDS MONEY
python -m jobpipe.cli sync      # push the shortlist to the sheet
```

Nothing is scored until you pass `--yes`. That is deliberate: scheduling the
job and authorising spend are separate decisions.

---

## Choosing a model backend

Gemini runs two ways, and it is one environment variable:

| | Developer API | Vertex AI |
|---|---|---|
| Auth | `GEMINI_API_KEY` | the service account you already made |
| Billed to | that key's account | your GCP billing account |
| Google Cloud credits | **cannot** be spent | can |
| Data terms | paid tier does not train on prompts; free tier does | enterprise terms |

To use Vertex:

```ini
JOBPIPE_GEMINI_BACKEND=vertex
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=global
```

Unset it to go back. Flipping the backend does not change which model runs, so
you can verify the backend on its own before changing models underneath it.

**If you are on a free-tier API key, move to a paid one or to Vertex.** The
prompt is your complete career profile, and the free tier's terms permit
training on submitted prompts.

---

## Daily use

```bash
python -m jobpipe.cli daily --yes     # fetch, filter, score, sync
python -m jobpipe.cli status          # where applications stand
```

To run it unattended (Windows):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\register-task.ps1
```

Run that from an **elevated** shell — the task registers with an S4U principal
so it runs whether or not you are logged in, and granting that needs admin.
Add `-Spend` to include scoring; without it the scheduled run fetches, filters
and syncs for free.

### Applying to something

```bash
python -m jobpipe.cli tailor --queued --yes            # a resume per queued role
python -m jobpipe.cli tailor --cover-letter <id> --yes # one letter, grounded
```

Then **copy the letter before editing it**:

```bash
cp cover_letter.md cover_letter.sent.md    # then edit the .sent.md
```

The original is the baseline `measure` compares against, and prose regenerates
at ~0.53 distance from itself, so a fresh draft is not a substitute for a
destroyed original. If you run the MCP server, `copy_for_editing` does this
correctly and refuses to do it wrong.

```bash
python -m jobpipe.cli measure --detail   # what you changed, per paragraph slot
```

The per-slot number is the actionable part: it tells you which instruction in
the prompt is wrong.

---

## The guardrails

These are not suggestions. Several were written after something went wrong.

- **It never submits an application.** It fills a form if you ask it to, then
  stops. You click submit. Most ATS terms require a human, and the
  twenty-second review is what stops a mis-tailored application going out.
- **It never sends email.** Drafts only.
- **It never fabricates.** Every claim traces to `bullets.yaml` or
  `master_profile.md`. If a posting wants something you have not done, the
  honest output is a document without it. If a claim is true but the profile
  does not support it, **fix the profile first.**
- **Nothing is ever deleted** from the database. Status is an append-only event
  log, because "advance rate" is a question about transitions.
- **Copy letters, never rename them.** A baseline was destroyed this way once
  and it is unrecoverable.

---

## How it works

```
targets.csv → Greenhouse / Ashby / SmartRecruiters public JSON
  → normalize → dedupe (source id, then content hash for reposts)
  → Stage A: deterministic regex filters        ~93% killed, $0
  → Stage B: LLM scoring against your profile
  → SQLite (source of truth) → Google Sheet (a rendered view)
  → queued → resume PDF → [you decide] → cover letter → grounded
```

Four decisions worth knowing about:

**Two score axes.** `overall` is fit on paper; `apply_score` is "worth applying
anyway". They come apart on the shape that dominates an early-career search —
right skills, not enough years — and one number cannot carry both.

**Rank, not threshold.** Scores pile onto 6, so a fixed cutoff swings the sheet
between three rows and twelve. The sheet takes the top 25, max 3 per company.

**Status is an event log, not a column.** Overwriting `interview` with
`rejected` destroys the evidence the interview happened.

**Content is stored, not pointed at.** Generated documents live in the database,
not only on disk, because files get renamed and overwritten.

`PROJECT.md` has the guardrails and why the phases are shaped as they are.
`CONTEXT.md` has where things actually stand. `PHASE5_PLAN.md` covers the MCP
server.

---

## Optional: the MCP server

Exposes the pipeline to Claude as tools, so reviewing a resume against the real
posting happens in a conversation. `.mcp.json` registers it for local desktop
sessions.

It is read-only plus two safe writes. There is deliberately **no submit tool
and no email tool** — a tool that does not exist cannot be argued into
existing, which is stronger than a rule a model is asked to respect. Scoring
and calibration are kept off the surface too: they spend money and write
corpus-wide.

---

## What is yours, and never committed

```
.env                          keys and ids
config/service_account.json   your Google credentials
config/master_profile.md      your career history
config/bullets.yaml           your resume
config/targets.csv            your target companies
config/calibration*           your hand scores and judgements
samples/                      your real cover letters
output/                       generated applications
jobpipe.db                    everything the pipeline knows
```

Check `git status` before your first push. If any of those appear, stop.

---

## Troubleshooting

**`sync` fails with a permission error.** The sheet is not shared with the
service account. See step 7 — it is almost always this.

**A company never appears.** Run `cli verify`. A wrong board token returns an
empty list rather than an error.

**Scores all come back 6–8.** The profile is too thin or the calibration
examples in §11 are still the drafted ones. Hand-score twenty real postings and
rewrite them.

**`tailor` fails on the bank.** The loader validates strictly — unknown
archetypes, missing `source`, duplicate ids. The message names the bullet.

**Resume rendering fails.** `python -m playwright install chromium`.

**Everything is slow the first time.** Fetching 143 boards takes a few minutes.
