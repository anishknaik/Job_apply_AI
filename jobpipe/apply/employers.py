"""Where he has worked, from the bullet bank's experience roles.

"Have you previously worked at {company}?" is a fact the profile can
answer, and the only honest sources for it are the roles in
`config/bullets.yaml` with `kind: experience` - the same list the resume
is built from. Projects are not employers. The bank is gitignored, so a
missing one means "unknown", and unknown resolves to a blocker rather than
to "No".
"""
from __future__ import annotations

import re
from pathlib import Path

from .. import bullets as _bullets

_STRIP = re.compile(r"[^a-z0-9]+")
# Trailing corporate furniture that a form's wording omits.
_SUFFIXES = ("inc", "incorporated", "llc", "ltd", "limited", "corp", "corporation",
             "co", "company", "plc", "gmbh")


def normalise(name: str) -> str:
    words = [w for w in _STRIP.sub(" ", (name or "").casefold()).split() if w]
    while words and words[-1] in _SUFFIXES:
        words.pop()
    return " ".join(words)


def from_bank(bank) -> tuple[str, ...]:
    """Company names of the bank's experience roles, as written there."""
    out = []
    for role in (bank.roles or {}).values():
        if (role.get("kind") or "").strip().lower() == "experience":
            company = str(role.get("company") or "").strip()
            if company:
                out.append(company)
    return tuple(dict.fromkeys(out))


def load(path: str | Path | None = None) -> tuple[str, ...] | None:
    """The employers, or None when the bank cannot be read. None is the
    answer "unknown", which the resolver turns into a blocker."""
    try:
        bank = _bullets.load_bank(path) if path else _bullets.load_bank()
    except Exception:                                        # noqa: BLE001
        return None
    return from_bank(bank)


def company_names(company: str | None) -> tuple[str, ...]:
    """The ways a form may name this company: the whole string, the part
    before a parenthesis, and each parenthesised part - so "Cursor
    (Anysphere)" is named by "Cursor" or by "Anysphere"."""
    text = (company or "").strip()
    if not text:
        return ()
    names = [text]
    head = re.sub(r"\s*\(.*$", "", text).strip()
    if head:
        names.append(head)
    names.extend(p.strip() for p in re.findall(r"\(([^)]+)\)", text) if p.strip())
    return tuple(dict.fromkeys(n for n in names if normalise(n)))


def label_names_company(label: str, company: str | None) -> bool:
    """Does the question name this employer? Whole words, case-insensitive,
    on the normalised forms, so "at Affirm?" matches and "affirmative" does
    not."""
    text = " " + normalise(label) + " "
    return any(f" {normalise(n)} " in text for n in company_names(company))


def is_past_employer(company: str | None, employers: tuple[str, ...] | list[str]) -> bool:
    """Whether this posting's company is one he has worked for. Either name
    may carry extra words ("Gradient Tensor" / "Gradient Tensor Inc"), so a
    whole-word containment either way counts; a bare substring does not."""
    mine = [normalise(e) for e in employers if normalise(e)]
    for name in company_names(company):
        n = normalise(name)
        for e in mine:
            if n == e or f" {e} " in f" {n} " or f" {n} " in f" {e} ":
                return True
    return False
