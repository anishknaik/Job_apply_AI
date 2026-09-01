"""Resume rendering: bullet bank -> HTML -> PDF, verified (spec §3.2-§3.4).

Three things happen here, in this order, and the order matters:

  1. render the selected bullets into the template;
  2. enforce one page by dropping the lowest-ranked bullet and re-rendering,
     never by shrinking type below 10pt or margins below 0.5in;
  3. parse the PDF back and confirm the name, email, every employer and every
     date survived extraction.

Step 3 is the point. A resume that renders beautifully and parses to mush is
worse than a plain one, and the only way to know which you have is to read
what the consumer reads. This is the Phase 1 liveness lesson — Ashby returning
200 for dead postings — applied to a different consumer.

No model calls in this module. Selection is someone else's job; this turns a
list of bullet ids into a verified file.
"""
from __future__ import annotations

import html
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import variants
from .bullets import Bank, Bullet, match_score

log = logging.getLogger(__name__)

TEMPLATE_PATH = Path("templates/resume.html")

# §3.2 sets a 10pt floor and gives "ATS parse risk" as the reason. Measured,
# that reason does not hold: a 9pt render of this template extracted 28/28
# fields and 5,032 characters with nothing missing, because extraction reads
# the text layer and point size does not touch it. Margins can genuinely
# affect some parsers; type size does not.
#
# The floor is kept at 10pt anyway, because the real argument for it is human
# readability rather than parsing, and that is the candidate's call rather
# than a thing to quietly override. JOBPIPE_MIN_FONT_PT lowers it deliberately
# — 9.0 matches the existing hand-built resume (9.0pt Carlito) and fits about
# four more bullets.
DEFAULT_MIN_FONT_PT = 10.0
MIN_MARGIN_IN = 0.5

# Every size worth trying, largest first. The ladder actually used is filtered
# by the floor at CALL time.
FULL_LADDER = (10.8, 10.4, 10.0, 9.5, 9.0)


def min_font_pt() -> float:
    """The floor, read when it is needed rather than at import.

    This was a module-level constant and it silently did the wrong thing: the
    CLI imports `jobpipe.resume` before calling `_load_env()`, so the read
    happened against an environment that did not yet contain .env and every
    resume rendered at the 10pt default. Nothing errored — the documents were
    simply four bullets thinner than configured.

    Same shape as the Phase 1 caching bug: a silent fallback that costs
    quality with no signal attached.
    """
    return float(os.environ.get("JOBPIPE_MIN_FONT_PT", DEFAULT_MIN_FONT_PT))


def font_ladder() -> tuple[float, ...]:
    """Sizes to try, never below the floor. Margins are not on a ladder at
    all: 0.5in is hard, because narrow margins DO break some ATS parsers."""
    floor = min_font_pt()
    return tuple(pt for pt in FULL_LADDER if pt >= floor)


class RenderError(RuntimeError):
    """Rendering or verification failed. Never silent — a bad resume that
    reaches the human unflagged is the failure this module exists to prevent."""


@dataclass
class RenderResult:
    path: Path
    pages: int
    font_pt: float
    bullet_ids: list[str]
    short: bool = False
    dropped: list[str] = field(default_factory=list)
    parse: dict = field(default_factory=dict)


def _esc(text) -> str:
    return html.escape(str(text or ""), quote=False)


