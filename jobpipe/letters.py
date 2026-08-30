"""Cover letter generation (Phase 2 spec §4, §9.8).

The only genuinely generated prose in the project, which makes it the only
place fabrication is possible at all. The resume cannot invent anything — its
text is selected from a fixed bank. A letter is written, so §2.2's grounding
check (step 9) exists for this module alone.

Voice comes from the candidate's own letters (§4.1). Two constraints in this
file were derived by reading all seven of them rather than taken on trust,
because the spec's characterisation was close but not exact:

  * §4.1 says the NORC letter "opens with a reason for wanting that work
    rather than a restatement of the job title". It does not — all seven open
    by restating the title. What distinguishes NORC is the SECOND sentence: it
    states a position ("I treat evaluation as an experimental design problem
    rather than a leaderboard exercise") where the weaker letters recite a
    credential ("I graduated from Rutgers with a B.S."). That is the usable
    rule, and it is what this prompt encodes.
  * §4.1 says four of five letters close on "I would welcome the chance to
    bring that combination of X, Y and Z". Three do, verbatim, varying only
    the three nouns. But SIX of seven close on "welcome the chance" in some
    form, so the ban here is broader than the spec's.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from .bullets import Bank, Bullet
from .providers import ScoringProvider

log = logging.getLogger(__name__)

LETTERS_DIR = Path("samples/cover_letters")

# Deliberately curated, not "every letter in the folder".
#
# EXCLUDED, and why it matters: McKinsey_Cover_Letter opens "As the co-founder
# of an AI company", a claim that appears nowhere in master_profile.md, which
# records Gradient Tensor as "AI Engineer". Whether the letter overstated or
# the profile is incomplete is the human's to resolve — but a few-shot example
# containing an unsupported claim teaches the model to make it, and that is
# the exact failure §2 is built to prevent.
#
# ALSO EXCLUDED: CityOfHope, Fanatics and Fervorly. All three close on the
# identical "bring that combination of X, Y and Z" scaffold, and all three
# follow the title restatement with "I graduated from Rutgers with a B.S.".
# Showing the model the pattern this prompt bans would be self-defeating.
VOICE_EXEMPLARS = (
    ("Anish_Naik_Cover_Letter_NORC_1.pdf",
     "Research institution. The anchor: states a position in the second "
     "sentence, and describes what he found absorbing rather than what he "
     "achieved."),
    ("Anish_Naik_TargetedVictory_Cover_Letter.pdf",
     "Engineering role. Concrete about the daily work, and names the kind of "
     "problem he likes rather than listing adjectives."),
)

# Phrases measured across the corpus. Banned because they are HIS tells, not
# because they are bad writing — a letter that reads as generated from a
# template is worse than a plainer one that reads as written.
BANNED_PHRASES = (
    "welcome the chance",
    "bring that combination",
    "combination of",
    "I am writing to apply",
    "I'm writing to apply",
    "I graduated from Rutgers",
    "Thank you for your time and consideration",
    # Added after measuring the model's own output rather than his. Banning
    # the phrases above moved the tell instead of removing it: three
    # generated letters closed on "look forward to discussing how my
    # experience [with X] can support [Company]'s [thing]" — the same
    # template with different words. Phrase bans alone play whack-a-mole,
    # which is why CLOSING_SHAPE_RULE below constrains the shape as well.
    "look forward to discussing",
    "eager to bring",
    "can support",
    "excited to bring",
    "would love the opportunity",
)

# The structural half. Every closing the model produced had the same subject
# ("I"), the same verb class (eagerness), and the same object (a summary of
# the letter). Naming the pattern and offering concrete alternatives works
# where a list of forbidden strings does not.
CLOSING_SHAPE_RULE = """\
The closing paragraph must NOT be a sentence whose subject is "I" and whose \
verb expresses eagerness — look forward, would welcome, am eager, would love, \
am excited. That construction is the single most repeated shape in both his \
letters and earlier drafts of yours, and it says nothing.

It must also not summarise the letter. If the closing lists what the previous \
paragraphs already established, delete it and write something else.

Pick a different shape each time. Any of these work:
  * End on the problem, not on himself — a sentence about what is hard or \
interesting in the work the team is doing.
  * Ask one specific question the posting left open.
  * State one concrete logistical fact plainly and stop (location, notice \
period, availability) with no flourish after it.
  * Name the thing he would want to learn from this team.

