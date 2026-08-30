"""Stage A: deterministic filters. No model, no cost.

Rules live in config/filters.yaml so they can be tuned without code changes.
Every posting records both the pass/fail and the rule that decided it, so the
discard pile is auditable:

    python -m jobpipe.cli audit --limit 40

Evaluation order matters — the first failing rule short-circuits and is the
one recorded. Cheapest and most decisive rules run first.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml

DEFAULT_FILTERS_PATH = (Path(__file__).resolve().parent.parent
                        / "config" / "filters.yaml")


@dataclass
class FilterRules:
    title_require_any: list[str]
    title_exclude: list[str]
    title_exclude_unless: list[str]
    location_allow: list[str]
    location_exclude: list[str]
    max_age_days: int | None
    max_age_exempt_titles: list[str]
    max_years_experience: int | None
    sponsorship_allow: list[str]
    sponsorship_exclude: list[str]
    description_exclude: list[str]


def load_rules(path: Path | str = DEFAULT_FILTERS_PATH) -> FilterRules:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"filters file not found: {path}")

    with path.open(encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}

    def terms(key: str) -> list[str]:
        value = raw.get(key) or []
        if not isinstance(value, list):
            raise ValueError(f"{path}: {key} must be a list, got {type(value).__name__}")
        return [str(v).lower() for v in value if str(v).strip()]

    max_age = raw.get("max_age_days")
    if max_age is not None:
        max_age = int(max_age)

    max_years = raw.get("max_years_experience")
    if max_years is not None:
        max_years = int(max_years)

    return FilterRules(
        title_require_any=terms("title_require_any"),
        title_exclude=terms("title_exclude"),
        title_exclude_unless=terms("title_exclude_unless"),
        location_allow=terms("location_allow"),
        location_exclude=terms("location_exclude"),
        max_age_days=max_age,
        max_age_exempt_titles=terms("max_age_exempt_titles"),
        max_years_experience=max_years,
        sponsorship_allow=terms("sponsorship_allow"),
        sponsorship_exclude=terms("sponsorship_exclude"),
        description_exclude=terms("description_exclude"),
    )


@lru_cache(maxsize=64)
def _compile_terms(terms: tuple[str, ...]) -> re.Pattern | None:
    """Compile a term list into one word-boundary alternation.

    Word boundaries rather than plain substring matching, because substrings
    silently produce both kinds of error:
        'intern' matched "Member of Technical Staff, Internal Agent"
        'ny'     matched "Germany" and "Sunnyvale"
    Cached because this runs once per posting per rule list.
    """
    if not terms:
        return None
    alternation = "|".join(re.escape(t) for t in sorted(terms, key=len, reverse=True))
    return re.compile(rf"\b(?:{alternation})\b")


def _first_match(haystack: str, needles: list[str]) -> str | None:
    pattern = _compile_terms(tuple(needles))
    if pattern is None:
        return None
    found = pattern.search(haystack)
    return found.group(0) if found else None


def mask_protected(title: str, protected: list[str]) -> str:
    """Blank out protected phrases before running the title blocklist.

    Exists for one specific, high-cost collision: "Member of Technical Staff"
    is the standard individual-contributor engineering title at most AI labs,
    and a bare `staff` entry in title_exclude silently deletes every one of
    them. Masking the phrase rather than whitelisting the posting keeps the
    rest of the blocklist live, so "Staff Engineer, Member of Technical Staff"
    would still be caught on `staff`.
    """
    masked = title
    for phrase in protected:
        if phrase in masked:
            masked = masked.replace(phrase, " ")
    return masked


# Locations arrive as free text and are frequently multi-valued:
#   "San Francisco, CA | New York City, NY"
#   "United Kingdom; Dublin; United States; New York; Sofia"
_LOCATION_SPLIT = re.compile(r"[;|/,]")


def _location_tokens(location: str) -> list[str]:
    return [part.strip() for part in _LOCATION_SPLIT.split(location)
            if part.strip()]


def _location_verdict(location: str, remote_flag: bool,
                      rules: FilterRules) -> tuple[bool, str]:
    """Decide a posting's location by evaluating each listed location alone.

    A posting is kept if *any single* location it names is somewhere you would
    work. Evaluating the raw string as one blob is what made
    "San Francisco; New York City; London, UK" fail on `london` — a genuine
    New York role thrown away because it is also open in London.
    """
    tokens = _location_tokens(location)
    if not tokens:
        return True, "passed"

    excluded_hits = []
    for token in tokens:
        bad = _first_match(token, rules.location_exclude)
        if bad:
            excluded_hits.append(bad)
            continue
        if not rules.location_allow or _first_match(token, rules.location_allow):
            return True, "passed"

    # No token was independently acceptable.
    if remote_flag and not excluded_hits:
        return True, "passed"
    if excluded_hits:
        return False, f"location_exclude: {excluded_hits[0]!r}"
    return False, f"location_allow: {location[:60]!r} not allowed"


# A years-of-experience requirement, anchored to the word "experience" so a
# bare number elsewhere in the posting cannot trigger it. Both orderings occur
# in the wild:
#     "5+ years of experience in ML"
#     "experience: 5-7 years"
# The range forms are captured so the LOWER bound is what counts — a posting
# asking for "3-5 years" has a real bar of 3, and reading it as 5 would
# discard a role the candidate could get.
_YOE_NUMBER = r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(?:\d{1,2})?\s*\+?\s*(?:years?|yrs?)"
_YOE_BEFORE = re.compile(_YOE_NUMBER + r"[^.;!?\n]{0,60}?\bexperience\b",
                         re.IGNORECASE)
_YOE_AFTER = re.compile(r"\bexperience\b[^.;!?\n]{0,60}?" + _YOE_NUMBER,
                        re.IGNORECASE)
# Requirements are not always written with the word "experience". OpenAI's
# "10+ years in a quantitative role such as Data Science" sailed past an
# experience-only anchor and reached scoring, where it was correctly rated 1 —
# an avoidable call.
_YOE_ROLE = re.compile(_YOE_NUMBER + r"\s+(?:in|of|as)\b", re.IGNORECASE)

# Phrases that mean the number is a ceiling or an aspiration rather than a
# hard floor. "up to 5 years" and "0-2 years" should never kill a posting.
_YOE_SOFTENER = re.compile(r"\b(up to|less than|fewer than|at most|maximum)\b",
                           re.IGNORECASE)

# Contexts where a year count is not a requirement at all. Needed because the
# looser _YOE_ROLE pattern would otherwise read "2 years of tenure" and
# "10 years of operating history" as hiring bars.
_YOE_NOT_A_REQUIREMENT = re.compile(
    r"\b(ago|history|tenure|founded|anniversary|in business|operating|"
    r"vesting|sabbatical|warranty|retention)\b", re.IGNORECASE)


def minimum_years_required(description: str) -> int | None:
    """Lowest years-of-experience floor stated in the description, if any."""
    if not description:
        return None

    found: list[int] = []
    for pattern in (_YOE_BEFORE, _YOE_AFTER, _YOE_ROLE):
        for match in pattern.finditer(description):
            window = description[max(0, match.start() - 40):match.end() + 40]
            if _YOE_SOFTENER.search(window):
                continue
            if _YOE_NOT_A_REQUIREMENT.search(window):
                continue
            try:
                found.append(int(match.group(1)))
            except (TypeError, ValueError):
                continue

    return min(found) if found else None


# Sentences mentioning sponsorship or work authorisation, pulled out so Stage B
# can be shown them directly rather than hunting through a 6,000-character
# description for a clause that decides the whole posting.
_SPONSORSHIP_SENTENCE = re.compile(
    r"[^.\n]{0,200}?\b(?:sponsor\w*|work authoriz\w*|work authoris\w*|"
    r"employment authoriz\w*)\b[^.\n]{0,200}",
    re.IGNORECASE,
)


def extract_sponsorship_sentences(description: str | None,
                                  limit: int = 4) -> list[str]:
    """Return the sentences that mention sponsorship or work authorisation.

    Stage A can only decide the unambiguous cases. Anthropic proves why: its
    postings carry a company-wide "We do sponsor visas!" *and*, on the Fellows
    programme, "We are not currently able to sponsor visas for fellows".
    Deciding which statement governs the role is reading comprehension, so the
    sentences are handed to Stage B intact and judged there.
    """
    if not description:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in _SPONSORSHIP_SENTENCE.finditer(description):
        sentence = " ".join(match.group(0).split()).strip(" -–—:;,")
        key = sentence.lower()
        if sentence and key not in seen:
            seen.add(key)
            out.append(sentence)
        if len(out) >= limit:
            break
    return out


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def evaluate(posting_row, rules: FilterRules,
             now: datetime | None = None) -> tuple[bool, str]:
    """Return (passed, reason). `reason` names the deciding rule either way.

    Accepts anything with the posting fields by key — an sqlite3.Row from the
    database or a plain dict in tests.
    """
    now = now or datetime.now(timezone.utc)

    title = (posting_row["title"] or "").lower()
    location = (posting_row["location"] or "").lower()
    description = (posting_row["description"] or "").lower()
    remote_flag = bool(posting_row["remote_flag"])
    posted_at = posting_row["posted_at"]

    # 1. Title blocklist, evaluated against the masked title so protected
    #    phrases (e.g. "member of technical staff") don't trip `staff`.
    #    Runs before the allowlist so an exclusion always wins.
    hit = _first_match(mask_protected(title, rules.title_exclude_unless),
                       rules.title_exclude)
    if hit:
        return False, f"title_exclude: {hit!r}"

    # 2. Title allowlist, against the original title.
    if rules.title_require_any:
        if not _first_match(title, rules.title_require_any):
            return False, "title_require_any: no required term in title"

    # 3+4. Location. Multi-valued strings are evaluated one location at a
    #      time; a posting with no location at all passes, since many boards
    #      simply omit it and discarding those loses real roles.
    if location:
        ok, reason = _location_verdict(location, remote_flag, rules)
        if not ok:
            return False, reason

    # 5. Age. Postings with no or unparseable date pass through to Stage B.
    #    Early-career and campus programmes are exempt: they are posted months
    #    ahead and stay open, so the age rule silently deletes exactly the
    #    postings a recent graduate most wants.
    if rules.max_age_days is not None and not _first_match(
            title, rules.max_age_exempt_titles):
        posted = _parse_dt(posted_at)
        if posted is not None:
            age = now - posted
            if age > timedelta(days=rules.max_age_days):
                return False, f"max_age_days: {age.days}d old"

    # 6. Sponsorship. Its own rule, ahead of the general description
    #    blocklist, so the histogram reports its true count and its output can
    #    be audited alone — a false positive here is invisible and permanent.
    #
    #    An explicit positive declaration wins outright. Only unambiguous
    #    refusals are matched; anything nuanced is left to Stage B, which
    #    reads the whole posting rather than a phrase list.
    if not _first_match(description, rules.sponsorship_allow):
        hit = _first_match(description, rules.sponsorship_exclude)
        if hit:
            return False, f"sponsorship: {hit!r}"

    # 7. Years of experience. The only seniority call made from the
    #    description in Stage A; the nuanced version belongs in Stage B.
    if rules.max_years_experience is not None:
        required = minimum_years_required(description)
        if required is not None and required > rules.max_years_experience:
            return False, f"max_years_experience: {required}y required"

    # 8. Description blocklist — the clearance line in paragraph nine.
    hit = _first_match(description, rules.description_exclude)
    if hit:
        return False, f"description_exclude: {hit!r}"

    return True, "passed"
