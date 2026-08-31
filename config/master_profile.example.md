# Master Profile — EXAMPLE

Copy to `master_profile.md` and replace everything:

    cp config/master_profile.example.md config/master_profile.md

**This file is the single source of truth for every claim any generated
document makes.** It is sent as the system-instruction prefix on every Stage B
scoring call, so two things follow: it should be substantial (a short profile
produces clustered, uninformative scores), and it should contain nothing you
would not want a model provider to hold. It is gitignored for that reason.

Write it in the third person. The scorer is judging a candidate, not talking
to you.

**Contact:** jane.example@example.com · (555) 010-0000 · linkedin.com/in/example
**Location:** Springfield, IL (open to relocation)

**Level:** B.S. May 2025. Roughly one year of internship experience. Target
band is new-grad through early-career.

---

## 1. Positioning

Two or three paragraphs on what this person is, what they are aiming at, and
what makes them an unusual fit. Be concrete. "Strong Python skills" tells a
scorer nothing; "built and operated an ingestion pipeline that three analysts
depended on daily" tells it a great deal.

## 2-8. Evidence sections

One section per capability area — machine learning, software engineering,
data engineering, analytics, whatever the person actually has. Each should
describe real work with specifics: what was built, what it ran on, what it
achieved, what went wrong.

These sections are what the scorer reasons over. Vague sections produce vague
scores clustered at 6-8, which carry no ranking information at all.

## 9. What they actually want

Lanes, seniority band, kinds of problem. The scorer uses this to tell a good
match from a merely adjacent one.

## 10. Dealbreakers and constraints

Feeds the Stage B `dealbreakers` field directly.

**Hard no — disqualify regardless of fit:**
- Roles requiring work authorization the candidate does not have
- Security clearance required
- Experience bars far above the candidate
- Senior, Staff, Principal, Lead, Manager, Director or VP titles

**Soft negative — flag, don't disqualify:**
- A stretch experience bar
- Work outside the lanes above **with none of the relevant component at all**
  — write the qualifier so it cannot be skimmed off. A rule that reads
  "frontend-heavy or general SWE with no ML" WILL be applied as
  "frontend-heavy"; that mistake cost two real roles here before it was found.

**Actively positive:**
- The shapes worth stretching for. Be as specific as the negatives.

## 11. Calibration examples

Anchored score bands, so scores do not cluster. Give a worked example at 9, 7,
5, 3 and 1 explaining WHY each lands there. Revise these after hand-scoring 20
real postings — the drafted versions are guesses until then.

## 12. Structured identity

Everything below already appears in the prose above. What is new is the shape:
a generator cannot reliably parse `**Acme — Data Engineering Intern** · Jun
2024` without guessing where the company ends and the title begins.

This block is the source of truth for the resume header. `bullets.yaml`'s
`meta:` and `education:` are generated from it, so the two cannot drift.

Placed at the END of the file on purpose: implicit prompt caching keys on a
shared prefix, so appending leaves everything above it byte-identical.

```yaml
identity:
  name: Jane Q. Example
  email: jane.example@example.com
  phone: "(555) 010-0000"
  linkedin: linkedin.com/in/example
  location: Springfield, IL

education:
  school: Example State University
  degree: B.S. Computer Science · Minor in Statistics
  date: May 2025
  coursework: >-
    Machine Learning, Databases, Algorithms, Regression Methods,
    Data Structures

experience:
  - id: acme
    company: Acme Analytics
    title: Data Engineering Intern
    dates: Jun 2024 - Aug 2024
```
