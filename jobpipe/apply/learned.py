"""Remembered answers: what Anish chose by hand, reused on the same question.

Anish, 2026-11-04: "store the answers for one Greenhouse and one Ashby
application, so the same question can be auto-answered on other postings."

The store is `config/learned_answers.yaml`, gitignored, written by the
runner and hand-editable. Each entry is the question as first asked, the
answer as the exact option text, the widget kind, and where it was seen.

What makes this safe enough to reuse:

* **The key is the question**, whitespace collapsed and case-folded, with
  this posting's company replaced by `{company}` - so "How did you first
  learn about Affirm as an employer?" serves the same question at another
  company. Nothing fuzzier: `answers.normalise_question`'s docstring says
  why deciding two wordings "mean the same thing" is how one answer
  silently replaces another.
* **Choice widgets only** - select, radio, yes/no. Never free text (prose
  goes through save_short_answer and check_grounding), never
  self-identification or an EEOC/demographic section, never the
  never-fill rows, never a fact whose source of truth is the profile.
* **Reuse only where the resolver would otherwise leave a blocker or a
  blank**, and only as an exact option on the new form.
* **Recorded only after his submit**: the runner reads the choices it did
  not make while the window is open and writes them when the form
  disappears. An abandoned run teaches nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from . import employers as _employers, spec as _spec

DEFAULT_PATH = Path("config/learned_answers.yaml")
EXAMPLE_PATH = Path("config/learned_answers.example.yaml")
PLACEHOLDER = "{company}"

LEARNABLE_KINDS = (_spec.SELECT, _spec.RADIO, _spec.YESNO)

# Meanings whose source of truth is the profile, the bank or the tools. A
# remembered answer must never stand in for one of these.
PROFILE_MEANINGS = frozenset({
    "work_authorization", "sponsorship", "prior_employment", "resume",
    "cover_letter", "additional_info", "first_name", "last_name", "full_name",
    "email", "phone", "linkedin", "github", "website", "location", "school",
    "degree", "graduation", "preferred_name",
})


class LearnedError(ValueError):
    """learned_answers.yaml says something the runner must not guess at."""


@dataclass
class Seen:
    company: str
    posting_id: int | None
    date: str

    def label(self) -> str:
        return f"{self.company} #{self.posting_id} {self.date}"


@dataclass
class Entry:
    question: str
    answer: str
    kind: str
    seen_on: list[Seen] = field(default_factory=list)
    superseded: list[str] = field(default_factory=list)   # earlier answers, kept for the reader

    @property
    def latest(self) -> Seen | None:
        return self.seen_on[-1] if self.seen_on else None


@dataclass
class Store:
    entries: dict[str, Entry] = field(default_factory=dict)
    path: Path | None = None

    def get(self, question: str, company: str | None) -> Entry | None:
        return self.entries.get(key_for(question, company))


def key_for(question: str, company: str | None) -> str:
    """The question, whitespace collapsed and case-folded, with every way
    of naming this company replaced by the placeholder."""
    text = " ".join(str(question or "").split()).casefold()
    for name in sorted(_employers.company_names(company), key=len, reverse=True):
        norm = " ".join(name.casefold().split())
        if not norm:
            continue
        text = re.sub(r"(?<![a-z0-9])" + re.escape(norm) + r"(?![a-z0-9])",
                      PLACEHOLDER, text)
    return text


def template_answer(answer: str, company: str | None) -> str:
    """The option text with this company's name replaced by the placeholder,
    case preserved otherwise - Sierra's "Yes, I am based in one of Sierra's
    office locations." is the same answer at another company."""
    text = " ".join(str(answer or "").split())
    for name in sorted(_employers.company_names(company), key=len, reverse=True):
        norm = " ".join(name.split())
        if norm:
            text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(norm) + r"(?![A-Za-z0-9])",
                          PLACEHOLDER, text, flags=re.I)
    return text


def candidate_answers(answer: str, company: str | None) -> tuple[str, ...]:
    """The stored answer expanded for this company: the name before any
    parenthesis first, then the whole string, then each parenthesised part;
    the raw text last. The caller takes the first that is exactly an
    option on the form."""
    if PLACEHOLDER not in answer:
        return (answer,)
    names = list(_employers.company_names(company))
    if len(names) > 1:
        names = [names[1]] + [names[0]] + names[2:]
    out = [answer.replace(PLACEHOLDER, n) for n in names] + [answer]
    return tuple(dict.fromkeys(out))


