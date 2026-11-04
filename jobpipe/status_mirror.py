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

That leaves a cell nobody touched showing a status the database has moved
past. Anish decided (2026-09-10, §6.2) that sync closes that gap itself:
`write_back` puts the database's status into such a cell - compare-and-set
through `sheets.set_cells`, so a cell he edits in the seconds between the read
and the write keeps his edit. A cell he HAS edited is never written; his edit
flows the other way, into the database.

The india tab is mirrored too (Anish, 2026-11-04), with two differences. Its
readings are kept per tab, so neither tab's last value is read as the
other's. And a role on both tabs - one that passed Stage A on a US location
as well - is left to the main tab: `skip` names it, and the india tab's cell
for it neither writes the database nor gets written. One status, one cell.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import db, states


@dataclass
class Behind:
    """A cell nobody edited whose status the database has moved past."""
    posting_id: int
    url: str
    cell: str          # the raw text the cell held when it was read
    database: str      # the status id the database holds
    name: str          # "Company - Title", for the report

    def __str__(self) -> str:
        return f"{self.name}: sheet says {self.cell or 'nothing'}, " \
               f"database says {self.database}"


@dataclass
class MirrorResult:
    changed: int = 0
    unknown: list[str] = field(default_factory=list)
    backwards: list[str] = field(default_factory=list)
    behind: list[Behind] = field(default_factory=list)


def mirror(conn, rows_by_url: dict[str, str], tab: str = db.MAIN_TAB,
           skip=None, source: str = "sheet") -> MirrorResult:
    """Copy human edits on a tab's status column into status_events.

    `skip(posting_row)` returning True leaves that row to another tab.
    """
    result = MirrorResult()
    known = db.current_statuses(conn)
    last_read = db.sheet_readings(conn, tab)
    postings = {row["url"]: row for row in conn.execute(
        "SELECT id, url, company, title, location, stage_a_passed "
        "FROM postings").fetchall()}

    for url, raw in rows_by_url.items():
        row = postings.get(url)
        if row is None or (skip is not None and skip(row)):
            continue
        try:
            status = states.normalise(raw)
        except states.UnknownStatus:
            result.unknown.append(raw)
            continue

        posting_id = row["id"]
        previous = known.get(posting_id)
        before = last_read.get(posting_id)
        db.record_sheet_reading(conn, posting_id, status, tab)

        if before is not None:
            untouched = status == before
        else:
            # The first reading of this cell. A blank one is the resting
            # state, not an instruction to undo a status the database holds.
            untouched = (status == "not_applied"
                         and previous not in (None, "not_applied"))
        if untouched:
            if previous and previous != status:
                result.behind.append(Behind(
                    posting_id=posting_id, url=url, cell=raw,
                    database=previous,
                    name=f"{row['company']} - {row['title']}"))
            continue

        if previous and states.is_regression(previous, status):
            result.backwards.append(f"{raw} (was {previous})")
        if db.record_status(conn, posting_id, status, previous,
                            source=source):
            result.changed += 1

    conn.commit()
    return result


def write_back(conn, worksheet, behind: list[Behind],
               set_cells=None,
               tab: str = db.MAIN_TAB) -> tuple[int, list[Behind]]:
    """Bring untouched cells up to the database's status. Compare-and-set.

    Each write expects the cell still to hold what the mirror read. If Anish
    retyped it in between, `set_cells` skips it and his edit is picked up as
    an edit on the next sync. A successful write is remembered as this sync's
    reading, so the next sync sees the cell as unchanged, not as a human edit.
    Returns how many were written and which were left alone.
    """
    if set_cells is None:
        from .sheets import set_cells
    written, skipped = 0, []
    for row in behind:
        ok = set_cells(worksheet, row.url,
                       {"status": states.label(row.database)},
                       expect={"status": row.cell})
        if ok:
            db.record_sheet_reading(conn, row.posting_id, row.database, tab)
            written += 1
        else:
            skipped.append(row)
    conn.commit()
    return written, skipped
