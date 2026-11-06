"""Every field gets a value and its source, or a blocker. The safety core.

PHASE6_PLAN §3's table, as code. The sources are the ones the tools already
enforce: `form_answers` (profile §12 plus the gitignored answers file),
`render_resume`'s PDF for this posting, `prepare_letter`'s reviewed copy, and
a `documents` row for (posting, question) with a passing grounding verdict.
Nothing here writes prose, guesses an option, or reads a page.

Pure: a FormSpec and a Sources in, a Resolution out. The runner builds the
Sources from the tool bodies (slice 7); the tests build them by hand.

Two readings of the table are recorded here rather than left implicit:

* "blocker" means "cannot be auto-submitted, and the report says why". A
  REQUIRED field with no source is a blocker. An OPTIONAL field with no
  source is left blank and listed in the report - an optional "anything
  else?" box left empty is not a reason to stop, and filling it from
  nowhere would be. The never-fill rows (salary, government id, payment,
  attestation, signature) follow the same split, as the table itself says.
* A first/last name split is taken from §12 `first_name` / `last_name`
  when those keys exist, and from `name` only when it has exactly two
  words. A three-word name is a blocker with the fix named, because
  guessing which word is the surname misrepresents him on the form.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import answers as _answers
from . import employers as _employers, spec as _spec

# Actions the filler performs. `blank` and `skip` touch nothing.
FILL, UPLOAD, SELECT, AUTOCOMPLETE, BLANK, SKIP, BLOCKER = (
    "fill", "upload", "select", "autocomplete", "blank", "skip", "blocker")


@dataclass(frozen=True)
class Sources:
    """What the resolver may draw on. All optional: a missing source is a
    blank or a blocker, never a guess."""
    answers: dict[str, Any] | None = None          # form_answers() view
    resume_path: Path | None = None                 # render_resume, this posting
    letter_text: str | None = None                  # prepare_letter, reviewed copy
    letter_pdf: Path | None = None                  # prepare_letter(pdf=True)
    short_answers: dict[str, dict] = field(default_factory=dict)  # normalised question -> row
    company: str | None = None                      # this posting's employer
    employers: tuple[str, ...] | None = None        # his past employers; None = unknown
    learned: Any = None                             # learned.Store, or None

    def answer(self, question: str) -> dict | None:
        return self.short_answers.get(_answers.normalise_question(question))


@dataclass(frozen=True)
class Resolved:
    field: _spec.Field
    action: str
    value: str | None = None       # text, option label, or file path
    source: str | None = None      # where the value came from, for the report
    meaning: str | None = None     # what the label was read as
    note: str = ""

    @property
    def is_blocker(self) -> bool:
        return self.action == BLOCKER


@dataclass(frozen=True)
class Resolution:
    items: tuple[Resolved, ...]

    @property
    def blockers(self) -> tuple[Resolved, ...]:
        return tuple(r for r in self.items if r.action == BLOCKER)

    @property
    def fills(self) -> tuple[Resolved, ...]:
        return tuple(r for r in self.items
                     if r.action in (FILL, UPLOAD, SELECT, AUTOCOMPLETE))

    @property
    def blanks(self) -> tuple[Resolved, ...]:
        return tuple(r for r in self.items if r.action == BLANK)

    def by_key(self, key: str) -> Resolved | None:
        return next((r for r in self.items if r.field.key == key), None)


# ---------------------------------------------------------------------------
# What a label means. Order matters: "require sponsorship" must win over
# "authorized to work", and "cover letter" over "letter".
# ---------------------------------------------------------------------------

_MEANINGS: tuple[tuple[str, str], ...] = (
    ("cover_letter", r"cover\s*letter"),
    ("resume", r"\bresume\b|\bcv\b|curriculum"),
    # "Anything else?" boxes. A reviewed letter goes here when there is one
    # (Anish's decision, 2026-11-04); otherwise they are open-ended.
    ("additional_info", r"anything else|additional information|additional (comments|details)|"
                        r"anything (you'?d|you would) like (us|to)|is there anything|"
                        r"other information|any other (information|details|comments)"),
    ("signature", r"signature|\binitials?\b|\be-?sign\b"),
    ("attestation", r"\bi\s+(certify|acknowledge|understand|agree|confirm|attest|declare|consent)\b|certif(y|ication) that|acknowledg(e|ement) (that|of)"),
    ("government_id", r"\bssn\b|social security|passport|national id|government[- ]issued|date of birth|\bdob\b|driver'?s? licen"),
    ("payment", r"\bbank\b|payment|routing number|\biban\b"),
    # Answered only when the label also names this posting's employer; see
    # resolve_field. "Have you worked at a startup before?" is not this.
    ("prior_employment", r"(previously|ever|before|formerly|in the past)\b.*\b(worked|employed|an employee|intern(ed)?)|"
                         r"\b(worked|employed|intern(ed)?)\b.*\b(previously|before|in the past)|"
                         r"\bformer\b.*\b(employee|employer|employment)|prior employment|"
                         r"(are|were) you (a |an )?(current|former|previous) (employee|contractor)"),
    ("salary", r"salary|compensation|\bpay\b|desired rate|hourly rate|expected (rate|earnings)"),
    ("sponsorship", r"sponsor"),
    ("work_authorization", r"authori[sz]ed to work|legally (authori[sz]ed|eligible|permitted)|work authori[sz]ation|eligible to work|right to work"),
    ("start_date", r"start date|available to start|when (can|could) you start|notice period|earliest (start|availability)|availability to start"),
    ("how_heard", r"how did you (first )?(hear|find|learn)|how you heard|where did you (hear|find)|\breferr(al|ed)\b"),
    ("preferred_name", r"preferred (first )?name|what should we call you"),
    ("first_name", r"^(legal\s+)?first\s*name|given name"),
    ("last_name", r"^(legal\s+)?last\s*name|family name|surname"),
    ("full_name", r"^(full |legal )?name$|^your name$"),
    ("email", r"\be-?mail\b"),
    ("phone", r"\bphone\b|\bmobile\b|\btelephone\b"),
    ("linkedin", r"linkedin"),
    ("github", r"github"),
    ("website", r"website|portfolio|personal site|\burl\b"),
    ("pronouns", r"pronoun"),
    ("hispanic_or_latino", r"hispanic|latino|latinx"),
    ("race_ethnicity", r"\brace\b|ethnicit"),
    ("veteran_status", r"veteran"),
    ("disability_status", r"disabilit"),
    ("gender", r"\bgender\b|\bsex\b"),
    ("sexual_orientation", r"sexual orientation|\blgbt"),
    ("transgender", r"transgender"),
    ("graduation", r"graduat"),
    ("degree", r"\bdegree\b|highest (level of )?education"),
    ("school", r"\bschool\b|universit|college|institution"),
    # Before location: "City, State" stays location; this is the state alone.
    ("state_of_residence", r"\bstate\b.*\b(reside|residence|live|province)\b|"
                           r"\b(reside|residence|live)\b.*\bstate\b|^state( of residence)?$"),
    ("location", r"^location|current (location|city)|where (are you|do you) (located|based|live)|city\b.*state|^city$"),
)
_FUTURE = re.compile(r"future|at any (point|time)|\blater\b|going forward|\bever\b|"
                     r"\bwill you\b.*\b(need|require)", re.I)
_MEANING_RE = tuple((m, re.compile(p, re.I)) for m, p in _MEANINGS)

# Keys the ATSes use for the same things. Checked before the label.
_KEY_MEANINGS = {
    "first_name": "first_name", "last_name": "last_name", "email": "email",
    "phone": "phone", "preferred_name": "preferred_name",
    "candidate-location": "location", "resume": "resume",
    "cover_letter": "cover_letter",
    # Greenhouse EEOC
    "gender": "gender", "race": "race_ethnicity",
    "hispanic_ethnicity": "hispanic_or_latino",
    "veteran_status": "veteran_status", "disability_status": "disability_status",
    # Ashby system fields
    "_systemfield_name": "full_name", "_systemfield_email": "email",
    "_systemfield_phone": "phone", "_systemfield_location": "location",
    "_systemfield_resume": "resume", "_systemfield_linkedin": "linkedin",
    "_systemfield_website": "website",
    # Greenhouse fields read off the rendered page (spec.GH_PAGE_FIELDS)
    "country": "phone_country",
    "school--0": "edu_school", "degree--0": "edu_degree",
    "discipline--0": "edu_discipline",
    "start-month--0": "edu_start_month", "start-year--0": "edu_start_year",
    "end-month--0": "edu_end_month", "end-year--0": "edu_end_year",
}

# Greenhouse's Education section takes its own option wording ("Bachelor's
# Degree", the school's name as Greenhouse lists it), which is not how §12
# states the same facts. The form spellings live in answers.yaml's
# greenhouse_education, as the Greenhouse spellings of self-ID answers do.
_GH_EDUCATION = {
    "edu_school": "school", "edu_degree": "degree", "edu_discipline": "discipline",
    "edu_start_month": "start_month", "edu_start_year": "start_year",
    "edu_end_month": "end_month", "edu_end_year": "end_year",
}

NEVER_FILL = ("salary", "government_id", "payment", "attestation", "signature")
SELF_ID = ("gender", "hispanic_or_latino", "race_ethnicity", "veteran_status",
           "disability_status", "pronouns", "sexual_orientation", "transgender")
LOGISTICS = {"start_date": "earliest_start_date", "how_heard": "how_did_you_hear",
             "preferred_name": "preferred_name"}


def meaning_of(f: _spec.Field) -> str | None:
    if f.key in _KEY_MEANINGS:
        return _KEY_MEANINGS[f.key]
    label = " ".join(f.label.split())
    for meaning, pattern in _MEANING_RE:
        if pattern.search(label):
            return meaning
    return None


# ---------------------------------------------------------------------------
# Resolving one field
# ---------------------------------------------------------------------------

def _get(mapping: Any, *path: str) -> Any:
    cur = mapping
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _exact_option(f: _spec.Field, wanted: str) -> _spec.Option | None:
    """The option whose text is exactly `wanted`. Never a fuzzy match."""
    for o in f.options or ():
        if o.label.strip() == wanted.strip():
            return o
    return None


def _no_source(f: _spec.Field, meaning: str | None, why: str) -> Resolved:
    if f.required:
        return Resolved(f, BLOCKER, meaning=meaning, note=why)
    return Resolved(f, BLANK, meaning=meaning, note=f"optional; {why}")


def _put_text(f: _spec.Field, meaning: str, value: str | None, source: str,
              missing: str) -> Resolved:
    if value is None:
        return _no_source(f, meaning, missing)
    if f.kind == _spec.SELECT and f.options is None:
        # Ashby's dropdowns and autocompletes carry no options until opened.
        # The filler matches exact text in the live listbox; no match leaves
        # it unset, which fails decide.py's `verified` condition.
        return Resolved(f, SELECT, value, source, meaning,
                        note="options read from the live widget; exact text only, "
                             "otherwise left unset and reported")
    if f.kind in (_spec.SELECT, _spec.YESNO, _spec.MULTISELECT, _spec.RADIO):
        opt = _exact_option(f, value)
        if opt is None:
            return _no_source(f, meaning,
                              f"no option exactly equals {value!r}; options: "
                              f"{[o.label for o in f.options or ()]}")
        return Resolved(f, SELECT, opt.label, source, meaning)
    if f.kind in (_spec.LOCATION,):
        return Resolved(f, AUTOCOMPLETE, value, source, meaning,
                        note="typed, then the suggestion exactly equal to it; "
                             "otherwise left and reported")
    if f.kind in (_spec.TEXT, _spec.EMAIL, _spec.PHONE, _spec.TEXTAREA):
        return Resolved(f, FILL, value, source, meaning)
    return _no_source(f, meaning, f"a {f.kind} widget cannot take text")


def _yes_no(f: _spec.Field, meaning: str, flag: Any, source: str) -> Resolved:
    if not isinstance(flag, bool):
        return _no_source(f, meaning, f"{source} is not set to true/false")
    wanted = "Yes" if flag else "No"
    if f.kind in (_spec.SELECT, _spec.YESNO, _spec.RADIO):
        opt = _exact_option(f, wanted)
        if opt is None:
            return _no_source(f, meaning,
                              f"no option exactly equals {wanted!r}; options: "
                              f"{[o.label for o in f.options or ()]}")
        return Resolved(f, SELECT, opt.label, source, meaning)
    return _no_source(f, meaning, f"a {f.kind} widget cannot take yes/no")


def _split_name(identity: dict | None) -> tuple[str | None, str | None, str]:
    """(first, last, note). §12 keys first; a two-word name second; a
    longer name is not split."""
    identity = identity or {}
    first, last = _text(identity.get("first_name")), _text(identity.get("last_name"))
    if first and last:
        return first, last, "profile:identity.first_name/last_name"
    name = _text(identity.get("name"))
    if name:
        parts = name.split()
        if len(parts) == 2:
            return parts[0], parts[1], "profile:identity.name (two words)"
        return None, None, (f"identity.name {name!r} has {len(parts)} words; add "
                            f"first_name and last_name to master_profile.md §12")
    return None, None, "identity.name is not in master_profile.md §12"


def _self_id(f: _spec.Field, meaning: str, answers: dict | None) -> Resolved:
    entries = _get(answers, "self_identification", meaning) or []
    if isinstance(entries, str):
        entries = [entries]
    source = f"answers:self_identification.{meaning}"
    if f.kind not in (_spec.SELECT, _spec.YESNO, _spec.MULTISELECT, _spec.RADIO):
        # A free-text self-ID box (pronouns, usually).
        value = _text(entries[0]) if entries else None
        if value is None:
            return _no_source(f, meaning, "no stored entry; left blank, never interpreted")
        return Resolved(f, FILL, value, source, meaning)
    for entry in entries:
        opt = _exact_option(f, str(entry))
        if opt is not None:
            return Resolved(f, SELECT, opt.label, source, meaning)
    why = ("no stored entry exactly matches an option; left blank, never "
           f"interpreted. Options: {[o.label for o in f.options or ()]}")
    return _no_source(f, meaning, why)


def _open_ended(f: _spec.Field, sources: Sources, meaning: str | None) -> Resolved:
    row = sources.answer(f.label)
    if row is None:
        return _no_source(f, meaning or "open_ended",
                          "no stored answer for this question; draft it in a "
                          "session with save_short_answer, then check_grounding")
    if row.get("grounding_ok") != 1:
        return Resolved(f, BLOCKER, meaning=meaning or "open_ended",
                        note=f"stored answer (document {row.get('id')}) has no "
                             f"passing grounding verdict; check_grounding first")
    content = _text(row.get("content"))
    if content is None:
        return Resolved(f, BLOCKER, meaning=meaning or "open_ended",
                        note="stored answer is empty")
    if f.kind == _spec.TEXT and "\n" in content:
        return Resolved(f, BLOCKER, meaning=meaning or "open_ended",
                        note="single-line box, multi-paragraph answer: re-save "
                             "it as one paragraph and re-check it")
    if f.kind not in (_spec.TEXT, _spec.TEXTAREA):
        return _no_source(f, meaning or "open_ended",
                          f"a {f.kind} widget cannot take a written answer")
    return Resolved(f, FILL, content, f"documents:short_answer#{row.get('id')}",
                    meaning or "open_ended", note=f"grounding passed ({row.get('grounding_model') or 'model unrecorded'})")


def resolve_field(f: _spec.Field, sources: Sources) -> Resolved:
    answers = sources.answers or {}
    identity = answers.get("identity") or {}
    education = answers.get("education") or {}
    work = answers.get("work_authorization") or {}
    logistics = answers.get("logistics") or {}

    if f.kind == _spec.HIDDEN:
        return Resolved(f, SKIP, meaning="hidden", note="hidden field, never touched")
    if f.kind == _spec.UNKNOWN:
        return Resolved(f, BLOCKER, meaning=None,
                        note="widget of an unknown kind; nothing fills what it cannot see")

    meaning = meaning_of(f)
    if f.section in (_spec.EEOC, _spec.DEMOGRAPHIC) and meaning not in SELF_ID:
        # A self-ID question whose label we could not read is still self-ID:
        # blank, never a guess.
        return _no_source(f, "self_identification",
                          "self-identification question with no matching stored entry; left blank")

    if meaning in NEVER_FILL:
        return _no_source(f, meaning, f"{meaning.replace('_', ' ')}: never filled; left for Anish")

    if meaning in SELF_ID:
        return _self_id(f, meaning, answers)

    if meaning == "resume":
        if sources.resume_path is None:
            return Resolved(f, BLOCKER, meaning=meaning,
                            note="no resume rendered for this posting; render_resume first")
        if f.kind != _spec.FILE:
            return _no_source(f, meaning, f"a {f.kind} widget cannot take the resume file")
        return Resolved(f, UPLOAD, str(sources.resume_path), "render_resume", meaning)

    if meaning == "cover_letter":
        if f.kind == _spec.FILE:
            if sources.letter_pdf is not None:
                return Resolved(f, UPLOAD, str(sources.letter_pdf), "prepare_letter(pdf=True)", meaning)
            return _no_source(f, meaning, "no reviewed letter PDF; copy_for_editing, review, then prepare_letter(pdf=True)")
        if f.kind == _spec.TEXTAREA:
            if sources.letter_text is not None:
                return Resolved(f, FILL, sources.letter_text, "prepare_letter", meaning)
            return _no_source(f, meaning, "no reviewed letter; copy_for_editing, review, then prepare_letter")
        return _no_source(f, meaning, f"a {f.kind} widget cannot take a letter")

    if meaning == "prior_employment":
        if not _employers.label_names_company(f.label, sources.company):
            # "Have you worked at a startup before?", or a non-compete question
            # that mentions "a former employer" - not about this employer, so
            # an ordinary unrecognised question, which memory may answer.
            return _no_source(f, UNRECOGNISED, f"unrecognised {f.kind} question; no source to fill it from")
        if sources.employers is None:
            return _no_source(f, meaning, "past employers unknown (bullets.yaml not loaded); left for Anish")
        if _employers.is_past_employer(sources.company, sources.employers):
            return Resolved(f, BLOCKER, meaning=meaning,
                            note=f"{sources.company} is a past employer; left for Anish")
        if f.kind not in (_spec.SELECT, _spec.YESNO, _spec.RADIO):
            return _no_source(f, meaning, f"a {f.kind} widget cannot take yes/no")
        opt = _exact_option(f, "No")
        if opt is None:
            # Checked: not a past employer. The form words its "no" as a
            # sentence ("I have not previously been employed at Affirm"), which
            # is not interpreted - but the sentence Anish picked on an earlier
            # form, with {company} templated, may fill it (learned.py). A past
            # employer never reaches here.
            return _no_source(f, PRIOR_NOT_PAST, f"no option exactly equals 'No'; options: "
                                                 f"{[o.label for o in f.options or ()]}")
        return Resolved(f, SELECT, opt.label, "employers:bullets.yaml (not a past employer)", meaning)

    if meaning == "additional_info":
        # The reviewed letter - prepare_letter reads only cover_letter.sent.md
        # and refuses otherwise - fills an additional-information box, required
        # or optional. Only a textarea: a single-line box cannot hold a letter.
        # With no reviewed letter the box is an ordinary open-ended question.
        if f.kind == _spec.TEXTAREA and sources.letter_text is not None:
            return Resolved(f, FILL, sources.letter_text, "prepare_letter", meaning,
                            note="the reviewed letter, into the additional-information box")
        return _open_ended(f, sources, meaning)

    if meaning == "phone_country":
        return _put_text(f, meaning, _text(identity.get("phone_country")),
                         "profile:identity.phone_country",
                         "identity.phone_country is not in master_profile.md §12")
    if meaning in _GH_EDUCATION:
        key = _GH_EDUCATION[meaning]
        return _put_text(f, meaning, _text((answers.get("greenhouse_education") or {}).get(key)),
                         f"answers:greenhouse_education.{key}",
                         f"greenhouse_education.{key} is not in config/answers.yaml")
    if meaning == "first_name" or meaning == "last_name":
        first, last, note = _split_name(identity)
        value = first if meaning == "first_name" else last
        return _put_text(f, meaning, value, note, note)
    if meaning == "full_name":
        return _put_text(f, meaning, _text(identity.get("name")), "profile:identity.name",
                         "identity.name is not in master_profile.md §12")
    if meaning in ("email", "phone", "linkedin", "github", "website", "location"):
        return _put_text(f, meaning, _text(identity.get(meaning)),
                         f"profile:identity.{meaning}",
                         f"identity.{meaning} is not in master_profile.md §12")
    if meaning in ("school", "degree"):
        return _put_text(f, meaning, _text(education.get(meaning)),
                         f"profile:education.{meaning}",
                         f"education.{meaning} is not in master_profile.md §12")
    if meaning == "graduation":
        return _put_text(f, meaning, _text(education.get("date")),
                         "profile:education.date",
                         "education.date is not in master_profile.md §12")
    if meaning == "work_authorization":
        return _yes_no(f, meaning, work.get("authorized_to_work_in_us"),
                       "profile:work_authorization.authorized_to_work_in_us")
    if meaning == "sponsorship":
        # "Do you require sponsorship?" and "will you, now or in the future?"
        # are different questions with different answers on STEM OPT. Any
        # mention of the future reads the future flag (a combined "now or in
        # the future" is yes if either is). Each falls back to the single
        # requires_sponsorship flag for a profile that has only that.
        key = ("requires_sponsorship_future" if _FUTURE.search(f.label)
               else "requires_sponsorship_now")
        if key in work:
            return _yes_no(f, meaning, work.get(key), f"profile:work_authorization.{key}")
        return _yes_no(f, meaning, work.get("requires_sponsorship"),
                       "profile:work_authorization.requires_sponsorship")
    if meaning == "state_of_residence":
        # Dropdowns spell it either way ("Illinois", "IL"). A select takes
        # whichever stored spelling is exactly an option, full name first;
        # text and a live-matched widget take the full name.
        spellings = [(k, _text(logistics.get(k)))
                     for k in ("state_of_residence", "state_abbreviation")]
        spellings = [(k, v) for k, v in spellings if v]
        if f.kind in (_spec.SELECT, _spec.MULTISELECT, _spec.RADIO) and f.options is not None:
            for k, v in spellings:
                if _exact_option(f, v) is not None:
                    return _put_text(f, meaning, v, f"answers:logistics.{k}", "")
        k, v = spellings[0] if spellings else ("state_of_residence", None)
        return _put_text(f, meaning, v, f"answers:logistics.{k}",
                         "logistics.state_of_residence is empty; left for Anish")
    if meaning in LOGISTICS:
        key = LOGISTICS[meaning]
        return _put_text(f, meaning, _text(logistics.get(key)),
                         f"answers:logistics.{key}",
                         f"logistics.{key} is empty; left for Anish")

    if f.kind in (_spec.TEXT, _spec.TEXTAREA):
        return _open_ended(f, sources, meaning)

    # A select, multiselect, yes/no, file, location or checkbox nobody has a
    # source for.
    return _no_source(f, meaning, f"unrecognised {f.kind} question; no source to fill it from")


# A have-you-worked-here question where the employer is checked NOT to be a
# past one but the options are sentences. Learnable, unlike prior_employment.
PRIOR_NOT_PAST = "prior_employment_not_past_employer"
# A label that matched a pattern but turned out to be about something else.
UNRECOGNISED = "unrecognised"


def _from_memory(item: Resolved, sources: Sources) -> Resolved:
    """A remembered answer, only where the table left a blocker or a blank,
    only for a learnable choice widget, only as an exact option."""
    from . import learned as _learned
    f = item.field
    if item.action not in (BLOCKER, BLANK) or sources.learned is None:
        return item
    if not _learned.learnable(f, item.meaning):
        return item
    entry = sources.learned.get(f.label, sources.company)
    if entry is None:
        return item
    seen = entry.latest.label() if entry.latest else "hand-edited"
    source = f"learned:{seen}"
    candidates = _learned.candidate_answers(entry.answer, sources.company)
    if f.options is None:
        return Resolved(f, SELECT, candidates[0], source, item.meaning,
                        note="remembered answer; options read from the live widget, "
                             "exact text only, otherwise left unset and reported")
    for wanted in candidates:
        opt = _exact_option(f, wanted)
        if opt is not None:
            return Resolved(f, SELECT, opt.label, source, item.meaning, note="remembered answer")
    note = (f"remembered answer {entry.answer!r} is not an option here "
            f"({[o.label for o in f.options]}); " + item.note)
    return Resolved(f, item.action, item.value, item.source, item.meaning, note)


def resolve(form: _spec.FormSpec, sources: Sources) -> Resolution:
    return Resolution(tuple(_from_memory(resolve_field(f, sources), sources)
                            for f in form.fields))