def learnable(f: _spec.Field, meaning: str | None) -> bool:
    """May this field's answer be remembered, or filled from memory?"""
    from . import resolve as _resolve   # late: resolve imports this module
    if f.kind not in LEARNABLE_KINDS:
        return False
    if f.section != _spec.APPLICATION:
        return False
    if meaning in _resolve.NEVER_FILL or meaning in _resolve.SELF_ID:
        return False
    if meaning in PROFILE_MEANINGS:
        return False
    return True


# ---------------------------------------------------------------------------
# File
# ---------------------------------------------------------------------------

def load(path: str | Path | None = None) -> Store:
    path = Path(path) if path else DEFAULT_PATH
    store = Store(path=path)
    if not path.exists():
        return store
    import yaml
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict) or not isinstance(raw.get("answers", {}), dict):
        raise LearnedError(f"{path} must be a mapping with an `answers` mapping")
    for key, item in (raw.get("answers") or {}).items():
        if not isinstance(item, dict):
            raise LearnedError(f"{path}: {key!r} must be a mapping")
        answer = item.get("answer")
        if not isinstance(answer, str) or not answer.strip():
            raise LearnedError(f"{path}: {key!r} has no answer text (quote it)")
        kind = str(item.get("kind") or _spec.SELECT)
        if kind not in LEARNABLE_KINDS:
            raise LearnedError(f"{path}: {key!r} has kind {kind!r}; only "
                               f"{', '.join(LEARNABLE_KINDS)} may be remembered")
        seen = [Seen(str(s.get("company", "")), s.get("posting_id"), str(s.get("date", "")))
                for s in (item.get("seen_on") or []) if isinstance(s, dict)]
        store.entries[str(key)] = Entry(
            question=str(item.get("question") or key), answer=answer.strip(),
            kind=kind, seen_on=seen,
            superseded=[str(x) for x in (item.get("superseded") or [])])
    return store


def save(store: Store, path: str | Path | None = None) -> Path:
    import yaml
    path = Path(path) if path else (store.path or DEFAULT_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"answers": {
        key: {"question": e.question, "answer": e.answer, "kind": e.kind,
              "seen_on": [{"company": s.company, "posting_id": s.posting_id, "date": s.date}
                          for s in e.seen_on],
              **({"superseded": list(e.superseded)} if e.superseded else {})}
        for key, e in sorted(store.entries.items())}}
    header = ("# Answers Anish chose by hand on review-mode forms, remembered so the same\n"
              "# question is answered the same way on another posting. Written by the\n"
              "# runner after his submit; hand-editable. See learned_answers.example.yaml.\n")
    path.write_text(header + yaml.safe_dump(data, sort_keys=False, allow_unicode=True,
                                            width=100), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------

def record(store: Store, question: str, answer: str, kind: str, company: str,
           posting_id: int | None, when: str | None = None) -> Entry:
    """Remember one choice. The latest answer wins; an earlier different
    answer is kept under `superseded` so the file stays readable."""
    key = key_for(question, company)
    when = when or date.today().isoformat()
    seen = Seen(company=company or "", posting_id=posting_id, date=when)
    entry = store.entries.get(key)
    answer = template_answer(answer, company)
    if entry is None:
        entry = Entry(question=" ".join(question.split()), answer=answer, kind=kind)
        store.entries[key] = entry
    elif entry.answer != answer:
        entry.superseded.append(entry.answer)
        entry.answer = answer
    entry.kind = kind                 # the widget it was last seen in
    entry.seen_on.append(seen)
    return entry


def record_choices(store: Store, form: _spec.FormSpec, resolution, choices: dict[str, str | None],
                   company: str, posting_id: int | None, when: str | None = None) -> list[str]:
    """Record what Anish chose on the fields the runner did not fill.
    `choices` is fill.read_choices' snapshot: key -> selected text or None.
    Returns the keys recorded."""
    from . import resolve as _resolve
    filled = {r.field.key for r in resolution.items
              if r.action in (_resolve.SELECT, _resolve.FILL, _resolve.UPLOAD, _resolve.AUTOCOMPLETE)}
    # The resolver's reading of each field, not the label pattern alone: a
    # non-compete question mentions a "former employer" without being a
    # have-you-worked-here question (Chime, 2026-11-05).
    meanings = {r.field.key: r.meaning for r in resolution.items}
    recorded = []
    for f in form.fields:
        chosen = choices.get(f.key)
        if not chosen or f.key in filled:
            continue
        if not learnable(f, meanings.get(f.key) or _resolve.meaning_of(f)):
            continue
        if f.options is not None and not any(o.label.strip() == chosen.strip() for o in f.options):
            continue   # not one of the form's own options; not a choice we can name
        record(store, f.label, chosen, f.kind, company, posting_id, when)
        recorded.append(f.key)
    return recorded
