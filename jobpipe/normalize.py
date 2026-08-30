"""The common Posting shape every fetcher returns, plus the derived fields.

Fetchers are responsible for one thing only: pulling their ATS's JSON and
handing back `Posting` objects. Everything about how a posting is compared,
deduped or filtered is decided here, so that logic lives in exactly one place.
"""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass


@dataclass
class Posting:
    ats_type: str
    source_id: str
    company: str
    title: str
    location: str | None
    remote_flag: bool
    description: str          # plain text, tags stripped
    url: str
    posted_at: str | None     # ISO-8601 string, or None if the board omits it

    @property
    def normalized_title(self) -> str:
        # Location is passed so a trailing "(Amsterdam)" can be recognised as
        # a place and dropped, while "(Software Engineer, GPU)" is kept.
        return normalize_title(self.title, self.location)

    @property
    def content_hash(self) -> str:
        return content_hash(self.company, self.normalized_title)


# ---------------------------------------------------------------------------
# HTML -> text
# ---------------------------------------------------------------------------

_SCRIPT_STYLE = re.compile(r"<(script|style)[^>]*>.*?</\1>",
                           re.IGNORECASE | re.DOTALL)
_BLOCK_END = re.compile(
    r"</(p|div|li|ul|ol|h[1-6]|tr|table|section|article)\s*>|<br\s*/?>",
    re.IGNORECASE,
)
_TAG = re.compile(r"<[^>]+>")
_WS_RUN = re.compile(r"[ \t\f\v]+")
_BLANK_RUN = re.compile(r"\n{3,}")


def html_to_text(raw: str | None) -> str:
    """Strip markup and unescape entities, preserving paragraph breaks.

    Greenhouse double-encodes its `content` field (`&lt;h2&gt;` rather than
    `<h2>`), so entities are unescaped first, then again after tag removal to
    catch anything that was encoded twice.
    """
    if not raw:
        return ""
    text = html.unescape(raw)
    text = _SCRIPT_STYLE.sub(" ", text)
    text = _BLOCK_END.sub("\n", text)
    text = _TAG.sub(" ", text)
    text = html.unescape(text)
    text = text.replace("\xa0", " ").replace("​", "")
    text = _WS_RUN.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    text = _BLANK_RUN.sub("\n\n", text)
    return text.strip()


# ---------------------------------------------------------------------------
# title normalization
# ---------------------------------------------------------------------------

# Order matters: parentheticals and bracketed suffixes go first so that the
# level patterns below aren't matching inside "(Senior, Remote)".
_PAREN_SUFFIX = re.compile(r"\s*[\(\[]([^\)\]]*)[\)\]]\s*$")

# A trailing parenthetical is dropped only when it is a level, an employment
# type, or a restatement of the posting's own location. Anything else is kept,
# because for several companies the parenthetical *is* the role:
#     "Member of Technical Staff (AI Inference Engineer)"
#     "Senior Solutions Architect (DS/ML/GenAI/LLM)"
# Stripping those collapsed 42 distinct Perplexity engineering roles into one.
_DISPOSABLE_PAREN = re.compile(
    r"^(remote|hybrid|on-?site|contract|full[- ]?time|part[- ]?time|"
    r"[ivx]{1,4}|l-?\d{1,2}|level\s*\d{1,2}|\d{1,2}|"
    r"us|usa|u\.s\.|united states|uk|emea|apac|amer|latam)$",
    re.IGNORECASE,
)
# Trailing ", New York" / " - Remote" / " | Data Platform" style suffixes.
_TRAILING_SEP = re.compile(r"\s*[-–—|,/]\s*(remote|hybrid|onsite|on-site|us|usa|"
                           r"united states|new york|nyc|ny|san francisco|sf|"
                           r"contract|full[- ]?time|part[- ]?time)\s*$",
                           re.IGNORECASE)
