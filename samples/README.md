# Phase 2 inputs

Drop your cover letters and resume here. Everything in this directory except
this file is gitignored — it contains your real name, employment history, and
text written to named companies.

```
samples/
  README.md              <- the only tracked file
  cover_letters/         <- one file per letter, any format
  resume/                <- base resume, plus any variants you already keep
  outcomes.csv           <- optional, and the single most useful thing here
```

## Cover letters

**Variety beats volume.** Six letters spanning different role types teach the
generator more than twenty near-identical ones, because what it has to learn
is where your writing *changes* and where it stays fixed. Useful spread:

- one AI/ML engineering role
- one data science role
- one general software engineering role
- one you wrote for a company you badly wanted
- one you wrote quickly for a role you were lukewarm on
- one for a role where your experience was clearly light

That last pair matters most. The gap between your careful writing and your
rushed writing is exactly the gap the generator has to close.

Keep them as sent, including anything you consider weak. A corpus of only your
best work teaches a target, not a range, and gives no signal about what you
were compensating for.

## Resume

Two things:

1. **The source file, not just a PDF.** DOCX, LaTeX, Google Doc export,
   Markdown — anything the layout can be regenerated from. A PDF alone means
   Phase 2 can propose wording but cannot produce a document you can send.
2. **Any variants you already maintain.** If you keep a separate version for
   ML roles and another for data roles, both are worth having: you have
   already made tailoring decisions by hand, and those decisions are the
   specification.

## outcomes.csv — optional, most valuable

If you know which applications got a response, say so:

```csv
file,company,role,outcome,notes
letter_cohere.md,Cohere,Applied AI Engineer,no reply,
letter_notion.md,Notion,Early Career,recruiter screen,replied in 3 days
```

`outcome` is free text — "no reply", "rejected", "recruiter screen",
"interview", "unknown". Unknown is fine and honest.

Why this is worth more than more letters: without it, the generator can only
learn to imitate your writing. With it, there is at least a weak signal about
which parts of your writing did anything. The sample is far too small to prove
causation and will not be treated as though it does — but "these three got
replies and these five did not" is still the only outcome evidence in the
project, and it costs you five minutes.

## What happens to these

They become few-shot examples and a style reference for Phase 2 drafting.

The guardrail from PROJECT.md holds without exception: **no fabricated
experience**. Tailoring means selecting and reframing real material from the
master profile, never inventing it. If a generated letter ever claims
something you did not do, that is a bug of the most serious kind, and these
samples are partly here so it can be caught by comparison.