Two sentences at most. A short flat close reads as confident; a long warm one \
reads as generated."""

# Measured against his own letters rather than taken from §4.2, which says
# ~350. His run 360-431 words at a strikingly consistent 16 sentences each.
# The first generated batch came in at 249-309 words and 11-13 sentences —
# noticeably thinner on the page, which is the first thing he noticed.
TARGET_WORDS = 390
MIN_WORDS = 340


class LetterError(RuntimeError):
    """Generation failed or produced something unusable."""


# --- letter structure: ONE definition -------------------------------------
#
# The four body slots appeared in three places — LETTER_SCHEMA below,
# body_text()'s ordering, and measure.SLOT_NAMES — and the copies drifted.
# SLOT_NAMES listed only the four body slots, but assemble() also emits a
# header, a salutation and a sign-off, and measure splits on blank lines. So a
# real letter is SEVEN blocks and every per-slot label was two positions off:
# block 0 is the name/contact/date header, not "why this role". The CLI calls
# that number "the number that says which instruction is wrong" — it named the
# wrong instruction.
#
# Everything derives from here, and test_letters asserts the assembled shape
# matches BLOCK_NAMES, so adding a block without naming it fails the suite.

BODY_SLOTS = (
    ("why_this_role", "why this role"),
    ("primary_evidence", "primary evidence"),
    ("secondary_evidence", "secondary evidence"),
    ("closing", "closing"),
)
BODY_KEYS = tuple(key for key, _ in BODY_SLOTS)

# Scaffolding assemble() wraps around the body. These are real blocks, so they
# shift the body's indices — that offset is the whole bug above.
LEADING_BLOCK_NAMES = ("header", "salutation")
TRAILING_BLOCK_NAMES = ("sign-off",)

# Every block of an ASSEMBLED letter, in the order measure splits them. A bare
# body (assemble() with no row and no meta) has only the four body blocks.
BLOCK_NAMES = (LEADING_BLOCK_NAMES
               + tuple(name for _, name in BODY_SLOTS)
               + TRAILING_BLOCK_NAMES)
BODY_OFFSET = len(LEADING_BLOCK_NAMES)


LETTER_SCHEMA = {
    "type": "object",
    "properties": {
        "why_this_role": {
            "type": "string",
            "description": "Paragraph 1. Something specific and true about "
                           "this company, team or problem — drawn from the "
                           "posting, not from the job title. Never open with "
                           "'I am writing to apply'.",
        },
        "primary_evidence": {
            "type": "string",
            "description": "Paragraph 2. The strongest matching evidence, "
                           "with its metric. Must correspond to a bullet in "
                           "the bank.",
        },
        "secondary_evidence": {
            "type": "string",
            "description": "Paragraph 3. A second thread, ideally the rigor "
                           "or evaluation angle.",
        },
        "closing": {
            "type": "string",
            "description": "Paragraph 4. Short. Must not use any banned "
                           "phrase, and must not restate the paragraphs "
                           "above as a three-item list.",
        },
    },
    "required": list(BODY_KEYS),
    "additionalProperties": False,
}


def load_exemplars(directory: Path = LETTERS_DIR) -> list[tuple[str, str]]:
    """Read the curated letters. Returns [(note, text)]."""
    from pypdf import PdfReader

    out = []
    for filename, note in VOICE_EXEMPLARS:
        path = Path(directory) / filename
        if not path.exists():
            log.warning("voice exemplar missing: %s", path)
            continue
        text = " ".join(" ".join(
            page.extract_text() or "" for page in PdfReader(str(path)).pages
        ).split())
        out.append((note, text))
    if not out:
        raise LetterError(
            f"no voice exemplars found in {directory}. Cover letters are "
            f"generated from the candidate's own letters; without them the "
            f"output is generic by construction.")
    return out


def build_system_prompt(bank: Bank, profile: str,
                        exemplars: list[tuple[str, str]]) -> str:
    """Cacheable static prefix: rules, voice examples, profile, bank."""
    lines = [
        "You write one cover letter, in this candidate's own voice, for a "
        "specific posting.",
        "",
        "=== THE HARD CONSTRAINT ===",
        "",
        "Every factual claim must be supported by the master profile or the "
        "bullet bank below. Not 'consistent with' — supported. If you cannot "
        "point to the sentence it comes from, do not write it.",
        "",
        "This is the only part of this system that writes prose, so it is the "
        "only place a fabricated claim can enter. A hallucinated metric on an "
        "application is career-damaging in a way no other error here is. "
        "A second pass will check every claim against the profile and block "
        "the draft if any is unsupported.",
        "",
        "Specifically:",
        "  * No metric that is not in the bank, verbatim.",
        "  * No employer, title, date or technology that is not in the "
        "profile.",
        "  * No claim about scale, team size, or business impact beyond what "
        "is written.",
        "  * Absence is honest. If the posting wants something he has not "
        "done, write about what he has done instead — never imply the gap "
        "is filled.",
        "",
        "=== STRUCTURE (spec §4.2) ===",
        "",
        f"Four body paragraphs, {MIN_WORDS}-{TARGET_WORDS + 40} words in "
        f"total. Aim for {TARGET_WORDS}.",
        "",
        f"Length is a real constraint, not a suggestion. His own letters run "
        f"360-431 words across sixteen sentences; earlier drafts of yours came "
        f"in at 250-300 and read visibly thin beside them. Write four "
        f"substantial paragraphs, roughly four sentences each. Do not pad — "
        f"if a paragraph is short, it is because you have not said the "
        f"specific thing yet.",
        "",
        "  1. Why this role specifically. Something true about the company, "
        "team or problem, taken from the posting. Not the job title.",
        "  2. The strongest matching evidence, with its metric.",
        "  3. A second thread, ideally the rigor or evaluation angle.",
        "  4. A short close.",
        "",
        "=== VOICE ===",
        "",
        "Read the examples below. They are his. Match their register: plain, "
        "specific, technical without being showy, and more interested in the "
        "problem than in himself.",
        "",
        "Two rules taken from measuring his own letters:",
        "",
        "**State a position, do not recite a credential.** His strongest "
        "letter follows the opening with 'I treat evaluation as an "
        "experimental design problem rather than a leaderboard exercise'. His "
        "weakest three follow it with 'I graduated from Rutgers with a B.S.' "
        "The degree is on the resume. The second sentence is where you say "
        "what he thinks.",
        "",
        "**Say what he found absorbing, not what he achieved.** The metric "
        "belongs in the letter, but the sentence around it should be about "
        "the problem — why the ablation design was the interesting part, not "
        "that the number was large.",
        "",
        "=== PHRASES YOU MAY NOT USE ===",
        "",
        "These are his tells, measured across seven letters. Six of seven "
        "close on 'welcome the chance'; three close on the identical 'bring "
        "that combination of X, Y and Z' scaffold with only the nouns "
        "changed. A letter that reads as generated from a template is worse "
        "than a plainer one that reads as written.",
        "",
    ]
    lines += [f"  * \"{phrase}\"" for phrase in BANNED_PHRASES]
    lines += [
        "",
        "=== HOW THE LETTER CLOSES ===",
        "",
        CLOSING_SHAPE_RULE,
        "",
        "=== DO NOT RAISE WORK AUTHORISATION (spec §4.3) ===",
        "",
        "Never mention visa status, sponsorship, or work authorisation. "
        "Application forms ask directly and are answered truthfully there; "
        "raising it here invites a screen-out before anyone reads the "
        "evidence. The single exception is a posting that explicitly asks "
        "candidates to state their status in the letter.",
        "",
        "=== HIS OWN LETTERS ===",
        "",
    ]
    for index, (note, text) in enumerate(exemplars, 1):
        lines += [f"--- EXAMPLE {index}: {note} ---", "", text, ""]

    lines += ["=== BULLET BANK (the only evidence you may cite) ===", ""]
    for bullet in bank.bullets.values():
        lines.append(f"  [{bullet.id}] {bullet.render_text()}")

    lines += ["", "=== CANDIDATE MASTER PROFILE ===", "", profile]
    return "\n".join(lines)


def slug(row) -> str:
    """A stable, filesystem-safe name for one application."""
    def field(name):
        try:
            return str(row[name] or "")
        except (KeyError, IndexError):
            return ""

    raw = f"{field('company')}-{field('title')}"
    cleaned = re.sub(r"[^a-z0-9]+", "-", raw.lower()).strip("-")
    return cleaned[:70] or "application"


def save_letter(text: str, path: Path) -> Path:
    """Write the letter as clean prose and nothing else.

    Deliberately no front matter, header or metadata block. §7 measures edit
    distance between this file and the one actually sent, so anything the
    human has to delete before sending would register as an edit and pollute
    the only real quality signal in the phase. Metadata belongs in the
    `documents` table (§6), which is why that table exists.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    return path