def _entry_html(role: dict, bullets: list[Bullet], short: bool) -> str:
    """One role block. DOM order is reading order — see the template.

    A role may declare `groups`: named workstreams inside it, each with its
    own stack line. Gradient Tensor is three distinct projects, and collapsing
    them into a flat bullet list loses both the project names and their
    technology keywords, which is most of what an ATS reads out of an
    experience section.
    """
    company = _esc(role.get("company"))
    title = _esc(role.get("title"))
    dates = _esc(role.get("dates"))
    location = _esc(role.get("location"))
    stack = _esc(role.get("stack"))

    meta = " · ".join(p for p in (dates, location) if p)
    head_title = f"{company} · {title}" if title else company

    parts = ['<div class="entry">', '  <div class="entry-head">',
             f'    <span class="entry-title">{head_title}</span>']
    if meta:
        parts.append(f'    <span class="entry-meta">{meta}</span>')
    parts.append("  </div>")
    if stack:
        parts.append(f'  <p class="entry-sub">{stack}</p>')

    groups = {g["id"]: g for g in (role.get("groups") or [])}
    ungrouped = [b for b in bullets if not b.group]
    if ungrouped:
        parts.append("  <ul>")
        for bullet in ungrouped:
            parts.append(f"    <li>{_esc(bullet.render_text(short))}</li>")
        parts.append("  </ul>")

    # Groups render in the order the role declares them, not the order the
    # selection happened to produce, so the page reads consistently whatever
    # the model returns.
    for group_id, group in groups.items():
        members = [b for b in bullets if b.group == group_id]
        if not members:
            continue
        label = _esc(group.get("name"))
        group_stack = _esc(group.get("stack"))
        if group_stack:
            label = f"{label} · <em>{group_stack}</em>"
        parts.append(f'  <p class="group-head">{label}</p>')
        parts.append("  <ul>")
        for bullet in members:
            parts.append(f"    <li>{_esc(bullet.render_text(short))}</li>")
        parts.append("  </ul>")

    parts.append("</div>")
    return "\n".join(parts)


# §3.1's emphasis table, expressed as the order the Skills section renders in.
# This is what makes `variant` a layout rather than just a different headline:
# a data-science reader sees Statistics & Analysis first and Cloud & Infra
# last, an AI/ML reader the reverse. Sections absent from a list still render,
# appended in bank order — a new skill category cannot silently disappear.
VARIANT_SKILL_ORDER = {
    "ai_ml": ["GenAI & LLM", "ML & Frameworks", "Cloud & Infra",
              "Data & Backend", "Languages", "Statistics & Analysis",
              "Analytics & BI"],
    "data_science": ["Statistics & Analysis", "ML & Frameworks", "Languages",
                     "Analytics & BI", "Data & Backend", "GenAI & LLM",
                     "Cloud & Infra"],
}

# The seven §4.2 variants join the two legacy ones rather than replacing them.
# Documents already on disk record `archetype` as `ai_ml` or `data_science`,
# and a resume regenerated from one of those rows must still render the layout
# it was sent with.
VARIANT_SKILL_ORDER.update(variants.SKILL_ORDER)


def build_html(bank: Bank, selected: list[Bullet], variant: str = "ai_ml",
               short: bool = False, font_pt: float | None = None,
               skill_order: list[str] | None = None,
               emphasise: list[str] | None = None) -> str:
    """Fill the template. Pure string work — no model, no network, no IO
    beyond reading the template itself.

    `skill_order` (from the model) beats the variant default; `emphasise` is
    the posting vocabulary used to front-load entries inside each section.
    """
    font_pt = font_pt if font_pt is not None else font_ladder()[0]
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    meta, edu = bank.meta, bank.education

    # Group selected bullets under their role, preserving the order the
    # selection gave, so a caller's ranking survives into the page.
    by_role: dict[str, list[Bullet]] = {}
    for bullet in selected:
        by_role.setdefault(bullet.role, []).append(bullet)

    sections = {"experience": [], "project": []}
    for role_id, role in bank.roles.items():
        if role_id not in by_role:
            continue
        kind = "experience" if role.get("kind") == "experience" else "project"
        sections[kind].append(_entry_html(role, by_role[role_id], short))

    surfaceable = bank.surfaceable_skills(emphasise=emphasise)
    # Model order, else the variant's default, else bank order. Any section
    # the chosen order omits is appended rather than dropped: a partial list
    # from the model must not silently delete a whole skills line.
    order = list(skill_order or VARIANT_SKILL_ORDER.get(variant) or ())
    order += [s for s in surfaceable if s not in order]

    skills_html = []
    for section in order:
        entries = surfaceable.get(section)
        if entries:
            skills_html.append(
                f"<p><strong>{_esc(section)}:</strong> "
                f"{_esc(', '.join(entries))}</p>")

    contact = " · ".join(p for p in (meta.get("email"), meta.get("phone"),
                                     meta.get("linkedin"),
                                     meta.get("location")) if p)
    # Seven variants share four headlines, so the variant name is not the
    # key. Unknown names pass through unchanged and the two legacy variants
    # keep resolving to themselves.
    headline = (meta.get("headline") or {}).get(
        variants.headline_key(variant), "")

    values = {
        "NAME": meta.get("name", ""),
        "CONTACT": contact,
        "HEADLINE": headline,
        "SCHOOL": edu.get("school", ""),
        "DEGREE": edu.get("degree", ""),
        "EDU_DATE": edu.get("date", ""),
        "COURSEWORK": edu.get("coursework", ""),
        "EXPERIENCE": "\n".join(sections["experience"]),
        "PROJECTS": "\n".join(sections["project"]),
        "SKILLS": "\n".join(skills_html),
        "FONT_SIZE": f"{font_pt:g}",
        "SMALL_SIZE": f"{font_pt - 0.8:g}",
        "NAME_SIZE": f"{font_pt + 5.2:g}",
    }
    for key, value in values.items():
        template = template.replace("{{" + key + "}}",
                                    value if key in ("EXPERIENCE", "PROJECTS",
                                                     "SKILLS")
                                    else _esc(value))
    return template


