"""Roles in India, for a tab of their own.

Stage A excludes India on purpose - STEM OPT requires US employment - and
that verdict stays as it is: nothing here reads or writes `stage_a_passed`,
and `filters.yaml` is untouched. This is a second, read-only view over
postings the daily fetch already holds, because the same 143 boards list
their India roles alongside the US ones.

Every Stage A rule except location still applies, so the tab holds the same
kind of role the main sheet does. `cli score --india` scores them - they are
not in the default scoring queue, which is Stage A's survivors - and a scored
India role stays out of the main tab (`lane_only`), so the two lists never
compete for the same 25 rows.
"""
from __future__ import annotations

import dataclasses

from . import db, filters

# Matched per location token with word boundaries, like every Stage A list -
# `india` must not fire inside "Indianapolis". Cities are listed because many
# boards give the city alone ("Gurugram", "Bengaluru - BLR1", "IN-Pune").
LOCATIONS = [
    "india", "bengaluru", "bangalore", "hyderabad", "mumbai", "pune",
    "gurugram", "gurgaon", "chennai", "noida", "delhi", "kolkata",
    "ahmedabad",
]

# `last_seen` is what says a row is still open - rows are never deleted, so a
# closed role stays on the page with the date it was last listed. The score
# columns come after the human ones because the tab existed before they did,
# and the sync only ever appends a column.
COLUMNS = [
    "date_found", "company", "role", "location", "last_seen", "url",
    # human-owned, as on the main tab
    "status", "applied_date", "notes",
    # blank until `cli score --india` has run
    "score", "fit", "reasoning",
]

# The tab's floor, on `apply_score` like the main tab's - Anish's call,
# 2026-10-08: below 6 he will not apply. A role with no score yet is not
# below it; it shows blank until `cli score --india` has run.
MIN_SCORE = 6

_SELECT = """
    SELECT p.id, p.first_seen_at, p.last_seen_at, p.company, p.title,
           p.location, p.remote_flag, p.description, p.url, p.posted_at,
           p.stage_a_passed, s.overall, s.reasoning,
           COALESCE(s.apply_score, s.overall) AS apply_score
    FROM postings p
    LEFT JOIN scores s ON s.posting_id = p.id
"""


def is_india(location: str | None) -> bool:
    """True if any single location the posting names is in India."""
    return any(filters._first_match(token, LOCATIONS)
               for token in filters._location_tokens((location or "").lower()))


def lane_only(row) -> bool:
    """True for a role that belongs to the india tab and not the main one.

    A role open in the US as well as India passed Stage A on its US location
    and stays on the main tab; it appears on both.
    """
    return row["stage_a_passed"] != 1 and is_india(row["location"])


def select(conn, rules: filters.FilterRules, max_stale_days: int = 7,
           tier_by_company: dict[str, int] | None = None,
           min_score: int | None = None) -> list:
    """Live India postings that pass every Stage A rule except location.

    Ordered by score where there is one, then company tier, then newest.
    `min_score` leaves out a role scored below it and keeps an unscored one,
    so the scoring queue, which is the unscored ones, is the same either way.
    """
    rules = dataclasses.replace(rules, location_allow=[], location_exclude=[])
    rows = conn.execute(
        f"{_SELECT} WHERE p.last_seen_at >= ?",
        (db.utc_days_ago(max_stale_days),)).fetchall()
    kept = [row for row in rows
            if is_india(row["location"]) and filters.evaluate(row, rules)[0]]
    if min_score is not None:
        kept = [row for row in kept if row["apply_score"] is None
                or row["apply_score"] >= min_score]
    tiers = tier_by_company or {}
    kept.sort(key=lambda row: row["first_seen_at"], reverse=True)
    kept.sort(key=lambda row: tiers.get(row["company"], 9))
    kept.sort(key=lambda row: -1 if row["apply_score"] is None
              else row["apply_score"], reverse=True)
    return kept


def unscored(conn, rules: filters.FilterRules, limit: int | None = None) -> list:
    """The India scoring queue: live roles in `select` with no score yet."""
    ids = [row["id"] for row in select(conn, rules)
           if row["apply_score"] is None]
    if limit is not None:
        ids = ids[:limit]
    # Full posting rows, the shape the scoring loop takes from get_unscored.
    return [db.get_posting(conn, posting_id) for posting_id in ids]


def rows_by_url(conn, urls) -> list:
    """Current rows for urls already in the tab, whatever their state now."""
    urls = [u for u in dict.fromkeys(urls) if u]
    out = []
    for start in range(0, len(urls), 500):      # stay under SQLite's var cap
        chunk = urls[start:start + 500]
        placeholders = ",".join("?" * len(chunk))
        out.extend(conn.execute(
            f"{_SELECT} WHERE p.url IN ({placeholders})", chunk).fetchall())
    return out


def build_row(posting) -> dict:
    """One tab row from one posting."""
    return {
        "date_found": str(posting["first_seen_at"])[:10],
        "company": posting["company"],
        "role": posting["title"],
        "location": posting["location"] or "",
        "last_seen": str(posting["last_seen_at"])[:10],
        "url": posting["url"],
        "score": ("" if posting["apply_score"] is None
                  else posting["apply_score"]),
        "fit": "" if posting["overall"] is None else posting["overall"],
        "reasoning": posting["reasoning"] or "",
    }