def banned_used(text: str) -> list[str]:
    """Which banned phrases survived. Checked, not merely instructed."""
    lowered = text.lower()
    return [p for p in BANNED_PHRASES if p.lower() in lowered]


def body_text(payload: dict) -> str:
    """The four generated paragraphs, with no scaffolding."""
    parts = [payload.get(k, "").strip() for k in BODY_KEYS]
    return "\n\n".join(p for p in parts if p)


def salutation(row) -> str:
    """'Dear <Company> Hiring Team,' — matching his own letters.

    Assembled here rather than generated. It is scaffolding, not prose: a
    model asked to write it will occasionally invent a hiring manager's name,
    which is both wrong and the exact failure class this phase guards against.
    """
    try:
        company = str(row["company"] or "").strip()
    except (KeyError, IndexError):
        company = ""
    return f"Dear {company} Hiring Team," if company else "Dear Hiring Team,"


def assemble(payload: dict, row=None, meta: dict | None = None,
             today: str | None = None) -> str:
    """The full letter as he sends it: date, salutation, body, sign-off.

    His seven letters all carry these; the first generated batch carried none
    of them, which is why they did not look like his. The model writes only
    the four body paragraphs — everything around them is fixed text assembled
    from the profile, so there is nothing here to hallucinate.
    """
    body = body_text(payload)
    if row is None and meta is None:
        return body                       # bare body, for tests and diffing

    import datetime
    # "%-d" is not portable to Windows, so strip the leading zero by hand:
    # "August 05, 2026" -> "August 5, 2026", matching his letters.
    stamp = today or re.sub(
        r"\b0(\d),", r"\1,", datetime.date.today().strftime("%B %d, %Y"))

    name = (meta or {}).get("name", "")
    contact = " · ".join(p for p in ((meta or {}).get("email"),
                                     (meta or {}).get("phone")) if p)

    head = [p for p in (name, contact, stamp) if p]
    return "\n".join([
        "\n".join(head), "",
        salutation(row) if row is not None else "Dear Hiring Team,", "",
        body, "",
        "Sincerely,",
        name,
    ]).rstrip() + "\n"


