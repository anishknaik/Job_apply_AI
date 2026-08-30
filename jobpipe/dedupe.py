"""Two-layer deduplication.

Layer 1 — UNIQUE (ats_type, source_id), enforced by the schema. Catches the
ordinary case of re-running the pipeline against an unchanged board. Handled
inside db.upsert_posting, which touches last_seen_at and reports is_new=False.

Layer 2 — content_hash = sha256(company + normalized_title + location).
Catches reposts: companies close and relist the same req constantly, with a
brand-new source_id each time. Without this you re-score the same job every
three weeks and pay for it every time.
"""
from __future__ import annotations

import logging

from . import db

log = logging.getLogger(__name__)

REPOST_WINDOW_DAYS = 60


def resolve_repost(conn, posting_id: int, content_hash: str,
                   location: str | None = None,
                   window_days: int = REPOST_WINDOW_DAYS) -> bool:
    """Mark `posting_id` as a duplicate if the same req was already seen.

    Returns True if it was a duplicate. Duplicates skip Stage A and Stage B
    entirely; any existing score is carried forward so the role still surfaces
    in the sheet without being re-billed.

    Because content_hash no longer includes location, this covers two cases at
    once: the same req relisted weeks later, and the same req returned N times
    in a single run — once per location. In the second case the duplicate's
    location is merged into the canonical row before it is set aside, so no
    city is lost.
    """
    prior = db.find_prior_content_hash(conn, content_hash, posting_id,
                                       within_days=window_days)
    if prior is None:
        return False

    merged = db.merge_posting_locations(conn, prior["id"], location)
    carried = db.copy_score(conn, prior["id"], posting_id)

    notes = []
    if merged:
        notes.append("location merged into canonical row")
    if carried:
        notes.append("score carried forward")
    suffix = ("; " + "; ".join(notes)) if notes else ""

    db.set_stage_a(
        conn, posting_id, passed=False,
        reason=(f"duplicate: matches posting {prior['id']} first seen "
                f"{prior['first_seen_at']}{suffix}"),
    )
    log.debug("  duplicate: posting %s matches %s", posting_id, prior["id"])
    return True