def _playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover - import guard
        raise RenderError(
            "playwright is required to render resumes: "
            "pip install playwright && python -m playwright install chromium"
        ) from exc
    return sync_playwright


# Letter minus the @page margins, at 96 CSS px/inch. WIDTH matters as much as
# height: measuring at the default 1280px viewport lets text wrap far less
# than it will at 7.4in, which understates the height badly — the first
# version of this reported 0.95 pages for content that printed to two.
PRINT_WIDTH_PX = round((8.5 - 2 * MIN_MARGIN_IN) * 96)
USABLE_PX = (11 - 2 * MIN_MARGIN_IN) * 96

# How many ordering slots one point of posting relevance is worth when the
# one-page loop decides what to cut. `match_score` caps at 2, so this is the
# maximum promotion in slots divided by two.
#
# It exists because the drop key changed from rank to position and the two
# have very different ranges. Against ranks 1-4, a promotion of 2 was half the
# scale and decisive. Against 21 positions it would be noise, and §4.2 leans
# on relevance being the thing that makes ONE fixed ordering behave
# differently for different postings - without it the tail is identical every
# time and the variants may as well be static documents.
#
# 2 puts the ceiling at four slots: a matching bullet sitting just under the
# fold can cross it, while one near the bottom of the ordering still cannot
# out-argue the lane's design. Tune here, not per posting.
RELEVANCE_SLOTS = 2


def measure_px(html_text: str) -> float:
    """Content height in CSS pixels, without producing a PDF.

    Measured at the printable width and under print media, so it predicts the
    PDF rather than the screen. The loop uses this to decide how much to trim;
    page count alone is far too coarse a signal to converge on.
    """
    with _playwright()() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page(
                viewport={"width": PRINT_WIDTH_PX, "height": int(USABLE_PX)})
            page.emulate_media(media="print")
            page.set_content(html_text, wait_until="load")
            return float(page.evaluate("document.body.scrollHeight"))
        finally:
            browser.close()