# Seniority is CANONICALISED, not stripped.
#
# The spec says to strip seniority prefixes, and that was the original
# behaviour. It is wrong in practice: "Senior Software Engineer" and "Software
# Engineer" are two different open reqs at the same company, not one req
# written two ways, and collapsing them deletes one of them permanently.
#
# The concrete failure: "Deployed Engineer (Early Career-NYC)", posted 9 days
# ago in New York, normalised to the same value as "Deployed Engineer (NYC)"
# from 68 days earlier, because `early career` was treated as a seniority
# band. The older row became canonical, was killed on posting age, and took
# the early-career role — the best-matching posting in the whole dataset for a
# recent grad — down with it.
#
# Stripping only helps repost detection when a company changes the level
# between listings, which is rare. Collapsing distinct reqs is invisible and
# permanent, while keeping them apart costs one extra scoring call. So only
# spelling variants of the same word are unified.
_ABBREVIATIONS = [
    (re.compile(r"\bsnr\.?\b", re.IGNORECASE), "senior"),
    (re.compile(r"\bsr\.?\b", re.IGNORECASE), "senior"),
    (re.compile(r"\bjr\.?\b", re.IGNORECASE), "junior"),
]
# Level suffixes: II, III, IV, L4, T5, E3, G7, or a bare trailing digit.
_LEVEL = re.compile(
    r"\b(i{1,3}v?|i?v|vi{0,3}|[lteg]-?\d{1,2}|level\s*\d{1,2}|\d)\b\s*$",
    re.IGNORECASE,
)
_PUNCT = re.compile(r"[^\w\s+#]")
_WS = re.compile(r"\s+")


def normalize_title(title: str, location: str | None = None) -> str:
    """Lowercase; strip seniority prefixes, level suffixes and place suffixes.

    "Sr. Machine Learning Engineer II (New York)" -> "machine learning engineer"
    "Member of Technical Staff (AI Inference Engineer)"
        -> "member of technical staff ai inference engineer"

    This is the key for repost detection, so two things have to hold at once:
    the same req listed twice must normalize identically, and two different
    reqs must not. `location` is used only to recognise a trailing
    parenthetical as a place worth dropping.
    """
    if not title:
        return ""
    t = title.strip()
    loc = (location or "").strip().lower()

    # Parentheticals can repeat: "Engineer (L4) (Remote)". Each is dropped
    # only if it is a level, an employment type, or this posting's own
    # location; otherwise it is left in place as part of the role identity.
    while True:
        match = _PAREN_SUFFIX.search(t)
        if not match:
            break
        content = match.group(1).strip()
        disposable = (
            not content
            or bool(_DISPOSABLE_PAREN.match(content))
            or (bool(loc) and content.lower() in loc)
        )
        if not disposable:
            break
        t = t[:match.start()].strip()

    while True:
        stripped = _TRAILING_SEP.sub("", t)
        if stripped == t:
            break
        t = stripped

    t = t.lower()
    for pattern, replacement in _ABBREVIATIONS:
        t = pattern.sub(replacement, t)
    t = _PUNCT.sub(" ", t)
    t = _WS.sub(" ", t).strip()

    # Level suffix is stripped after punctuation removal so "Engineer, L4"
    # and "Engineer L4" collapse to the same thing. Repeat for "Engineer II 2".
    while True:
        stripped = _WS.sub(" ", _LEVEL.sub("", t)).strip()
        if stripped == t or not stripped:
            break
        t = stripped

    return t


# ---------------------------------------------------------------------------
# repost detection
# ---------------------------------------------------------------------------

def content_hash(company: str, normalized_title: str) -> str:
    """sha256(company + normalized_title). Deliberately excludes location.

    Both Greenhouse and Ashby return one row per location for a multi-location
    req: a single "Technology Enablement Analyst" opening came back five
    times — New York, Remote, San Francisco, Chicago, Dallas — each with its
    own source_id.

    With location in the key those five rows hash differently, survive dedupe,
    get scored five times and land in the sheet five times. Worse, the row the
    pipeline keeps might be the San Francisco one, so a req genuinely open in
    New York is filtered out on location while its New York twin sits marked
    as a duplicate and skipped.

    Excluding location collapses them into one row whose locations are merged
    (see db.merge_posting_locations), so the location filter sees every city
    the req is actually open in.
    """
    payload = f"{company.strip().lower()}|{normalized_title}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def split_locations(location: str | None) -> list[str]:
    """Split a possibly multi-valued location string into its parts."""
    if not location:
        return []
    return [part.strip() for part in re.split(r"[;|]", location) if part.strip()]


def merge_locations(existing: str | None, incoming: str | None) -> str:
    """Union two location strings, preserving first-seen order."""
    merged: list[str] = []
    seen: set[str] = set()
    for part in split_locations(existing) + split_locations(incoming):
        key = _WS.sub(" ", part.lower())
        if key not in seen:
            seen.add(key)
            merged.append(part)
    return "; ".join(merged)


# ---------------------------------------------------------------------------
# remote detection
# ---------------------------------------------------------------------------

_REMOTE_HINT = re.compile(r"\bremote\b|\bwork from home\b|\bdistributed\b|"
                          r"\banywhere\b", re.IGNORECASE)


def looks_remote(*fields: str | None) -> bool:
    """Fallback for boards that expose no explicit remote flag."""
    return any(_REMOTE_HINT.search(f) for f in fields if f)