def generate_letter(provider: ScoringProvider, bank: Bank, row,
                    system_prompt: str, model: str,
                    selected: list[Bullet] | None = None,
                    retry_on_banned: bool = True) -> dict:
    """Generate one letter. Raises rather than returning something unusable.

    A banned phrase triggers exactly one retry with the offenders named. Not
    a loop: if the model cannot avoid its own tells in two attempts the letter
    needs a human, and an unbounded retry would quietly spend money on the
    expensive model.
    """
    user = _posting_message(row, selected)
    offenders: list[str] = []

    for attempt in range(2 if retry_on_banned else 1):
        text_in = user
        if offenders:
            text_in = (f"{user}\n\nYour previous draft used these banned "
                       f"phrases: {', '.join(offenders)}. Rewrite without "
                       f"them. Do not simply reword around the same shape — "
                       f"change what the sentence does.")

        response = provider.score(model=model, system_prompt=system_prompt,
                                  user_text=text_in, schema=LETTER_SCHEMA)
        if response.refused:
            raise LetterError(
                f"letter refused by {model}: {response.refusal_reason}")

        payload = json.loads(response.text or "")
        body = body_text(payload)
        if not body.strip():
            raise LetterError("letter generation returned empty prose")

        # Bans are checked against the generated body only. The scaffolding
        # is fixed text and cannot contain a tell.
        offenders = banned_used(body)
        if not offenders:
            return _result(payload, body, model, response, attempt, row=row,
                           bank=bank)
        log.warning("draft used banned phrase(s) %s%s", offenders,
                    "; retrying" if attempt == 0 else "")

    return _result(payload, body, model, response, 1, banned=offenders,
                   row=row, bank=bank)


def _result(payload, body, model, response, attempts, banned=None,
            row=None, bank=None) -> dict:
    full = assemble(payload, row=row,
                    meta=(bank.meta if bank is not None else None))
    return {
        "paragraphs": payload,
        "body": body,
        "text": full,
        "words": len(body.split()),
        "model": model,
        "attempts": attempts + 1,
        # Reported rather than raised: the draft is for human review, and a
        # letter that is good apart from one phrase is worth showing with the
        # phrase flagged.
        "banned_used": banned or [],
        "usage": {"input": response.input_tokens,
                  "output": response.output_tokens,
                  "cached": response.cached_tokens},
    }


def _posting_message(row, selected: list[Bullet] | None) -> str:
    def field(name, default=""):
        try:
            value = row[name]
        except (KeyError, IndexError):
            return default
        return default if value is None else value

    parts = [f"Company: {field('company')}",
             f"Title: {field('title')}",
             f"Location: {field('location')}", ""]
    if selected:
        parts += ["The resume for this application leads with these bullets. "
                  "Draw your evidence paragraphs from them:", ""]
        parts += [f"  [{b.id}] {b.render_text()}" for b in selected[:6]]
        parts.append("")
    parts.append(str(field("description"))[:12_000])
    return "\n".join(parts)


def word_count_ok(text: str, minimum: int = MIN_WORDS,
                  maximum: int = TARGET_WORDS + 60) -> bool:
    """Body length, against his measured range rather than §4.2's ~350."""
    return minimum <= len(text.split()) <= maximum


def paragraph_shapes(text: str) -> list[str]:
    """Coarse shape of each paragraph, for spotting a formulaic close."""
    out = []
    for para in text.split("\n\n"):
        sentences = re.split(r"(?<=[.!?])\s+", para.strip())
        out.append(f"{len(sentences)}s/{len(para.split())}w")
    return out