def _render_pdf(html_text: str, out: Path) -> int:
    """HTML -> PDF via Chromium. Returns the page count."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with _playwright()() as p:
        browser = p.chromium.launch()
        try:
            page = browser.new_page()
            page.set_content(html_text, wait_until="load")
            page.pdf(path=str(out), format="Letter",
                     print_background=False,
                     # Margins live in the stylesheet's @page rule so there is
                     # one home for them; passing them here too would let the
                     # two disagree silently.
                     prefer_css_page_size=True)
        finally:
            browser.close()

    from pypdf import PdfReader
    return len(PdfReader(str(out)).pages)


def extract_text(pdf_path: Path) -> str:
    """What the ATS actually sees."""
    from pypdf import PdfReader

    reader = PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _normalise(text: str) -> str:
    """Fold the differences that are cosmetic to a human but fatal to a match.

    Ligatures are the reason this exists: the template disables them, and this
    unfolds any that slip through anyway so the check reports a real failure
    rather than an encoding artefact.
    """
    for lig, plain in (("ﬀ", "ff"), ("ﬁ", "fi"), ("ﬂ", "fl"),
                       ("ﬃ", "ffi"), ("ﬄ", "ffl")):
        text = text.replace(lig, plain)
    text = (text.replace("–", "-").replace("—", "-")
                .replace("’", "'").replace(" ", " ")
                .replace("·", " "))
    return re.sub(r"\s+", " ", text).strip().lower()


def ats_parse_check(pdf_path: Path, bank: Bank, selected: list[Bullet],
                    short: bool = False) -> dict:
    """Confirm the fields an ATS keys on survived extraction (§3.3).

    Checks name, email, every employer whose bullets appear, and every date
    string. Employers and dates are what a parser uses to build the work
    history; losing one silently truncates the candidate's career.
    """
    text = _normalise(extract_text(pdf_path))
    meta = bank.meta
    missing, checked = [], 0

    def want(label: str, value: str):
        nonlocal checked
        if not value:
            return
        checked += 1
        if _normalise(value) not in text:
            missing.append(f"{label}: {value!r}")

    want("name", meta.get("name", ""))
    want("email", meta.get("email", ""))

    for role_id in {b.role for b in selected}:
        role = bank.roles.get(role_id, {})
        want("employer", role.get("company", ""))
        if role.get("dates"):
            want("dates", role["dates"])

    # A bullet that vanished entirely is a rendering failure, not a parse
    # nuance. Check a distinctive fragment rather than the whole string, since
    # extraction inserts line breaks mid-sentence.
    #
    # `short` must match what was actually rendered. Checking the long text
    # against a page built from short variants reports five failures that are
    # really one bug in the checker — which is exactly what it did first time.
    for bullet in selected:
        fragment = " ".join(bullet.render_text(short).split()[:6])
        checked += 1
        if _normalise(fragment) not in text:
            missing.append(f"bullet {bullet.id}: {fragment!r}")

    # Hyphenated compounds are the subtle one. A line break at an existing
    # hyphen can extract as "citation-" + "grounded" fused into
    # "citationgrounded", which matches neither term. Found on the first real
    # render, and invisible to a first-six-words check.
    #
    # Reported as a warning rather than a failure: it degrades keyword
    # matching for one compound, it varies by extractor, and there is no
    # honest fix at render time — a non-breaking hyphen changes the codepoint,
    # which is the ligature bug wearing a different hat.
    fused = []
    for bullet in selected:
        for compound in re.findall(r"\b[\w]+-[\w]+\b",
                                   bullet.render_text(short)):
            want = _normalise(compound)
            if want in text:
                continue
            # Two ways it breaks, and both must be caught. pypdf keeps the
            # hyphen and inserts a newline ("citation-\ngrounded"); other
            # extractors drop the hyphen and fuse ("citationgrounded").
            # Checking only the fused form reported a clean bill on a PDF that
            # was demonstrably split.
            left, right = compound.split("-", 1)
            split_form = f"{_normalise(left)}- {_normalise(right)}"
            if _normalise(compound.replace("-", "")) in text:
                fused.append(f"{compound} (hyphen dropped, tokens fused)")
            elif split_form in text:
                fused.append(f"{compound} (line break inside the word)")

    return {"ok": not missing, "checked": checked, "missing": missing,
            "fused_hyphens": fused, "chars": len(text)}


# Drop from the END of the given order, promoting what this posting
# actually asks for.
#
# `rank` was the primary key here and it was the wrong comparator.
# rank orders bullets WITHIN a role — gt-lesson-plan 1, gt-nbs7 2,
# gt-remittance 3, gt-ownership 4, all inside Gradient Tensor — so
# using it across roles asked whether Gradient Tensor's third bullet
# is worse than Brainayan's first, which it was never meant to answer.
# Dropping the high numbers first removed every 3 and 4 before
# touching any 2, and because every role has a rank-1 bullet, every
# role survived. Measured across all seven variants: 14 bullets spread
# over 9 of 10 roles, every time. That is the sample-everything,
# lead-nothing shape tailor's own selection prompt warns costs a third
# of the content to the page limit. It also cut swe_backend's
# position-1 bullet, gt-remittance, from every posting that did not
# happen to name payments.
#
# Position is the right key because the incoming list is ordered
# strongest-first by whoever built it: a variant ordering (§4.2), or
# the model's own order, which tailor.rank_selection already preserves
# and which rank was overriding.
#
# Relevance still promotes — this is what kept `gt-ownership`, rank 4
# and irrelevant to most postings, on the Founding Engineer resume it
# belonged on. Ties break by position, so the result stays
# deterministic and reproducible.
def drop_weakness(bullet: Bullet, working: list[Bullet],
                  emphasise: list[str] | None = None) -> tuple:
    relevance = min(match_score(bullet, emphasise or []), 2)
    position = working.index(bullet)
    return (position - relevance * RELEVANCE_SLOTS, position)


def weakest(working: list[Bullet],
            emphasise: list[str] | None = None) -> Bullet:
    """The bullet the one-page loop cuts next.

    Module level, and the loop calls it, so the suite can exercise the real
    decision. It used to be a closure inside render(), and the tests mirrored
    the formula instead of calling it - which meant they went on passing
    unchanged when the formula was replaced, asserting a policy the code no
    longer had.
    """
    return max(working, key=lambda b: drop_weakness(b, working, emphasise))


def render(bank: Bank, selected: list[Bullet], out: Path,
           variant: str = "ai_ml", skill_order: list[str] | None = None,
           emphasise: list[str] | None = None,
           verify: bool = True) -> RenderResult:
    """Render to a verified one-page PDF, or raise.

    The one-page loop (§3.2), in the order the spec prescribes: try the type
    ladder down to the 10pt floor, then start dropping the lowest-ranked
    bullet. Never shrink below the floor — a 9pt resume that parses badly has
    traded the only thing that matters for the appearance of fitting.
    """
    out = Path(out)
    working = list(selected)
    dropped: list[str] = []

    while True:
        # Cheap pass first: find the least aggressive setting whose measured
        # height fits, and only then spend a PDF render on it. Rendering every
        # combination is both slow and, because page count is coarse, worse at
        # choosing.
        candidates = [(f, s) for f in font_ladder() for s in (False, True)
                      if measure_px(build_html(bank, working, variant=variant,
                                               short=s, font_pt=f,
                                               skill_order=skill_order,
                                               emphasise=emphasise))
                      <= USABLE_PX]
        for font_pt, short in candidates:
            pages = _render_pdf(
                build_html(bank, working, variant=variant, short=short,
                           font_pt=font_pt, skill_order=skill_order,
                           emphasise=emphasise), out)
            if pages <= 1:
                result = RenderResult(path=out, pages=pages, font_pt=font_pt,
                                      bullet_ids=[b.id for b in working],
                                      short=short, dropped=dropped)
                if verify:
                    result.parse = ats_parse_check(out, bank, working,
                                                   short=short)
                    if not result.parse["ok"]:
                        raise RenderError(
                            "the PDF rendered but did not parse back cleanly, "
                            "which is worse than a plain resume:\n  " +
                            "\n  ".join(result.parse["missing"][:8]))
                log.info("rendered %d bullet(s) at %.1fpt%s", len(working),
                         font_pt, " (short variants)" if short else "")
                return result

        if len(working) <= 4:
            raise RenderError(
                f"cannot fit onto one page even at {min_font_pt()}pt with only "
                f"{len(working)} bullets. Something is wrong with the "
                f"template rather than the selection.")

        victim = weakest(working, emphasise)
        working.remove(victim)
        dropped.append(victim.id)
        log.info("dropped %s to fit one page", victim.id)
