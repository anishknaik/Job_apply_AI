"""Mirror the sheet's status column into the event log - edits only.

Status has two writers. Anish edits the sheet's status column, which the
pipeline never writes; and `advance_status` appends to SQLite from a session
(PHASE5_PLAN §6.2). The mirror used to compare every cell with the database's
CURRENT status and record any difference as a sheet edit, so an untouched cell
overrode anything newer. LangChain was recorded applied through
advance_status and reverted to not_applied thirteen seconds later, by the very
cell that write had superseded (2026-09-10).

The rule now: only a CHANGE on the sheet is an edit. Each sync remembers what
it read (`sheet_status`), and a cell that still says what it said last time
is no signal, whatever the database has done since. The latest action wins,
in either channel.

That leaves the sheet able to show a status the database has moved past. The
column is not written to close the gap - it stays the human's, and §6.2 makes
changing that its own decision - so the gap is reported instead.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import db, states


@dataclass
class MirrorResult:
    changed: int = 0
    unknown: list[str] = field(default_factory=list)
    backwards: list[str] = field(default_factory=list)
    # Rows whose cell nobody edited but whose status moved on in the
    # database. Reported, never written back.
    behind: list[str] = field(default_factory=list)


def mirror(conn, rows_by_url: dict[str, str]) -> MirrorResult:
    """Copy human edits on the sheet's status column into status_events."""
    result = MirrorResult()
    known = db.current_statuses(conn)
    last_read = db.sheet_readings(conn)
    postings = {row["url"]: row for row in conn.execute(
        "SELECT id, url, company, title FROM postings").fetchall()}

    for url, raw in rows_by_url.items():
        row = postings.get(url)
        if row is None:
            continue
        try:
            status = states.normalise(raw)
        except states.UnknownStatus:
            result.unknown.append(raw)
            continue

        posting_id = row["id"]
        previous = known.get(posting_id)
        before = last_read.get(posting_id)
        db.record_sheet_reading(conn, posting_id, status)

        if before is not None:
            untouched = status == before
        else:
            # The first reading of this cell. A blank one is the resting
            # state, not an instruction to undo a status the database holds.
            untouched = (status == "not_applied"
                         and previous not in (None, "not_applied"))
        if untouched:
            if previous and previous != status:
                result.behind.append(
                    f"{row['company']} - {row['title']}: sheet says "
                    f"{status}, database says {previous}")
            continue

        if previous and states.is_regression(previous, status):
            result.backwards.append(f"{raw} (was {previous})")
        if db.record_status(conn, posting_id, status, previous):
            result.changed += 1

    conn.commit()
    return result
